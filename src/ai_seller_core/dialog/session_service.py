from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.jobs.outbox import enqueue_outbound_message
from ai_seller_core.persistence.models import ChatMessage, DialogSession
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


async def open_or_resume_session(
    db: AsyncSession,
    *,
    project_id: str,
    channel_id: str,
    customer_id: str,
    external_chat_id: str,
    release_id: str,
    worker_id: str,
    lease_seconds: int,
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
    if dialog is None:
        new_session_id = str(uuid4())
        create = (
            insert(DialogSession)
            .values(
                id=new_session_id,
                project_id=project_id,
                channel_id=channel_id,
                customer_id=customer_id,
                external_chat_id=external_chat_id,
                release_id=release_id,
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

    if dialog.state == "closed":
        dialog.state = "active"
        dialog.reopen_count += 1
        dialog.closed_at = None
        dialog.close_reason = None
        dialog.closed_by = None

    dialog.last_activity_at = now
    lease = await acquire_session_lease(
        db,
        session_id=dialog.id,
        owner=worker_id,
        lease_seconds=lease_seconds,
    )
    if lease is None:
        return None

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
        .where(DialogSession.id == lease.session_id)
        .with_for_update()
    )
    if dialog is None:
        raise LookupError(f"Dialog session {lease.session_id!r} does not exist")
    if dialog.state == "closed":
        return

    dialog.state = "closed"
    dialog.closed_at = datetime.now(UTC)
    dialog.close_reason = reason
    dialog.closed_by = closed_by
    dialog.lease_owner = None
    dialog.lease_until = None


async def commit_turn(
    db: AsyncSession,
    *,
    lease: SessionLease,
    inbound: MessageInput | None,
    outbound: tuple[MessageInput, ...],
    outbox: tuple[OutboundIntent, ...],
) -> TurnResult:
    """Atomically persist one turn, its delivery intents, and durable delivery jobs."""
    if not db.in_transaction():
        raise RuntimeError("commit_turn requires an active transaction")
    if len(outbound) != len(outbox):
        raise ValueError("Each outbound message must have exactly one outbox intent")

    await assert_current_fence(db, lease=lease)
    dialog = await db.scalar(
        select(DialogSession)
        .where(DialogSession.id == lease.session_id)
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
            "session_id": dialog.id,
            "role": inbound.role,
            "content": inbound.content,
            "external_message_id": inbound.external_message_id,
            "delivery_status": "received",
        }
        statement = insert(ChatMessage).values(**values)
        if inbound.external_message_id is not None:
            statement = statement.on_conflict_do_nothing(
                constraint="uq_chat_messages_external"
            )
        inserted_id = await db.scalar(statement.returning(ChatMessage.id))
        if inserted_id is None:
            existing = await db.scalar(
                select(ChatMessage).where(
                    ChatMessage.session_id == dialog.id,
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
            session_id=dialog.id,
            role=message_input.role,
            content=message_input.content,
            external_message_id=message_input.external_message_id,
            delivery_status="pending",
        )
        db.add(message)
        out_messages.append(message)

        outbox_message = await enqueue_outbound_message(
            db,
            channel_id=dialog.channel_id,
            session_id=dialog.id,
            payload=intent.payload,
            idempotency_key=intent.idempotency_key,
            correlation_id=intent.correlation_id,
            deadline_at=intent.deadline_at,
        )
        outbox_ids.append(outbox_message.id)

    dialog.last_activity_at = datetime.now(UTC)
    dialog.lease_owner = None
    dialog.lease_until = None
    await db.flush()
    return TurnResult(
        inbound_message=inbound_message,
        outbound_messages=tuple(out_messages),
        outbox_ids=tuple(outbox_ids),
    )
