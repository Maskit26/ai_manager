from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.jobs.outbox import enqueue_outbound_message
from ai_seller_core.persistence.models import Agent, ChatMessage, DialogSession, DomainEvent
from ai_seller_core.persistence.session_lease import (
    SessionLease,
    acquire_session_lease,
    assert_current_fence,
)


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    project_id: str
    release_id: str
    lease: SessionLease


@dataclass(frozen=True)
class MessageInput:
    role: str
    content: dict[str, Any]
    external_message_id: str | None = None


@dataclass(frozen=True)
class OutboundIntent:
    payload: dict[str, Any]
    idempotency_key: str
    correlation_id: str
    deadline_at: datetime | None = None


@dataclass(frozen=True)
class TurnResult:
    inbound_message: ChatMessage | None
    outbound_messages: tuple[ChatMessage, ...]
    outbox_ids: tuple[str, ...]
    duplicate: bool = False


def _record_domain_event(
    db: AsyncSession,
    *,
    project_id: str,
    session_id: str,
    event_type: str,
    correlation_id: str | None,
    payload: dict[str, Any],
    occurred_at: datetime,
) -> None:
    db.add(
        DomainEvent(
            project_id=project_id,
            session_id=session_id,
            event_type=event_type,
            aggregate_type="dialog_session",
            aggregate_id=session_id,
            tenant_ids={"project_id": project_id},
            occurred_at=occurred_at,
            schema_version=1,
            correlation_id=correlation_id or str(uuid4()),
            payload=payload,
        )
    )


async def open_or_resume_session(
    db: AsyncSession,
    *,
    project_id: str,
    channel_id: str,
    customer_id: str,
    external_chat_id: str,
    worker_id: str,
    lease_seconds: int,
    correlation_id: str | None = None,
) -> SessionContext | None:
    """Find/create the conversation and acquire its session lease in the caller's txn.

    The caller must pass the release selected for a new session. Existing sessions
    always retain their original release, including when a closed session is reopened.
    """
    if not db.in_transaction():
        raise RuntimeError("open_or_resume_session requires an active transaction")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    lookup = (
        select(DialogSession)
        .where(
            DialogSession.project_id == project_id,
            DialogSession.channel_id == channel_id,
            DialogSession.external_chat_id == external_chat_id,
        )
        .with_for_update()
    )
    dialog = await db.scalar(lookup)

    now = datetime.now(UTC)
    created = False
    if dialog is None:
        agent = await db.scalar(
            select(Agent).where(Agent.project_id == project_id).with_for_update()
        )
        if agent is None:
            raise LookupError(f"No agent exists for project {project_id!r}")
        if agent.status != "active" or agent.active_release_id is None:
            raise RuntimeError("Cannot create a dialog session without an active agent release")

        new_session_id = str(uuid4())
        create = (
            insert(DialogSession)
            .values(
                id=new_session_id,
                project_id=project_id,
                channel_id=channel_id,
                customer_id=customer_id,
                external_chat_id=external_chat_id,
                release_id=agent.active_release_id,
                state="active",
                variables={},
                last_activity_at=now,
                lease_owner=None,
                lease_until=None,
                lease_fence=0,
                reopen_count=0,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "project_id",
                    "channel_id",
                    "external_chat_id",
                ]
            )
            .returning(DialogSession.id)
        )
        created_id = await db.scalar(create)
        created = created_id is not None
        dialog = await db.scalar(
            select(DialogSession)
            .where(
                DialogSession.id == (created_id or new_session_id),
            )
            .with_for_update()
        )
        if dialog is None and created_id is None:
            dialog = await db.scalar(lookup)
        if dialog is None:
            raise RuntimeError("Session creation conflicted but no session was found")

    if dialog.lease_until is not None and dialog.lease_until > now:
        return None

    reopen_event_payload: dict[str, Any] | None = None
    if dialog.state == "closed":
        reopen_event_payload = {
            "previous_closed_at": dialog.closed_at.isoformat() if dialog.closed_at else None,
            "previous_close_reason": dialog.close_reason,
            "previous_closed_by": dialog.closed_by,
            "reopen_count": dialog.reopen_count + 1,
        }
        dialog.state = "active"
        dialog.reopen_count += 1
        dialog.version += 1
        dialog.last_activity_at = now
        dialog.closed_at = None
        dialog.close_reason = None
        dialog.closed_by = None

    lease = await acquire_session_lease(
        db,
        project_id=project_id,
        session_id=dialog.id,
        owner=worker_id,
        lease_seconds=lease_seconds,
    )
    if lease is None:
        return None

    if created:
        _record_domain_event(
            db,
            project_id=dialog.project_id,
            session_id=dialog.id,
            event_type="dialog.session_opened",
            correlation_id=correlation_id,
            payload={"release_id": dialog.release_id},
            occurred_at=now,
        )
    elif reopen_event_payload is not None:
        _record_domain_event(
            db,
            project_id=dialog.project_id,
            session_id=dialog.id,
            event_type="dialog.session_reopened",
            correlation_id=correlation_id,
            payload=reopen_event_payload,
            occurred_at=now,
        )

    await db.flush()
    return SessionContext(
        session_id=dialog.id,
        project_id=dialog.project_id,
        release_id=dialog.release_id,
        lease=lease,
    )


async def close_session(
    db: AsyncSession,
    *,
    lease: SessionLease,
    reason: str,
    closed_by: str,
    correlation_id: str | None = None,
) -> None:
    """Close a session only while the caller still owns its current lease."""
    if reason not in {"max_chat_closed", "idle_ttl", "manual"}:
        raise ValueError(f"Unsupported session close reason: {reason!r}")
    if not closed_by:
        raise ValueError("closed_by must not be empty")
    if not db.in_transaction():
        raise RuntimeError("close_session requires an active transaction")

    await assert_current_fence(db, lease=lease)
    dialog = await db.scalar(
        select(DialogSession)
        .where(
            DialogSession.id == lease.session_id,
            DialogSession.project_id == lease.project_id,
        )
        .with_for_update()
    )
    if dialog is None:
        raise LookupError(f"Dialog session {lease.session_id!r} does not exist")
    if dialog.state == "closed":
        return

    closed_at = datetime.now(UTC)
    dialog.state = "closed"
    dialog.closed_at = closed_at
    dialog.close_reason = reason
    dialog.closed_by = closed_by
    dialog.lease_owner = None
    dialog.lease_until = None
    dialog.version += 1
    _record_domain_event(
        db,
        project_id=dialog.project_id,
        session_id=dialog.id,
        event_type="dialog.session_closed",
        correlation_id=correlation_id,
        payload={
            "closed_at": closed_at.isoformat(),
            "close_reason": reason,
            "closed_by": closed_by,
        },
        occurred_at=closed_at,
    )
    await db.flush()


async def commit_turn(
    db: AsyncSession,
    *,
    lease: SessionLease,
    inbound: MessageInput | None,
    outbound: tuple[MessageInput, ...],
    outbox: tuple[OutboundIntent, ...],
    correlation_id: str | None = None,
) -> TurnResult:
    """Atomically persist one turn, its delivery intents, and durable delivery jobs."""
    if not db.in_transaction():
        raise RuntimeError("commit_turn requires an active transaction")
    if len(outbound) != len(outbox):
        raise ValueError("Each outbound message must have exactly one outbox intent")

    await assert_current_fence(db, lease=lease)
    dialog = await db.scalar(
        select(DialogSession)
        .where(
            DialogSession.id == lease.session_id,
            DialogSession.project_id == lease.project_id,
        )
        .with_for_update()
    )
    if dialog is None:
        raise LookupError(f"Dialog session {lease.session_id!r} does not exist")
    if dialog.state != "active":
        raise RuntimeError("Cannot commit a turn to a closed dialog session")

    inbound_message: ChatMessage | None = None
    if inbound is not None:
        values = {
            "id": str(uuid4()),
            "project_id": dialog.project_id,
            "session_id": dialog.id,
            "channel_id": dialog.channel_id,
            "role": inbound.role,
            "content": inbound.content,
            "external_message_id": inbound.external_message_id,
            "delivery_status": "received",
        }
        statement = insert(ChatMessage).values(**values)
        if inbound.external_message_id is not None:
            statement = statement.on_conflict_do_nothing(constraint="uq_chat_messages_external")
        inserted_id = await db.scalar(statement.returning(ChatMessage.id))
        if inserted_id is None:
            existing = await db.scalar(
                select(ChatMessage).where(
                    ChatMessage.project_id == dialog.project_id,
                    ChatMessage.channel_id == dialog.channel_id,
                    ChatMessage.external_message_id == inbound.external_message_id,
                )
            )
            dialog.lease_owner = None
            dialog.lease_until = None
            return TurnResult(existing, (), (), duplicate=True)
        inbound_message = await db.get(ChatMessage, inserted_id)

    out_messages: list[ChatMessage] = []
    outbox_ids: list[str] = []
    for message_input, intent in zip(outbound, outbox, strict=True):
        message = ChatMessage(
            id=str(uuid4()),
            project_id=dialog.project_id,
            session_id=dialog.id,
            channel_id=dialog.channel_id,
            role=message_input.role,
            content=message_input.content,
            external_message_id=message_input.external_message_id,
            delivery_status="pending",
        )
        db.add(message)
        out_messages.append(message)

        outbox_message = await enqueue_outbound_message(
            db,
            project_id=dialog.project_id,
            channel_id=dialog.channel_id,
            session_id=dialog.id,
            payload=intent.payload,
            idempotency_key=intent.idempotency_key,
            correlation_id=intent.correlation_id,
            deadline_at=intent.deadline_at,
            lease=lease,
        )
        outbox_ids.append(outbox_message.id)

    committed_at = datetime.now(UTC)
    dialog.last_activity_at = committed_at
    dialog.version += 1
    dialog.lease_owner = None
    dialog.lease_until = None
    _record_domain_event(
        db,
        project_id=dialog.project_id,
        session_id=dialog.id,
        event_type="dialog.turn_committed",
        correlation_id=(correlation_id or (outbox[0].correlation_id if outbox else None)),
        payload={
            "sequence": dialog.version,
            "inbound_message_id": inbound_message.id if inbound_message else None,
            "outbound_message_ids": [message.id for message in out_messages],
            "outbox_ids": outbox_ids,
        },
        occurred_at=committed_at,
    )
    await db.flush()
    return TurnResult(
        inbound_message=inbound_message,
        outbound_messages=tuple(out_messages),
        outbox_ids=tuple(outbox_ids),
    )
