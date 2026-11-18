import asyncio
import io
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import delete, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.dialog.session_service import close_session, open_or_resume_session
from ai_seller_core.persistence.models import (
    Account,
    AccountMembership,
    BackgroundJob,
    Channel,
    ChatMessage,
    DialogSession,
    InboxEvent,
    OutboxMessage,
    Project,
)
from ai_seller_core.persistence.session_lease import acquire_session_lease, assert_current_fence

ROOT = Path(__file__).resolve().parents[1]


def alembic_config(output: io.StringIO | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_accounts_projects_offline_migration() -> None:
    output = io.StringIO()
    command.upgrade(alembic_config(output), "head", sql=True)
    sql = output.getvalue()

    assert "CREATE TABLE accounts" in sql
    assert "CREATE TABLE account_memberships" in sql
    assert "CREATE TABLE projects" in sql
    assert "CREATE TABLE channels" in sql
    assert "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE RESTRICT" in sql
    assert "uq_account_memberships_account_user" in sql
    assert "ix_projects_account_id" in sql
    assert "fk_dialog_sessions_project_channel" in sql
    assert "fk_inbox_project_channel" in sql
    assert "fk_outbox_project_channel" in sql
    assert "DO $$ BEGIN IF EXISTS (SELECT 1 FROM dialog_sessions" in sql
    for table in ("background_jobs", "inbox", "outbox", "chat_messages"):
        assert f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE project_id IS NULL)" in sql


def test_accounts_projects_postgresql_migrations() -> None:
    test_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        database_url = Settings().database_url.get_secret_value()
    except ValidationError as error:
        if any(
            item["loc"] == ("database_url",) and item["type"] == "missing"
            for item in error.errors()
        ):
            pytest.skip("Configure DATABASE_URL for PostgreSQL tests")
        raise
    finally:
        if test_database_url is not None:
            os.environ["DATABASE_URL"] = test_database_url

    async def verify() -> None:
        engine = create_async_engine(database_url)
        schema = f"test_accounts_{uuid4().hex}"
        schema_created = False
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def upgrade(sync_connection) -> None:
                    config = alembic_config()
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
                assert await connection.scalar(text("SELECT count(*) FROM alembic_version")) == 1
                assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "20261118_channels"
                )

                project = {
                    "id": "project-1",
                    "account_id": "missing",
                    "locale": "ru",
                    "timezone": "UTC",
                    "settings": {},
                    "status": "active",
                }
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(insert(Project), project)

                await connection.execute(
                    insert(Account),
                    {"id": "account-1", "external_id": "external-1", "status": "active"},
                )
                await connection.execute(insert(Project), {**project, "account_id": "account-1"})
                await connection.execute(
                    insert(Account),
                    {"id": "account-2", "external_id": "external-2", "status": "active"},
                )
                await connection.execute(
                    insert(Project),
                    {**project, "id": "project-2", "account_id": "account-2"},
                )
                await connection.execute(
                    insert(Channel),
                    [
                        {"project_id": "project-1", "channel_id": "channel-1"},
                        {"project_id": "project-1", "channel_id": "project-1-only-channel"},
                        {"project_id": "project-2", "channel_id": "channel-1"},
                    ],
                )
                await connection.execute(
                    insert(AccountMembership),
                    {
                        "id": "member-1",
                        "account_id": "account-1",
                        "user_id": "user-1",
                        "role": "owner",
                    },
                )
                session_row = {
                    "id": "session-1",
                    "project_id": "project-1",
                    "channel_id": "channel-1",
                    "customer_id": "customer-1",
                    "external_chat_id": "chat-1",
                    "release_id": "release-1",
                    "state": "active",
                    "variables": {},
                    "lease_fence": 0,
                    "reopen_count": 0,
                }
                await connection.execute(insert(DialogSession), session_row)
                await connection.execute(
                    insert(ChatMessage),
                    {
                        "id": "message-1",
                        "project_id": "project-1",
                        "session_id": "session-1",
                        "role": "user",
                        "content": {"text": "test"},
                        "delivery_status": "received",
                    },
                )

                for statement, values in (
                    (
                        insert(DialogSession),
                        {
                            **session_row,
                            "id": "session-cross-project-channel",
                            "project_id": "project-2",
                            "channel_id": "project-1-only-channel",
                        },
                    ),
                    (
                        insert(ChatMessage),
                        {
                            "id": "message-cross-project",
                            "project_id": "project-2",
                            "session_id": "session-1",
                            "role": "user",
                            "content": {"text": "test"},
                            "delivery_status": "received",
                        },
                    ),
                    (
                        insert(OutboxMessage),
                        {
                            "id": "outbox-cross-project",
                            "project_id": "project-2",
                            "channel_id": "channel-1",
                            "session_id": "session-1",
                            "idempotency_key": "cross-project",
                            "payload": {},
                        },
                    ),
                    (
                        insert(InboxEvent),
                        {
                            "id": "inbox-cross-project-channel",
                            "project_id": "project-2",
                            "channel_id": "project-1-only-channel",
                            "provider_event_id": "event-cross-project",
                            "event_type": "message",
                            "normalized_event": {},
                            "correlation_id": "correlation-1",
                        },
                    ),
                    (
                        insert(InboxEvent),
                        {
                            "id": "inbox-missing-project",
                            "project_id": "missing",
                            "channel_id": "channel-1",
                            "provider_event_id": "event-1",
                            "event_type": "message",
                            "normalized_event": {},
                            "correlation_id": "correlation-1",
                        },
                    ),
                    (
                        insert(BackgroundJob),
                        {
                            "id": "job-missing-project",
                            "project_id": "missing",
                            "queue": "inbound_dialog",
                            "kind": "process_inbound_event",
                            "payload": {},
                            "idempotency_key": "job-missing",
                            "correlation_id": "correlation-1",
                        },
                    ),
                ):
                    with pytest.raises(IntegrityError):
                        async with connection.begin_nested():
                            await connection.execute(statement, values)

                async with AsyncSession(
                    bind=connection,
                    join_transaction_mode="create_savepoint",
                    expire_on_commit=False,
                ) as db:
                    with pytest.raises(LookupError):
                        await acquire_session_lease(
                            db,
                            project_id="project-2",
                            session_id="session-1",
                            owner="wrong-project-worker",
                            lease_seconds=60,
                        )

                    context = await open_or_resume_session(
                        db,
                        project_id="project-2",
                        channel_id="channel-1",
                        customer_id="customer-1",
                        external_chat_id="chat-1",
                        release_id="release-1",
                        worker_id="project-2-worker",
                        lease_seconds=60,
                    )
                    assert context is not None
                    assert context.project_id == "project-2"
                    assert context.session_id != "session-1"

                    with pytest.raises(RuntimeError):
                        await assert_current_fence(
                            db,
                            lease=replace(context.lease, project_id="project-1"),
                        )
                    with pytest.raises(RuntimeError):
                        await close_session(
                            db,
                            lease=replace(context.lease, session_id="session-1"),
                            reason="manual",
                            closed_by="cross-project-test",
                        )
                    assert await db.scalar(
                        select(DialogSession.state).where(DialogSession.id == "session-1")
                    ) == "active"

                for statement, values in (
                    (
                        insert(Account),
                        {"id": "account-2", "external_id": "external-1", "status": "active"},
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-2",
                            "account_id": "account-1",
                            "user_id": "user-1",
                            "role": "admin",
                        },
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-3",
                            "account_id": "account-1",
                            "user_id": "user-2",
                            "role": "invalid",
                        },
                    ),
                    (
                        insert(AccountMembership),
                        {
                            "id": "member-4",
                            "account_id": "missing",
                            "user_id": "user-2",
                            "role": "operator",
                        },
                    ),
                    (delete(Account).where(Account.id == "account-1"), {}),
                ):
                    with pytest.raises(IntegrityError):
                        async with connection.begin_nested():
                            await connection.execute(statement, values)
                assert await connection.scalar(select(Project.id)) == "project-1"
        finally:
            try:
                if schema_created:
                    async with engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                await engine.dispose()

    asyncio.run(verify())
