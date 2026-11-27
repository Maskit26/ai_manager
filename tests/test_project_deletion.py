import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence import project_deletion, retention
from ai_seller_core.persistence.models import (
    Account,
    AgentSlot,
    AuditLog,
    Channel,
    Project,
    ProjectDeletionRequest,
    User,
)
from ai_seller_core.persistence.project_deletion import (
    PROJECT_VECTOR_OBJECT_TYPES,
    QdrantProjectVectorStore,
    archive_project,
    purge_due_project_deletions,
    restore_project,
)
from ai_seller_core.persistence.retention import purge_expired_activity

PROJECT_SCOPED_TABLES = (
    "agent_drafts",
    "agent_releases",
    "agent_slots",
    "agents",
    "audit_log",
    "background_jobs",
    "catalog_import_errors",
    "catalog_imports",
    "catalog_sources",
    "channels",
    "chat_messages",
    "customer_identities",
    "customers",
    "dialog_sessions",
    "domain_events",
    "escalations",
    "inbox",
    "kb_chunks",
    "kb_documents",
    "lead_items",
    "leads",
    "llm_calls",
    "outbox",
    "products",
    "tool_executions",
    "usage_counters",
)


def test_retention_elapsed_checks_all_project_data_tables() -> None:
    class RecordingSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def scalar(self, statement: object, _params: object) -> bool:
            self.statements.append(str(statement))
            return False

    session = RecordingSession()
    assert asyncio.run(
        project_deletion._retention_elapsed(
            session,
            "project-delete",
            datetime.now(UTC) - timedelta(days=30),
        )
    )

    checked_tables = {
        statement.split('FROM "', 1)[1].split('"', 1)[0] for statement in session.statements
    }
    assert checked_tables == {*PROJECT_SCOPED_TABLES, "projects"}


class MockQdrant:
    def __init__(self, *, failure_status: int | None = None) -> None:
        self.failure_status = failure_status
        self.points = [
            {"id": "chunk-1", "project_id": "project-delete", "object_type": "kb_chunk"},
            {"id": "product-1", "project_id": "project-delete", "object_type": "product"},
            {"id": "other-project", "project_id": "other", "object_type": "kb_chunk"},
        ]
        self.filters: list[dict[str, str]] = []

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            conditions = body["filter"]["must"]
            values = {condition["key"]: condition["match"]["value"] for condition in conditions}
            assert set(values) == {"project_id", "object_type"}
            self.filters.append(values)
            if self.failure_status is not None:
                return httpx.Response(self.failure_status)
            self.points = [
                point
                for point in self.points
                if point["project_id"] != values["project_id"]
                or point["object_type"] != values["object_type"]
            ]
            return httpx.Response(200, json={"result": {"status": "completed"}})

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def test_qdrant_deletion_filters_by_project_and_object_type() -> None:
    async def verify() -> None:
        qdrant = MockQdrant()
        async with qdrant.client() as client:
            store = QdrantProjectVectorStore(
                client,
                qdrant_url="https://qdrant.example.test",
                collection_name="test-vectors",
                api_key="test-key",
            )
            await store.delete_project_points("project-delete")

        assert {item["object_type"] for item in qdrant.filters} == set(PROJECT_VECTOR_OBJECT_TYPES)
        assert all(item["project_id"] == "project-delete" for item in qdrant.filters)
        assert {point["id"] for point in qdrant.points} == {"other-project"}

    asyncio.run(verify())


def test_project_deletion_postgresql_is_retryable_and_removes_orphans(monkeypatch) -> None:
    monkeypatch.setattr(
        project_deletion,
        "get_settings",
        lambda: SimpleNamespace(project_deletion_retention_days=30),
    )
    monkeypatch.setattr(
        retention,
        "get_settings",
        lambda: SimpleNamespace(activity_retention_days=30),
    )
    configured_test_url = os.environ.pop("DATABASE_URL", None)
    try:
        try:
            database_url = Settings().database_url.get_secret_value()
        except ValidationError as error:
            pytest.skip(f"PostgreSQL integration configuration is unavailable: {error}")
    finally:
        if configured_test_url is not None:
            os.environ["DATABASE_URL"] = configured_test_url
    if "change-me" in database_url:
        pytest.skip("PostgreSQL integration configuration is a placeholder")

    async def verify() -> None:
        schema = f"project_delete_test_{uuid4().hex}"
        engine = create_async_engine(
            database_url,
            pool_size=1,
            max_overflow=0,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                config = Config("alembic.ini")

                def upgrade(sync_connection) -> None:
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)

            project_id = "project-delete"
            async with AsyncSession(engine, expire_on_commit=False) as session:
                async with session.begin():
                    account = Account(status="active")
                    session.add(account)
                    await session.flush()
                    project = Project(
                        id=project_id,
                        account_id=account.id,
                        locale="en",
                        timezone="UTC",
                        settings={"private": "retained until purge"},
                        status="active",
                    )
                    session.add(project)
                    await session.flush()
                    session.add(AgentSlot(project_id=project_id, slot_limit=1, used_slots=1))
                    await session.flush()
                    session.add(Channel(project_id=project_id, channel_id="web"))
                    await session.flush()
                    session.add(User(id="tenant-admin"))
                    await session.flush()

                    now = datetime.now(UTC)
                    await archive_project(
                        session,
                        project_id,
                        requested_at=now - timedelta(days=32),
                        actor_id="tenant-admin",
                    )
                    assert await restore_project(
                        session,
                        project_id,
                        now=now - timedelta(days=31),
                        actor_id="tenant-admin",
                    )
                    request = await archive_project(
                        session,
                        project_id,
                        requested_at=now,
                        actor_id="tenant-admin",
                    )
                    assert request.status == "pending"
                    assert not await restore_project(
                        session,
                        project_id,
                        now=now + timedelta(days=31),
                    )
                    assert project.status == "archived"

                not_due_qdrant = MockQdrant()
                async with not_due_qdrant.client() as client:
                    store = QdrantProjectVectorStore(
                        client,
                        qdrant_url="https://qdrant.example.test",
                        collection_name="test-vectors",
                    )
                    async with session.begin():
                        await purge_expired_activity(session)
                        assert (
                            await purge_due_project_deletions(
                                session,
                                store,
                                now=now + timedelta(days=29),
                            )
                            == 0
                        )
                assert not_due_qdrant.filters == []

                failed_qdrant = MockQdrant(failure_status=503)
                async with failed_qdrant.client() as client:
                    store = QdrantProjectVectorStore(
                        client,
                        qdrant_url="https://qdrant.example.test",
                        collection_name="test-vectors",
                    )
                    async with session.begin():
                        assert (
                            await purge_due_project_deletions(
                                session,
                                store,
                                now=now + timedelta(days=31),
                            )
                            == 0
                        )

                assert (
                    await session.scalar(select(Project.id).where(Project.id == project_id))
                    == project_id
                )
                assert (
                    await session.scalar(
                        select(ProjectDeletionRequest.status).where(
                            ProjectDeletionRequest.project_id == project_id
                        )
                    )
                    == "pending"
                )
                assert (
                    await session.scalar(
                        select(Channel.project_id).where(Channel.project_id == project_id)
                    )
                    == project_id
                )
                assert (
                    await session.scalar(
                        select(AuditLog.id).where(
                            AuditLog.project_id == project_id,
                            AuditLog.action == "project.deletion_requested",
                        )
                    )
                    is not None
                )
                await session.rollback()

                qdrant = MockQdrant()
                async with qdrant.client() as client:
                    store = QdrantProjectVectorStore(
                        client,
                        qdrant_url="https://qdrant.example.test",
                        collection_name="test-vectors",
                    )
                    delete_rows = project_deletion._delete_project_rows

                    async def delete_rows_then_fail(
                        db: AsyncSession,
                        target_project_id: str,
                    ) -> None:
                        await delete_rows(db, target_project_id)
                        raise RuntimeError("simulated failure after SQL deletion")

                    with monkeypatch.context() as scoped_monkeypatch:
                        scoped_monkeypatch.setattr(
                            project_deletion,
                            "_delete_project_rows",
                            delete_rows_then_fail,
                        )
                        async with session.begin():
                            assert (
                                await purge_due_project_deletions(
                                    session,
                                    store,
                                    now=now + timedelta(days=31),
                                )
                                == 0
                            )

                    assert (
                        await session.scalar(select(Project.id).where(Project.id == project_id))
                        == project_id
                    )
                    assert (
                        await session.scalar(
                            select(ProjectDeletionRequest.status).where(
                                ProjectDeletionRequest.project_id == project_id
                            )
                        )
                        == "pending"
                    )
                    await session.rollback()

                    activity_filters_before = len(qdrant.filters)
                    async with session.begin():
                        channel = await session.get(Channel, (project_id, "web"))
                        assert channel is not None
                        previous_updated_at = channel.updated_at
                        channel.channel_id = "web-updated"
                        await session.flush()
                        updated_at = await session.scalar(
                            select(Channel.updated_at).where(
                                Channel.project_id == project_id,
                                Channel.channel_id == "web-updated",
                            )
                        )
                        assert updated_at > previous_updated_at
                        channel.updated_at = now + timedelta(days=30)
                        await session.flush()
                        assert (
                            await purge_due_project_deletions(
                                session,
                                store,
                                now=now + timedelta(days=31),
                            )
                            == 0
                        )
                    assert len(qdrant.filters) == activity_filters_before

                    async with session.begin():
                        assert (
                            await purge_due_project_deletions(
                                session,
                                store,
                                now=now + timedelta(days=61),
                            )
                            == 1
                        )

                assert (
                    await session.scalar(select(Project.id).where(Project.id == project_id)) is None
                )
                assert (
                    await session.scalar(
                        select(ProjectDeletionRequest.status).where(
                            ProjectDeletionRequest.project_id == project_id
                        )
                    )
                    == "completed"
                )
                assert (
                    await session.scalar(
                        select(AuditLog.id).where(AuditLog.project_id == project_id)
                    )
                    is None
                )
                for table in PROJECT_SCOPED_TABLES:
                    count = await session.scalar(
                        text(f'SELECT count(*) FROM "{table}" WHERE project_id = :id'),
                        {"id": project_id},
                    )
                    assert count == 0, f"orphaned rows remain in {table}"
                assert {point["id"] for point in qdrant.points} == {"other-project"}
        finally:
            async with engine.begin() as connection:
                await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await engine.dispose()

    asyncio.run(verify())
