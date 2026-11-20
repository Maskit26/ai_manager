from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import quote

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import AuditLog, Project, ProjectDeletionRequest

PROJECT_DELETION_RETENTION = timedelta(days=30)
PROJECT_VECTOR_OBJECT_TYPES: Sequence[str] = ("kb_chunk", "product")


class ProjectNotFoundError(LookupError):
    pass


class ProjectVectorCleanupError(RuntimeError):
    pass


class ProjectVectorStore(Protocol):
    async def delete_project_points(self, project_id: str) -> None: ...


class QdrantProjectVectorStore:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        qdrant_url: str,
        collection_name: str,
        api_key: str | None = None,
    ) -> None:
        self.client = client
        self.base_url = qdrant_url.rstrip("/")
        self.collection_name = quote(collection_name, safe="")
        self.headers = {"api-key": api_key} if api_key else {}

    async def delete_project_points(self, project_id: str) -> None:
        url = (
            f"{self.base_url}/collections/{self.collection_name}/points/delete"
            "?wait=true"
        )
        for object_type in PROJECT_VECTOR_OBJECT_TYPES:
            response = await self.client.post(
                url,
                headers=self.headers,
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "project_id",
                                "match": {"value": project_id},
                            },
                            {
                                "key": "object_type",
                                "match": {"value": object_type},
                            },
                        ]
                    }
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise ProjectVectorCleanupError(
                    "Qdrant rejected project point deletion"
                ) from error
            try:
                status = response.json()["result"]["status"]
            except (KeyError, TypeError, ValueError) as error:
                raise ProjectVectorCleanupError(
                    "Qdrant returned an invalid point deletion response"
                ) from error
            if status != "completed":
                raise ProjectVectorCleanupError(
                    "Qdrant did not confirm project point deletion"
                )


async def archive_project(
    session: AsyncSession,
    project_id: str,
    *,
    requested_at: datetime | None = None,
    actor_id: str | None = None,
) -> ProjectDeletionRequest:
    requested_at = requested_at or datetime.now(UTC)
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    if project is None:
        raise ProjectNotFoundError(f"Project {project_id!r} does not exist")

    request = await session.get(ProjectDeletionRequest, project_id)
    if request is not None and request.status == "pending":
        return request

    previous_status = project.status
    project.status = "archived"
    if request is None:
        request = ProjectDeletionRequest(
            project_id=project_id,
            previous_status=previous_status,
            status="pending",
            requested_at=requested_at,
        )
        session.add(request)
    else:
        request.previous_status = previous_status
        request.status = "pending"
        request.requested_at = requested_at
        request.completed_at = None
    session.add(
        AuditLog(
            account_id=project.account_id,
            project_id=project_id,
            actor_id=actor_id,
            action="project.deletion_requested",
            entity_type="project",
            entity_id=project_id,
            diff={"from_status": previous_status, "to_status": "archived"},
            created_at=requested_at,
        )
    )
    return request


async def restore_project(
    session: AsyncSession,
    project_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    project = await session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
    )
    request = await session.get(ProjectDeletionRequest, project_id)
    if (
        project is None
        or request is None
        or request.status != "pending"
        or project.status != "archived"
        or request.requested_at + PROJECT_DELETION_RETENTION <= now
    ):
        return False
    previous_status = project.status
    project.status = request.previous_status
    request.status = "cancelled"
    request.completed_at = now
    session.add(
        AuditLog(
            account_id=project.account_id,
            project_id=project_id,
            action="project.restored",
            entity_type="project",
            entity_id=project_id,
            diff={"from_status": previous_status, "to_status": project.status},
            created_at=now,
        )
    )
    return True


async def purge_due_project_deletions(
    session: AsyncSession,
    vector_store: ProjectVectorStore | None,
    *,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - PROJECT_DELETION_RETENTION
    requests = (
        await session.scalars(
            select(ProjectDeletionRequest)
            .where(
                ProjectDeletionRequest.status == "pending",
                ProjectDeletionRequest.requested_at <= cutoff,
            )
            .order_by(ProjectDeletionRequest.requested_at)
            .with_for_update(skip_locked=True)
        )
    ).all()

    purged = 0
    for request in requests:
        project_status = await session.scalar(
            select(Project.status)
            .where(Project.id == request.project_id)
            .with_for_update()
        )
        if project_status is not None and project_status != "archived":
            raise RuntimeError("A pending project deletion requires an archived project")
        if vector_store is None:
            raise ProjectVectorCleanupError("Qdrant is not configured")
        await vector_store.delete_project_points(request.project_id)
        if not await _retention_elapsed(session, request.project_id, cutoff):
            continue
        await _delete_project_rows(session, request.project_id)
        request.status = "completed"
        request.completed_at = now
        purged += 1
    return purged


async def _retention_elapsed(
    session: AsyncSession,
    project_id: str,
    cutoff: datetime,
) -> bool:
    timestamps = (
        ("domain_events", "occurred_at"),
        ("audit_log", "created_at"),
        ("llm_calls", "created_at"),
        ("usage_counters", "updated_at"),
    )
    for table, column in timestamps:
        has_recent_rows = await session.scalar(
            text(
                f'SELECT EXISTS (SELECT 1 FROM "{table}" '
                f'WHERE project_id = :project_id AND "{column}" >= :cutoff)'
            ),
            {"project_id": project_id, "cutoff": cutoff},
        )
        if has_recent_rows:
            return False
    return True


async def _delete_project_rows(session: AsyncSession, project_id: str) -> None:
    # Delete dependent rows before their RESTRICT-referenced parents.
    delete_tables = (
        "domain_events",
        "audit_log",
        "llm_calls",
        "usage_counters",
        "tool_executions",
        "escalations",
        "outbox",
        "inbox",
        "lead_items",
        "leads",
        "chat_messages",
        "dialog_sessions",
    )
    params = {"project_id": project_id}
    for table in delete_tables:
        await session.execute(
            text(f'DELETE FROM "{table}" WHERE project_id = :project_id'),
            params,
        )

    await session.execute(
        text("UPDATE agents SET active_release_id = NULL WHERE project_id = :project_id"),
        params,
    )
    for table in (
        "agent_drafts",
        "agent_releases",
        "agents",
        "products",
        "catalog_import_errors",
        "catalog_imports",
        "catalog_sources",
        "customer_identities",
        "customers",
        "kb_chunks",
        "kb_documents",
        "channels",
        "background_jobs",
    ):
        await session.execute(
            text(f'DELETE FROM "{table}" WHERE project_id = :project_id'),
            params,
        )
    await _assert_no_project_rows(session, project_id)
    await session.execute(
        text("DELETE FROM projects WHERE id = :project_id"),
        params,
    )


async def _assert_no_project_rows(session: AsyncSession, project_id: str) -> None:
    tables = (
        await session.execute(
            text(
                "SELECT DISTINCT table_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND column_name = 'project_id' "
                "AND table_name <> 'project_deletion_requests'"
            )
        )
    ).scalars()
    for table in tables:
        quoted_table = '"' + table.replace('"', '""') + '"'
        remaining = await session.scalar(
            text(f"SELECT count(*) FROM {quoted_table} WHERE project_id = :project_id"),
            {"project_id": project_id},
        )
        if remaining:
            raise RuntimeError(f"Project cleanup left rows in {table}")
