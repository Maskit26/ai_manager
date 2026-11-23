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
    AccountEntitlement,
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
    assert "CREATE TABLE entitlements" in sql
    assert "CREATE TABLE projects" in sql
    assert "CREATE TABLE channels" in sql
    assert "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE RESTRICT" in sql
    assert "uq_account_memberships_account_user" in sql
    assert "uq_entitlements_account_id" in sql
    assert "ck_entitlements_max_projects_nonnegative" in sql
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
                    "20261125_account_entitlements"
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
                await connection.execute(
                    insert(AccountEntitlement),
                    {"id": "entitlement-1", "account_id": "account-1"},
                )
                await connection.execute(
                    insert(AccountEntitlement),
                    {
                        "id": "entitlement-2",
                        "account_id": "account-2",
                        "max_catalog_sources_per_project": None,
                    },
                )
                entitlement = await connection.execute(
                    select(
                        AccountEntitlement.catalog_import_enabled,
                        AccountEntitlement.knowledge_base_enabled,
                        AccountEntitlement.multi_channel_enabled,
                        AccountEntitlement.max_projects,
                        AccountEntitlement.max_catalog_sources_per_project,
                    ).where(
                        AccountEntitlement.account_id == "account-1"
                    )
                )
                entitlement = entitlement.one()
                assert entitlement.catalog_import_enabled is True
                assert entitlement.knowledge_base_enabled is True
                assert entitlement.multi_channel_enabled is True
                assert entitlement.max_projects is None
                assert entitlement.max_catalog_sources_per_project == 1
                unlimited_catalog_sources = await connection.scalar(
                    select(AccountEntitlement.max_catalog_sources_per_project).where(
                        AccountEntitlement.account_id == "account-2"
                    )
                )
                assert unlimited_catalog_sources is None
                await connection.execute(
                    text(
                        "INSERT INTO agents (id, project_id, status) VALUES "
                        "('agent-1', 'project-1', 'draft'), "
                        "('agent-2', 'project-2', 'draft')"
                    )
                )
                await connection.execute(
                    text(
                        """INSERT INTO agent_releases
                        (id, project_id, agent_id, version, snapshot) VALUES
                        ('release-1', 'project-1', 'agent-1', 1,
                         '{"prompt": {}, "tone": {}, "model_settings": {},
                           "sales_rules": {}, "functions": {},
                           "response_settings": {}}'::jsonb),
                        ('release-2', 'project-2', 'agent-2', 1,
                         '{"prompt": {}, "tone": {}, "model_settings": {},
                           "sales_rules": {}, "functions": {},
                           "response_settings": {}}'::jsonb)"""
                    )
                )
                await connection.execute(
                    text(
                        "UPDATE agents SET status = 'active', active_release_id = "
                        "CASE id WHEN 'agent-1' THEN 'release-1' ELSE 'release-2' END"
                    )
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
                        "channel_id": "channel-1",
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
                            "channel_id": "project-1-only-channel",
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
                        worker_id="project-2-worker",
                        lease_seconds=60,
                    )
                    assert context is not None
                    assert context.project_id == "project-2"
                    assert context.session_id != "session-1"
                    assert context.release_id == "release-2"

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
                        insert(AccountEntitlement),
                        {
                            "id": "entitlement-duplicate-account",
                            "account_id": "account-1",
                        },
                    ),
                    (
                        insert(AccountEntitlement),
                        {
                            "id": "entitlement-negative-projects",
                            "account_id": "account-2",
                            "max_projects": -1,
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


def test_agent_release_lifecycle_postgresql() -> None:
    import asyncio
    import os
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import pytest
    from alembic import command
    from pydantic import ValidationError
    from sqlalchemy import func, select, text, update
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from ai_seller_core.core.config import Settings
    from ai_seller_core.dialog.session_service import (
        MessageInput,
        OutboundIntent,
        close_session,
        commit_turn,
        open_or_resume_session,
    )
    from ai_seller_core.persistence.agent_releases import (
        create_agent,
        publish_agent_release,
        rollback_agent_release,
        save_agent_draft,
    )
    from ai_seller_core.persistence.models import (
        AgentRelease,
        ChatMessage,
        DialogSession,
        DomainEvent,
        OutboxMessage,
    )
    from ai_seller_core.persistence.session_lease import acquire_session_lease

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

    def config_for(version: int) -> dict[str, object]:
        return {
            "prompt": {"text": f"prompt-{version}"},
            "tone": {"style": "clear"},
            "model_settings": {"temperature": 0.1 * version},
            "sales_rules": {"version": version},
            "functions": {"enabled": ["lookup"]},
            "response_settings": {"format": "plain"},
        }

    async def verify() -> None:
        engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
        schema = f"test_agent_releases_{uuid4().hex}"
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

                await connection.run_sync(upgrade)
                await connection.execute(
                    text(
                        "INSERT INTO accounts (id, external_id, status) "
                        "VALUES ('agent-test-account', 'agent-test-external', 'active')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO projects "
                        "(id, account_id, locale, timezone, settings, status) "
                        "VALUES ('agent-test-project', 'agent-test-account', "
                        "'en-US', 'UTC', '{}'::jsonb, 'active')"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO channels (project_id, channel_id) "
                        "VALUES ('agent-test-project', 'agent-test-channel')"
                    )
                )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await create_agent(db, project_id="agent-test-project")
                    with pytest.raises(ValueError, match="excluded field"):
                        await save_agent_draft(
                            db,
                            project_id="agent-test-project",
                            config={
                                **config_for(1),
                                "model_settings": {
                                    "temperature": 0.1,
                                    "api_key": "test-placeholder",
                                },
                            },
                        )
                    await save_agent_draft(
                        db,
                        project_id="agent-test-project",
                        config=config_for(1),
                    )
                    release_1 = await publish_agent_release(
                        db,
                        project_id="agent-test-project",
                    )
                    release_1_id = release_1.id

            barrier = asyncio.Barrier(2)

            async def publish_next_release() -> str:
                async with AsyncSession(engine) as db:
                    async with db.begin():
                        await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                        await barrier.wait()
                        await save_agent_draft(
                            db,
                            project_id="agent-test-project",
                            config=config_for(2),
                        )
                        release = await publish_agent_release(
                            db,
                            project_id="agent-test-project",
                        )
                        return release.id

            async def create_concurrent_session() -> tuple[str, str]:
                async with AsyncSession(engine) as db:
                    async with db.begin():
                        await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                        await barrier.wait()
                        context = await open_or_resume_session(
                            db,
                            project_id="agent-test-project",
                            channel_id="agent-test-channel",
                            customer_id="agent-test-customer",
                            external_chat_id="agent-test-chat",
                            worker_id="agent-test-worker",
                            lease_seconds=60,
                        )
                        assert context is not None
                        return context.session_id, context.release_id

            release_2_id, (session_id, pinned_release_id) = await asyncio.gather(
                publish_next_release(),
                create_concurrent_session(),
            )
            assert pinned_release_id in {release_1_id, release_2_id}

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    pinned_release = await db.scalar(
                        select(AgentRelease).where(AgentRelease.id == pinned_release_id)
                    )
                    assert pinned_release is not None
                    assert pinned_release.snapshot in (
                        config_for(1),
                        config_for(2),
                    )
                    with pytest.raises(DBAPIError):
                        async with db.begin_nested():
                            await db.execute(
                                update(AgentRelease)
                                .where(AgentRelease.id == pinned_release_id)
                                .values(snapshot=config_for(99))
                            )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await rollback_agent_release(
                        db,
                        project_id="agent-test-project",
                        release_id=release_1_id,
                    )
                    new_context = await open_or_resume_session(
                        db,
                        project_id="agent-test-project",
                        channel_id="agent-test-channel",
                        customer_id="agent-test-customer",
                        external_chat_id="agent-test-chat-after-rollback",
                        worker_id="agent-test-worker",
                        lease_seconds=60,
                    )
                    assert new_context is not None
                    assert new_context.release_id == release_1_id
                    existing_release_id = await db.scalar(
                        select(DialogSession.release_id).where(DialogSession.id == session_id)
                    )
                    assert existing_release_id == pinned_release_id

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    initial_context = await open_or_resume_session(
                        db,
                        project_id="agent-test-project",
                        channel_id="agent-test-channel",
                        customer_id="fence-test-customer",
                        external_chat_id="fence-test-chat",
                        worker_id="fence-worker-a",
                        lease_seconds=60,
                        correlation_id="fence-session-opened",
                    )
                    assert initial_context is not None
                    await commit_turn(
                        db,
                        lease=initial_context.lease,
                        inbound=MessageInput(
                            role="user",
                            content={"text": "first turn"},
                            external_message_id="fence-message-1",
                        ),
                        outbound=(
                            MessageInput(role="assistant", content={"text": "first reply"}),
                        ),
                        outbox=(
                            OutboundIntent(
                                payload={"text": "first reply"},
                                idempotency_key="fence-outbox-1",
                                correlation_id="fence-turn-1",
                            ),
                        ),
                        correlation_id="fence-turn-1",
                    )
                    stale_session_id = initial_context.session_id
                    stale_lease = initial_context.lease

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await db.execute(
                        update(DialogSession)
                        .where(DialogSession.id == stale_session_id)
                        .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
                    )
                    current_lease = await acquire_session_lease(
                        db,
                        project_id="agent-test-project",
                        session_id=stale_session_id,
                        owner="fence-worker-b",
                        lease_seconds=60,
                    )
                    assert current_lease is not None
                    assert current_lease.fence == stale_lease.fence + 1

            async def session_counts(db: AsyncSession) -> tuple[int, int, int]:
                return (
                    await db.scalar(
                        select(func.count())
                        .select_from(ChatMessage)
                        .where(ChatMessage.session_id == stale_session_id)
                    ),
                    await db.scalar(
                        select(func.count())
                        .select_from(OutboxMessage)
                        .where(OutboxMessage.session_id == stale_session_id)
                    ),
                    await db.scalar(
                        select(func.count())
                        .select_from(DomainEvent)
                        .where(DomainEvent.session_id == stale_session_id)
                    ),
                )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    counts_before_stale_commit = await session_counts(db)

            with pytest.raises(RuntimeError, match="fenced"):
                async with AsyncSession(engine) as db:
                    async with db.begin():
                        await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                        await db.execute(
                            update(DialogSession)
                            .where(DialogSession.id == stale_session_id)
                            .values(variables={"must_rollback": True})
                        )
                        await commit_turn(
                            db,
                            lease=stale_lease,
                            inbound=MessageInput(
                                role="user",
                                content={"text": "stale turn"},
                                external_message_id="fence-message-stale",
                            ),
                            outbound=(
                                MessageInput(
                                    role="assistant",
                                    content={"text": "must not be saved"},
                                ),
                            ),
                            outbox=(
                                OutboundIntent(
                                    payload={"text": "must not be sent"},
                                    idempotency_key="fence-outbox-stale",
                                    correlation_id="fence-turn-stale",
                                ),
                            ),
                            correlation_id="fence-turn-stale",
                        )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    counts_after_stale_commit = await session_counts(db)
                    stale_dialog = await db.get(DialogSession, stale_session_id)
                    assert counts_after_stale_commit == counts_before_stale_commit
                    assert stale_dialog is not None
                    assert stale_dialog.variables == {}
                    assert stale_dialog.version == 1
                    assert stale_dialog.lease_owner == "fence-worker-b"
                    assert stale_dialog.lease_fence == current_lease.fence

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await close_session(
                        db,
                        lease=current_lease,
                        reason="manual",
                        closed_by="fence-test",
                        correlation_id="fence-session-closed",
                    )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    reopened_context = await open_or_resume_session(
                        db,
                        project_id="agent-test-project",
                        channel_id="agent-test-channel",
                        customer_id="fence-test-customer",
                        external_chat_id="fence-test-chat",
                        worker_id="fence-worker-c",
                        lease_seconds=60,
                        correlation_id="fence-session-reopened",
                    )
                    assert reopened_context is not None
                    assert reopened_context.session_id == stale_session_id
                    assert reopened_context.release_id == initial_context.release_id
                    await commit_turn(
                        db,
                        lease=reopened_context.lease,
                        inbound=MessageInput(
                            role="user",
                            content={"text": "after reopen"},
                            external_message_id="fence-message-2",
                        ),
                        outbound=(
                            MessageInput(
                                role="assistant",
                                content={"text": "second reply"},
                            ),
                        ),
                        outbox=(
                            OutboundIntent(
                                payload={"text": "second reply"},
                                idempotency_key="fence-outbox-2",
                                correlation_id="fence-turn-2",
                            ),
                        ),
                        correlation_id="fence-turn-2",
                    )

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    counts_before_duplicate = await session_counts(db)
                    dialog = await db.get(DialogSession, stale_session_id)
                    assert dialog is not None
                    assert dialog.state == "active"
                    assert dialog.release_id == initial_context.release_id
                    assert dialog.reopen_count == 1
                    assert dialog.version == 4
                    assert counts_before_duplicate == (4, 2, 5)
                    reopened_event = await db.scalar(
                        select(DomainEvent).where(
                            DomainEvent.session_id == stale_session_id,
                            DomainEvent.event_type == "dialog.session_reopened",
                        )
                    )
                    assert reopened_event is not None
                    assert reopened_event.payload["previous_close_reason"] == "manual"
                    assert reopened_event.payload["previous_closed_by"] == "fence-test"

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    duplicate_context = await open_or_resume_session(
                        db,
                        project_id="agent-test-project",
                        channel_id="agent-test-channel",
                        customer_id="fence-test-customer",
                        external_chat_id="fence-test-chat",
                        worker_id="fence-worker-d",
                        lease_seconds=60,
                        correlation_id="fence-duplicate",
                    )
                    assert duplicate_context is not None
                    duplicate_result = await commit_turn(
                        db,
                        lease=duplicate_context.lease,
                        inbound=MessageInput(
                            role="user",
                            content={"text": "after reopen"},
                            external_message_id="fence-message-2",
                        ),
                        outbound=(
                            MessageInput(
                                role="assistant",
                                content={"text": "duplicate response"},
                            ),
                        ),
                        outbox=(
                            OutboundIntent(
                                payload={"text": "duplicate response"},
                                idempotency_key="fence-outbox-duplicate",
                                correlation_id="fence-turn-duplicate",
                            ),
                        ),
                        correlation_id="fence-turn-duplicate",
                    )
                    assert duplicate_result.duplicate

            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    assert await session_counts(db) == counts_before_duplicate
                    dialog = await db.get(DialogSession, stale_session_id)
                    assert dialog is not None
                    assert dialog.reopen_count == 1
                    assert dialog.version == 4
        finally:
            try:
                if schema_created:
                    async with engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                await engine.dispose()

    asyncio.run(verify())
