import asyncio
import logging
from contextlib import AsyncExitStack

import httpx

from ai_seller_core.core.config import get_settings
from ai_seller_core.persistence.base import SessionFactory, database_lifespan
from ai_seller_core.persistence.project_deletion import (
    ProjectVectorStore,
    QdrantProjectVectorStore,
    purge_due_project_deletions,
)
from ai_seller_core.persistence.retention import purge_expired_activity

logger = logging.getLogger(__name__)


async def run_once() -> tuple[int, tuple[int, int, int]]:
    settings = get_settings()
    async with database_lifespan(settings):
        async with AsyncExitStack() as stack:
            vector_store: ProjectVectorStore | None = None
            if settings.qdrant_url is not None:
                client = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=10.0)
                )
                vector_store = QdrantProjectVectorStore(
                    client,
                    qdrant_url=str(settings.qdrant_url),
                    collection_name=settings.qdrant_collection_name,
                    api_key=(
                        settings.qdrant_api_key.get_secret_value()
                        if settings.qdrant_api_key is not None
                        else None
                    ),
                )
            async with SessionFactory() as session, session.begin():
                purged_projects = await purge_due_project_deletions(
                    session,
                    vector_store,
                )
                expired_activity = await purge_expired_activity(session)
    logger.info(
        "Project deletion maintenance completed",
        extra={
            "projects_purged": purged_projects,
            "domain_events_deleted": expired_activity[0],
            "audit_entries_deleted": expired_activity[1],
            "catalog_import_errors_deleted": expired_activity[2],
        },
    )
    return purged_projects, expired_activity


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_once())


if __name__ == "__main__":
    main()
