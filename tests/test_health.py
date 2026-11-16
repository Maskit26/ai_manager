import asyncio
from types import SimpleNamespace

import httpx
from sqlalchemy.exc import SQLAlchemyError

from ai_seller_core.api import health as health_api
from ai_seller_core.core.config import Settings
from ai_seller_core.main import app


def test_health_endpoint_returns_ok() -> None:
    async def request_health() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request_health())
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_service_unavailable_when_qdrant_is_not_configured(
    monkeypatch,
) -> None:
    async def postgresql_ok() -> str:
        return "ok"

    async def qdrant_not_configured(request, settings) -> str:
        return "not_configured"

    monkeypatch.setattr(health_api, "_check_postgresql", postgresql_ok)
    monkeypatch.setattr(health_api, "_check_qdrant", qdrant_not_configured)
    app.state.settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost:5432/ai_seller",
    )

    async def request_readiness() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.get("/ready")

    response = asyncio.run(request_readiness())
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"postgresql": "ok", "qdrant": "not_configured"},
    }


def test_readiness_returns_ok_when_all_critical_dependencies_are_available(
    monkeypatch,
) -> None:
    async def postgresql_ok() -> str:
        return "ok"

    async def qdrant_ok(request, settings) -> str:
        return "ok"

    monkeypatch.setattr(health_api, "_check_postgresql", postgresql_ok)
    monkeypatch.setattr(health_api, "_check_qdrant", qdrant_ok)
    app.state.settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost:5432/ai_seller",
        qdrant_url="http://127.0.0.1:6333",
    )

    async def request_readiness() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            return await client.get("/ready")

    response = asyncio.run(request_readiness())
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"postgresql": "ok", "qdrant": "ok"},
    }


def test_postgresql_probe_executes_a_ping_query(monkeypatch) -> None:
    class SessionStub:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, statement):
            assert str(statement) == "SELECT 1"

    session = SessionStub()
    monkeypatch.setattr(health_api, "get_session_factory", lambda: lambda: session)

    assert asyncio.run(health_api._check_postgresql()) == "ok"


def test_postgresql_probe_reports_database_errors_as_unavailable(monkeypatch) -> None:
    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, _statement):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(
        health_api,
        "get_session_factory",
        lambda: lambda: FailingSession(),
    )

    assert asyncio.run(health_api._check_postgresql()) == "unavailable"


def test_qdrant_probe_uses_configured_api_key() -> None:
    captured_headers: dict[str, str] = {}
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost:5432/ai_seller",
        qdrant_url="http://qdrant.local",
        qdrant_api_key="qdrant-secret",
    )

    async def respond(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200)

    async def check_qdrant() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(qdrant_client=client))
            )
            return await health_api._check_qdrant(request, settings)

    assert asyncio.run(check_qdrant()) == "ok"
    assert captured_headers["api-key"] == "qdrant-secret"


def test_qdrant_probe_reports_http_errors_as_unavailable() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:password@localhost:5432/ai_seller",
        qdrant_url="http://qdrant.local",
    )

    async def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async def check_qdrant() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            request = SimpleNamespace(
                app=SimpleNamespace(state=SimpleNamespace(qdrant_client=client))
            )
            return await health_api._check_qdrant(request, settings)

    assert asyncio.run(check_qdrant()) == "unavailable"
