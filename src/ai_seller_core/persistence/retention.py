from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.catalog_models import CatalogImportError
from ai_seller_core.persistence.models import (
    AuditLog,
    DomainEvent,
    ProjectDeletionRequest,
)

RETENTION_LOCK_KEY = 0x41494D47524554


async def purge_expired_activity(session: AsyncSession) -> tuple[int, int, int]:
    lock_acquired = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
        {"lock_key": RETENTION_LOCK_KEY},
    )
    if not lock_acquired:
        return 0, 0, 0

    cutoff = (
        await session.execute(text("SELECT CURRENT_TIMESTAMP - INTERVAL '30 days'"))
    ).scalar_one()
    active_project_deletions = select(ProjectDeletionRequest.project_id).where(
        ProjectDeletionRequest.status == "pending"
    )
    events_result = await session.execute(
        delete(DomainEvent)
        .where(
            DomainEvent.occurred_at < cutoff,
            DomainEvent.project_id.not_in(active_project_deletions),
        )
        .execution_options(synchronize_session=False)
    )
    audit_result = await session.execute(
        delete(AuditLog)
        .where(
            AuditLog.created_at < cutoff,
            or_(
                AuditLog.project_id.is_(None),
                AuditLog.project_id.not_in(active_project_deletions),
            ),
        )
        .execution_options(synchronize_session=False)
    )
    import_errors_result = await session.execute(
        delete(CatalogImportError)
        .where(
            CatalogImportError.created_at < cutoff,
            CatalogImportError.project_id.not_in(active_project_deletions),
        )
        .execution_options(synchronize_session=False)
    )
    return (
        events_result.rowcount or 0,
        audit_result.rowcount or 0,
        import_errors_result.rowcount or 0,
    )
