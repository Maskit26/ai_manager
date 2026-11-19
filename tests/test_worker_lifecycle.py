import asyncio
import signal
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from ai_seller_core import main as app_module
from ai_seller_core.core.config import Settings
from ai_seller_core.jobs.cli import _shutdown_signals
from ai_seller_core.jobs.worker import Worker
from ai_seller_core.persistence import base

DATABASE_URL = "postgresql+asyncpg://user:password@localhost:5432/ai_seller"


def test_worker_with_empty_registry_waits_for_shutdown(monkeypatch) -> None:
    worker = Worker(
        queue="inbound_dialog",
        handlers={},
        settings=Settings(_env_file=None, database_url=DATABASE_URL),
    )
    retention_cleanup = AsyncMock(return_value=(0, 0))
    monkeypatch.setattr(worker, "_run_retention_cleanup", retention_cleanup)
    claim = AsyncMock(side_effect=AssertionError("empty registry must not claim jobs"))
    monkeypatch.setattr(worker, "_claim", claim)

    async def run_worker() -> None:
        stop_event = asyncio.Event()
        task = asyncio.create_task(worker.run(stop_event))
        await asyncio.sleep(0)
        assert not task.done()
        stop_event.set()
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(run_worker())
    retention_cleanup.assert_awaited_once()
    claim.assert_not_awaited()


def test_database_lifespan_disposes_engine(monkeypatch) -> None:
    engine = Mock()
    engine.dispose = AsyncMock()
    session_factory = Mock()
    create_engine = Mock(return_value=engine)
    create_session_factory = Mock(return_value=session_factory)
    monkeypatch.setattr(base, "create_async_engine", create_engine)
    monkeypatch.setattr(base, "async_sessionmaker", create_session_factory)
    settings = Settings(_env_file=None, database_url=DATABASE_URL)

    async def use_database() -> None:
        async with base.database_lifespan(settings):
            assert base.get_session_factory() is session_factory

    asyncio.run(use_database())

    create_engine.assert_called_once_with(DATABASE_URL, pool_pre_ping=True)
    create_session_factory.assert_called_once_with(engine, expire_on_commit=False)
    engine.dispose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="lifespan is not active"):
        base.get_session_factory()


def test_database_lifespan_disposes_engine_after_error(monkeypatch) -> None:
    engine = Mock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(base, "create_async_engine", Mock(return_value=engine))
    monkeypatch.setattr(base, "async_sessionmaker", Mock(return_value=Mock()))
    settings = Settings(_env_file=None, database_url=DATABASE_URL)

    async def fail_inside_lifecycle() -> None:
        async with base.database_lifespan(settings):
            raise RuntimeError("test failure")

    with pytest.raises(RuntimeError, match="test failure"):
        asyncio.run(fail_inside_lifecycle())
    engine.dispose.assert_awaited_once()


def test_api_lifespan_closes_qdrant_http_client(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        qdrant_url="http://127.0.0.1:6333",
    )

    @asynccontextmanager
    async def database_lifespan(_settings):
        yield

    monkeypatch.setattr(app_module, "get_settings", lambda: settings)
    monkeypatch.setattr(app_module, "database_lifespan", database_lifespan)
    app = app_module.create_app()

    async def exercise_lifespan() -> None:
        async with app.router.lifespan_context(app):
            client = app.state.qdrant_client
            assert not client.is_closed
        assert client.is_closed

    asyncio.run(exercise_lifespan())


def test_shutdown_signal_sets_stop_event() -> None:
    async def wait_for_shutdown() -> None:
        stop_event = asyncio.Event()
        with _shutdown_signals(stop_event):
            signal_handler = signal.getsignal(signal.SIGTERM)
            signal_handler(signal.SIGTERM, None)
            await asyncio.wait_for(stop_event.wait(), timeout=1)

    asyncio.run(wait_for_shutdown())
