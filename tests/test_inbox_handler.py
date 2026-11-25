import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_seller_core.jobs import inbox_handler
from ai_seller_core.persistence.session_lease import SessionLease


class _Session:
    def __init__(self, event):
        self.event = event

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    @asynccontextmanager
    async def begin(self):
        yield self

    async def scalar(self, statement):
        return self.event


def _lease() -> SessionLease:
    return SessionLease(
        session_id="session-1",
        project_id="project-1",
        owner="worker-1",
        fence=1,
        lease_until=datetime.now(UTC),
    )


def test_retry_reset_releases_lease_and_restores_received_status(monkeypatch):
    event = SimpleNamespace(
        id="event-1",
        status="processing",
        processed_at=datetime.now(UTC),
        last_error=None,
    )
    session = _Session(event)
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(inbox_handler, "SessionFactory", lambda: session)
    monkeypatch.setattr(inbox_handler, "release_session_lease", release)

    asyncio.run(
        inbox_handler._reset_inbox_event_for_retry(
            "event-1",
            _lease(),
            reason="processing_failed",
        )
    )

    assert event.status == "received"
    assert event.processed_at is None
    assert event.last_error == "processing_failed"
    release.assert_awaited_once()


def test_retry_reset_does_not_change_event_after_lease_was_lost(monkeypatch):
    event = SimpleNamespace(
        id="event-1",
        status="processing",
        processed_at=None,
        last_error=None,
    )
    session = _Session(event)
    monkeypatch.setattr(inbox_handler, "SessionFactory", lambda: session)
    monkeypatch.setattr(
        inbox_handler,
        "release_session_lease",
        AsyncMock(return_value=False),
    )

    asyncio.run(
        inbox_handler._reset_inbox_event_for_retry(
            "event-1",
            _lease(),
            reason="processing_failed",
        )
    )

    assert event.status == "processing"
    assert event.last_error is None


def test_load_history_rejects_non_object_content():
    class Session:
        async def scalars(self, statement):
            return SimpleNamespace(
                all=lambda: [SimpleNamespace(id="message-1", role="user", content=None)]
            )

    with pytest.raises(ValueError, match="content must be an object"):
        asyncio.run(
            inbox_handler._load_history(
                Session(),
                session_id="session-1",
                limit=10,
            )
        )


def test_load_history_rejects_missing_text_content():
    class Session:
        async def scalars(self, statement):
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(id="message-1", role="assistant", content={"other": "value"})
                ]
            )

    with pytest.raises(ValueError, match="must contain text content"):
        asyncio.run(
            inbox_handler._load_history(
                Session(),
                session_id="session-1",
                limit=10,
            )
        )
