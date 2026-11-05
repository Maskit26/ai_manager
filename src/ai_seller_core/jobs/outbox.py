from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.jobs.repository import enqueue_job
from ai_seller_core.persistence.models import OutboxMessage


async def enqueue_outbound_message(
    session: AsyncSession,
    *,
    channel_id: str,
    session_id: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
    deadline_at: datetime | None = None,
) -> OutboxMessage:
    """Write delivery intent and its durable job in the caller's transaction."""
    message = OutboxMessage(
        channel_id=channel_id,
        session_id=session_id,
        payload=payload,
        idempotency_key=idempotency_key,
        deadline_at=deadline_at,
    )
    session.add(message)
    await session.flush()
    await enqueue_job(
        session,
        queue="outbound_delivery",
        kind="deliver_outbox_message",
        payload={"outbox_id": message.id},
        idempotency_key=f"outbox:{idempotency_key}",
        correlation_id=correlation_id,
    )
    return message
