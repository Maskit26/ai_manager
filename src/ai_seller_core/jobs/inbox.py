from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.jobs.repository import enqueue_job
from ai_seller_core.persistence.models import InboxEvent


@dataclass(frozen=True)
class IngestResult:
    event_id: str | None
    duplicate: bool


async def ingest_channel_event(
    session: AsyncSession,
    *,
    channel_id: str,
    provider_event_id: str,
    event_type: str,
    normalized_event: dict[str, Any],
    correlation_id: str,
) -> IngestResult:
    """Persist the normalized event and its job atomically before webhook ACK."""
    statement = (
        insert(InboxEvent)
        .values(
            channel_id=channel_id,
            provider_event_id=provider_event_id,
            event_type=event_type,
            normalized_event=normalized_event,
            correlation_id=correlation_id,
        )
        .on_conflict_do_nothing(constraint="uq_inbox_provider_event")
        .returning(InboxEvent.id)
    )
    event_id = await session.scalar(statement)
    if event_id is None:
        return IngestResult(event_id=None, duplicate=True)

    if event_type in {"message", "callback"}:
        await enqueue_job(
            session,
            queue="inbound_dialog",
            kind="process_inbound_event",
            payload={"inbox_event_id": event_id},
            idempotency_key=f"inbox:{event_id}",
            correlation_id=correlation_id,
        )
    return IngestResult(event_id=event_id, duplicate=False)
