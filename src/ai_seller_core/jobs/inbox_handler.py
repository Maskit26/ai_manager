import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.dialog.runtime import (
    DialogRuntime,
    InboundMessage,
    MessageSanitizer,
)
from ai_seller_core.dialog.session_service import (
    MessageInput,
    OutboundIntent,
    SessionContext,
    commit_turn,
    open_or_resume_session,
)
from ai_seller_core.persistence.base import SessionFactory
from ai_seller_core.persistence.models import ChatMessage, InboxEvent
from ai_seller_core.persistence.session_lease import (
    release_session_lease,
    renew_session_lease,
)


@dataclass(frozen=True)
class InboundCommand:
    project_id: str
    channel_id: str
    customer_id: str
    external_chat_id: str
    text: str
    external_message_id: str | None


class EventDecoder(Protocol):
    """Decode canonical event metadata with buyer text in the top-level ``text`` key."""

    def decode(self, event: InboxEvent) -> InboundCommand: ...


ReleaseResolver = Callable[[AsyncSession, str], Awaitable[str | None]]
RuntimeFactory = Callable[[str, str], DialogRuntime]


def build_inbound_event_handler(
    *,
    worker_id: str,
    decoder: EventDecoder,
    sanitizer: MessageSanitizer,
    resolve_release_id: ReleaseResolver,
    runtime_for_project: RuntimeFactory,
    lease_seconds: int,
    history_limit: int = 20,
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """Build the dialog job handler from application-specific dependencies.

    The release resolver must lock/read the project's agent in this transaction;
    no release ID is trusted from an incoming provider payload.
    """
    if not worker_id:
        raise ValueError("worker_id must not be empty")
    if lease_seconds < 3:
        raise ValueError("lease_seconds must be at least 3 seconds")
    if history_limit < 0:
        raise ValueError("history_limit must not be negative")

    async def process(payload: dict[str, Any]) -> None:
        inbox_event_id = str(payload["inbox_event_id"])

        async with SessionFactory() as db, db.begin():
            event = await db.scalar(
                select(InboxEvent)
                .where(InboxEvent.id == inbox_event_id)
                .with_for_update()
            )
            if event is None:
                raise LookupError(f"Inbox event {inbox_event_id!r} does not exist")
            if event.status in {"processed", "ignored", "failed"}:
                return

            command = decoder.decode(event)
            correlation_id = event.correlation_id
            sanitized_text = sanitizer.sanitize(command.text)
            if sanitized_text is None:
                event.normalized_event = {}
                event.status = "failed"
                return
            event.normalized_event = {
                **event.normalized_event,
                "text": sanitized_text,
            }

            release_id = await resolve_release_id(db, command.project_id)
            if release_id is None:
                event.status = "ignored"
                return

            event.status = "processing"
            context: SessionContext | None = await open_or_resume_session(
                db,
                project_id=command.project_id,
                channel_id=command.channel_id,
                customer_id=command.customer_id,
                external_chat_id=command.external_chat_id,
                release_id=release_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if context is None:
                raise RuntimeError("Dialog session is currently leased by another worker")
            history = await _load_history(
                db,
                session_id=context.session_id,
                limit=history_limit,
            )

        try:
            runtime = runtime_for_project(context.project_id, context.release_id)
            response_text = await _respond_with_heartbeat(
                runtime=runtime,
                command=command,
                context=context,
                text=sanitized_text,
                history=history,
                correlation_id=correlation_id,
                lease_seconds=lease_seconds,
            )
        except asyncio.CancelledError:
            async with SessionFactory() as db, db.begin():
                await release_session_lease(db, lease=context.lease)
            raise
        except Exception:
            async with SessionFactory() as db, db.begin():
                await release_session_lease(db, lease=context.lease)
            raise

        async with SessionFactory() as db, db.begin():
            event = await db.get(InboxEvent, inbox_event_id)
            if event is None:
                raise LookupError(f"Inbox event {inbox_event_id!r} does not exist")

            inbound = MessageInput(
                role="user",
                content={"text": sanitized_text},
                external_message_id=command.external_message_id,
            )
            outbound = (
                MessageInput(role="assistant", content={"text": response_text}),
            )
            outbox = (
                OutboundIntent(
                    payload={"text": response_text},
                    idempotency_key=f"inbox-reply:{inbox_event_id}",
                    correlation_id=event.correlation_id,
                ),
            )
            await commit_turn(
                db,
                lease=context.lease,
                inbound=inbound,
                outbound=outbound,
                outbox=outbox,
            )
            event.status = "processed"

    return process


async def _load_history(
    db: AsyncSession,
    *,
    session_id: str,
    limit: int,
) -> tuple[dict[str, object], ...]:
    if limit == 0:
        return ()
    rows = (
        await db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(limit)
        )
    ).all()
    return tuple(
        {
            "role": message.role,
            "content": message.content.get("text", ""),
        }
        for message in reversed(rows)
        if message.role in {"user", "assistant"}
    )


async def _respond_with_heartbeat(
    *,
    runtime: DialogRuntime,
    command: InboundCommand,
    context: SessionContext,
    text: str,
    history: tuple[dict[str, object], ...],
    correlation_id: str,
    lease_seconds: int,
) -> str:
    task = asyncio.create_task(
        runtime.respond(
            InboundMessage(
                project_id=command.project_id,
                session_id=context.session_id,
                text=text,
                correlation_id=correlation_id,
                history=history,
            )
        )
    )
    interval = max(1.0, lease_seconds / 3)
    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except TimeoutError:
                async with SessionFactory() as db, db.begin():
                    renewed = await renew_session_lease(
                        db,
                        lease=context.lease,
                        lease_seconds=lease_seconds,
                    )
                if renewed is None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise RuntimeError(
                        "Session lease was fenced during dialog generation"
                    ) from None
    except asyncio.CancelledError:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
