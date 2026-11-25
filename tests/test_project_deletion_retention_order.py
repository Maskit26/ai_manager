import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_seller_core.persistence import project_deletion, retention


def test_purge_checks_retention_before_deleting_vector_points(monkeypatch):
    now = datetime.now(UTC)
    request = SimpleNamespace(
        project_id="project-1",
        requested_at=now - timedelta(days=31),
        status="pending",
        completed_at=None,
    )

    class Session:
        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: [request])

        async def scalar(self, statement):
            return "archived"

        @asynccontextmanager
        async def begin_nested(self):
            yield

    vector_store = SimpleNamespace(delete_project_points=AsyncMock())
    retention_elapsed = AsyncMock(return_value=False)
    monkeypatch.setattr(
        project_deletion,
        "get_settings",
        lambda: SimpleNamespace(project_deletion_retention_days=30),
    )
    monkeypatch.setattr(project_deletion, "_retention_elapsed", retention_elapsed)

    purged = asyncio.run(
        project_deletion.purge_due_project_deletions(
            Session(),
            vector_store,
            now=now,
        )
    )

    assert purged == 0
    retention_elapsed.assert_awaited_once()
    assert retention_elapsed.await_args.args[2] == now - timedelta(days=30)
    vector_store.delete_project_points.assert_not_awaited()
    assert request.status == "pending"
    assert request.completed_at is None


def test_purge_continues_after_single_request_failure(monkeypatch, caplog):
    now = datetime.now(UTC)
    requests = [
        SimpleNamespace(
            project_id=project_id,
            requested_at=now - timedelta(days=31),
            status="pending",
            completed_at=None,
        )
        for project_id in ("project-failed", "project-ok")
    ]

    class Session:
        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: requests)

        async def scalar(self, statement):
            return "archived"

        @asynccontextmanager
        async def begin_nested(self):
            yield

    vector_store = SimpleNamespace(delete_project_points=AsyncMock())
    delete_rows = AsyncMock(side_effect=[RuntimeError("database failure"), None])
    monkeypatch.setattr(
        project_deletion,
        "get_settings",
        lambda: SimpleNamespace(project_deletion_retention_days=30),
    )
    monkeypatch.setattr(project_deletion, "_retention_elapsed", AsyncMock(return_value=True))
    monkeypatch.setattr(project_deletion, "_delete_project_rows", delete_rows)

    purged = asyncio.run(
        project_deletion.purge_due_project_deletions(
            Session(),
            vector_store,
            now=now,
        )
    )

    assert purged == 1
    assert [request.status for request in requests] == ["pending", "completed"]
    assert [call.args[0] for call in vector_store.delete_project_points.await_args_list] == [
        "project-failed",
        "project-ok",
    ]
    assert "Project deletion purge failed" in caplog.text


def test_project_purge_requires_explicit_retention_configuration(monkeypatch):
    monkeypatch.setattr(
        project_deletion,
        "get_settings",
        lambda: SimpleNamespace(project_deletion_retention_days=None),
    )

    with pytest.raises(RuntimeError, match="PROJECT_DELETION_RETENTION_DAYS"):
        asyncio.run(project_deletion.purge_due_project_deletions(object(), None))


def test_retention_elapsed_checks_dialog_activity():
    statements = []

    class Session:
        async def scalar(self, statement, params):
            statements.append(str(statement))
            return False

    elapsed = asyncio.run(
        project_deletion._retention_elapsed(
            Session(),
            "project-1",
            datetime.now(UTC),
        )
    )

    assert elapsed is True
    sql = "\n".join(statements)
    assert '"chat_messages"' in sql
    assert '"dialog_sessions"' in sql
    assert '"inbox"' in sql
    assert '"outbox"' in sql


def test_activity_purge_requires_explicit_retention_configuration(monkeypatch):
    monkeypatch.setattr(
        retention,
        "get_settings",
        lambda: SimpleNamespace(activity_retention_days=None),
    )

    with pytest.raises(RuntimeError, match="ACTIVITY_RETENTION_DAYS"):
        asyncio.run(retention.purge_expired_activity(object()))


def test_activity_purge_uses_configured_retention_cutoff(monkeypatch):
    now = datetime(2026, 1, 15, tzinfo=UTC)
    expected_cutoff = now - timedelta(days=7)
    compiled_parameters = []

    class Session:
        async def scalar(self, statement, params):
            return True

        async def execute(self, statement):
            compiled_parameters.append(statement.compile().params.values())
            return SimpleNamespace(rowcount=0)

    monkeypatch.setattr(
        retention,
        "get_settings",
        lambda: SimpleNamespace(activity_retention_days=7),
    )

    result = asyncio.run(retention.purge_expired_activity(Session(), now=now))

    assert result == (0, 0, 0)
    assert len(compiled_parameters) == 3
    assert all(expected_cutoff in parameters for parameters in compiled_parameters)
