from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import BackgroundJob


async def claim_job(
    session: AsyncSession,
    *,
    queue: str,
    worker_id: str,
    lease_seconds: int,
) -> BackgroundJob | None:
    now = datetime.now(UTC)
    statement = (
        select(BackgroundJob)
        .where(
            BackgroundJob.queue == queue,
            BackgroundJob.available_at <= now,
            or_(
                BackgroundJob.status.in_(("pending", "retrying")),
                (BackgroundJob.status == "processing") & (BackgroundJob.lease_until < now),
            ),
        )
        .order_by(BackgroundJob.available_at, BackgroundJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = await session.scalar(statement)
    if job is None:
        return None

    job.status = "processing"
    job.lease_owner = worker_id
    job.lease_until = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    await session.flush()
    return job


async def enqueue_job(
    session: AsyncSession,
    *,
    queue: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    correlation_id: str,
) -> BackgroundJob:
    job = BackgroundJob(
        queue=queue,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
    )
    session.add(job)
    await session.flush()
    return job
