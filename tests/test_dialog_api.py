import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_seller_core.api.auth import get_current_principal
from ai_seller_core.api.error_handlers import register_exception_handlers
from ai_seller_core.api.v1.router import router as v1_router
from ai_seller_core.core.config import Settings
from ai_seller_core.jobs import inbox_handler
from ai_seller_core.jobs.development_handlers import build_development_handlers
from ai_seller_core.jobs.worker import Worker
from ai_seller_core.persistence.access_policy import AccountRole, Principal
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.models import (
    Account,
    Agent,
    AgentRelease,
    BackgroundJob,
    Channel,
    ChatMessage,
    DialogSession,
    InboxEvent,
    OutboxMessage,
    Project,
)

ROOT = Path(__file__).resolve().parents[1]


def _upgrade_head(sync_connection) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = sync_connection
    command.upgrade(config, "head")


def test_authenticated_dialog_api_and_development_worker(monkeypatch) -> None:
    try:
        database_url = Settings().database_url.get_secret_value()
    except ValidationError:
        pytest.skip("Configure DATABASE_URL for PostgreSQL dialog API tests")

    async def run() -> None:
        admin_engine = create_async_engine(database_url)
        schema = f"test_dialog_api_{uuid4().hex}"
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        engine = create_async_engine(
            database_url,
            connect_args={"server_settings": {"search_path": schema}},
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(_upgrade_head)

            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            account_id = str(uuid4())
            project_id = str(uuid4())
            channel_id = "test-channel"
            async with session_factory() as session, session.begin():
                await session.execute(insert(Account).values(id=account_id, status="active"))
                await session.execute(
                    insert(Project).values(
                        id=project_id,
                        account_id=account_id,
                        locale="en",
                        timezone="UTC",
                        settings={},
                        status="active",
                    )
                )
                await session.execute(
                    insert(Channel).values(
                        project_id=project_id,
                        channel_id=channel_id,
                    )
                )
                agent = Agent(
                    id=str(uuid4()),
                    project_id=project_id,
                    status="draft",
                )
                session.add(agent)
                await session.flush()
                release = AgentRelease(
                    id=str(uuid4()),
                    project_id=project_id,
                    agent_id=agent.id,
                    version=1,
                    snapshot={},
                )
                session.add(release)
                await session.flush()
                agent.active_release_id = release.id
                agent.status = "active"

            principal = Principal(
                user_id=str(uuid4()),
                account_id=account_id,
                role=AccountRole.ADMIN,
                scopes=frozenset({"dialogs:read", "dialogs:write"}),
            )
            current_principal = [principal]
            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(v1_router)
            assert "/api/v1/projects/{project_id}/dialogs" in app.openapi()["paths"]
            assert "/api/v1/dialogs/{dialog_id}/messages" in app.openapi()["paths"]

            async def override_session():
                async with session_factory() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
            app.dependency_overrides[get_current_principal] = lambda: current_principal[0]

            monkeypatch.setattr(inbox_handler, "SessionFactory", session_factory)
            handlers = build_development_handlers(
                worker_id="dialog-test-worker",
                lease_seconds=10,
                session_factory=session_factory,
            )

            async def run_worker(queue: str) -> None:
                worker = Worker(
                    queue=queue,
                    handlers=handlers,
                    worker_id=f"test-{queue}",
                    settings=SimpleNamespace(
                        job_poll_interval_seconds=0.01,
                        job_lease_seconds=10,
                    ),
                    session_factory=session_factory,
                )
                stop_event = asyncio.Event()
                worker_task = asyncio.create_task(worker.run(stop_event))
                while True:
                    async with session_factory() as session:
                        states = (
                            await session.scalars(
                                select(BackgroundJob.status).where(BackgroundJob.queue == queue)
                            )
                        ).all()
                    if "dead_letter" in states:
                        stop_event.set()
                        await worker_task
                        raise AssertionError(f"Dialog worker dead-lettered a {queue} job")
                    if all(state == "completed" for state in states):
                        stop_event.set()
                        await worker_task
                        return
                    await asyncio.sleep(0.01)

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                request_body = {
                    "channel_id": channel_id,
                    "customer_id": "customer-1",
                    "external_chat_id": "chat-1",
                    "external_message_id": "external-message-1",
                    "text": "hello test-onboarding-server-secret-32-chars",
                }
                headers = {"Idempotency-Key": "inbound-message-1"}
                accepted = await client.post(
                    f"/api/v1/projects/{project_id}/dialogs",
                    json=request_body,
                    headers=headers,
                )
                assert accepted.status_code == 202
                assert "test-onboarding-server-secret-32-chars" not in accepted.text
                first_response = accepted.json()
                replay = await client.post(
                    f"/api/v1/projects/{project_id}/dialogs",
                    json=request_body,
                    headers=headers,
                )
                assert replay.status_code == 202
                assert replay.json() == first_response
                conflict = await client.post(
                    f"/api/v1/projects/{project_id}/dialogs",
                    json={**request_body, "text": "changed request body"},
                    headers=headers,
                )
                assert conflict.status_code == 409
                assert conflict.json()["error"]["code"] == "idempotency_conflict"
                async with session_factory() as session:
                    inbox_count = await session.scalar(
                        select(func.count())
                        .select_from(InboxEvent)
                        .where(
                            InboxEvent.project_id == project_id,
                            InboxEvent.provider_event_id == headers["Idempotency-Key"],
                        )
                    )
                    inbound_job_count = await session.scalar(
                        select(func.count())
                        .select_from(BackgroundJob)
                        .where(
                            BackgroundJob.project_id == project_id,
                            BackgroundJob.queue == "inbound_dialog",
                            BackgroundJob.idempotency_key == f"inbox:{first_response['event_id']}",
                        )
                    )
                assert inbox_count == 1
                assert inbound_job_count == 1

                await run_worker("inbound_dialog")
                await run_worker("outbound_delivery")

                async with session_factory() as session:
                    dialog = await session.scalar(
                        select(DialogSession).where(DialogSession.project_id == project_id)
                    )
                    assert dialog is not None
                    dialog_id = dialog.id

                listed = await client.get(f"/api/v1/projects/{project_id}/dialogs?state=active")
                assert listed.status_code == 200
                assert [item["id"] for item in listed.json()["items"]] == [dialog_id]

                operator_message = await client.post(
                    f"/api/v1/dialogs/{dialog_id}/messages",
                    json={"text": "operator reply"},
                    headers={"Idempotency-Key": "operator-message-1"},
                )
                assert operator_message.status_code == 202
                await run_worker("inbound_dialog")
                await run_worker("outbound_delivery")

                note = await client.post(
                    f"/api/v1/dialogs/{dialog_id}/notes",
                    json={"text": "internal note"},
                    headers={"Idempotency-Key": "system-note-1"},
                )
                assert note.status_code == 202
                await run_worker("inbound_dialog")

                history = await client.get(f"/api/v1/dialogs/{dialog_id}/messages?limit=2")
                assert history.status_code == 200
                first_page = history.json()
                assert len(first_page["items"]) == 2
                assert first_page["next_cursor"]
                second_page = await client.get(
                    f"/api/v1/dialogs/{dialog_id}/messages",
                    params={"limit": 2, "cursor": first_page["next_cursor"]},
                )
                assert second_page.status_code == 200
                assert len(second_page.json()["items"]) == 2
                assert second_page.json()["next_cursor"] is None
                invalid_cursor = await client.get(
                    f"/api/v1/dialogs/{dialog_id}/messages?cursor=not-a-cursor"
                )
                assert invalid_cursor.status_code == 422
                invalid_low_limit = await client.get(
                    f"/api/v1/dialogs/{dialog_id}/messages?limit=0"
                )
                invalid_high_limit = await client.get(
                    f"/api/v1/dialogs/{dialog_id}/messages?limit=101"
                )
                assert invalid_low_limit.status_code == 422
                assert invalid_high_limit.status_code == 422

                current_principal[0] = Principal(
                    user_id=str(uuid4()),
                    account_id=str(uuid4()),
                    role=AccountRole.ADMIN,
                    scopes=frozenset({"dialogs:read"}),
                )
                forbidden = await client.get(f"/api/v1/dialogs/{dialog_id}")
                assert forbidden.status_code == 404

            async with session_factory() as session:
                messages = (
                    await session.scalars(
                        select(ChatMessage).where(ChatMessage.session_id == dialog_id)
                    )
                ).all()
                assert {message.role for message in messages} == {
                    "user",
                    "assistant",
                    "operator",
                    "system",
                }
                assert (
                    await session.scalar(
                        select(ChatMessage.delivery_status).where(
                            ChatMessage.session_id == dialog_id,
                            ChatMessage.role == "operator",
                        )
                    )
                ) == "sent"
                assert (
                    await session.scalar(
                        select(ChatMessage.delivery_status).where(
                            ChatMessage.session_id == dialog_id,
                            ChatMessage.role == "system",
                        )
                    )
                ) == "not_required"
                outbox_messages = (
                    await session.scalars(
                        select(OutboxMessage).where(OutboxMessage.session_id == dialog_id)
                    )
                ).all()
                assert len(outbox_messages) == 2
                assert all(message.status == "sent" for message in outbox_messages)
        finally:
            await engine.dispose()
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin_engine.dispose()

    asyncio.run(run())
