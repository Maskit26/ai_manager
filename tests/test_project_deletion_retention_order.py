import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ai_seller_core.persistence import project_deletion


def test_purge_checks_retention_before_deleting_vector_points(monkeypatch):
    now = datetime.now(timezone.utc)
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

    vector_store = SimpleNamespace(delete_project_points=AsyncMock())
    retention_elapsed = AsyncMock(return_value=False)
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
    vector_store.delete_project_points.assert_not_awaited()
    assert request.status == "pending"
    assert request.completed_at is None
