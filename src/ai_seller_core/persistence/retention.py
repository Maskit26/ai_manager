from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import AuditLog, DomainEvent

RETENTION_LOCK_KEY = 0x41494D47524554


async def purge_expired_activity(session: AsyncSession) -> tuple[int, int]:
    lock_acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
        {"lock_key": RETENTION_LOCK_KEY},
    )
    if not lock_acquired:
        return 0, 0

    cutoff = (
        await session.execute(text("SELECT CURRENT_TIMESTAMP - INTERVAL '30 days'"))
    ).scalar_one()
    events_result = await session.execute(
        delete(DomainEvent)
        .where(DomainEvent.occurred_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    audit_result = await session.execute(
        delete(AuditLog)
        .where(AuditLog.created_at < cutoff)
        .execution_options(synchronize_session=False)
    )
    return events_result.rowcount or 0, audit_result.rowcount or 0
