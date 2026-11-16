from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ai_seller_core.core.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("The database lifespan is not active")
    return _session_factory


class _SessionFactoryProxy:
    def __call__(self) -> AsyncSession:
        return get_session_factory()()


SessionFactory = _SessionFactoryProxy()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


@asynccontextmanager
async def database_lifespan(settings: Settings | None = None) -> AsyncIterator[None]:
    global _engine, _session_factory

    if _engine is not None:
        raise RuntimeError("The database lifespan is already active")

    settings = settings or get_settings()
    engine = create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    _engine = engine
    _session_factory = session_factory
    try:
        yield
    finally:
        _session_factory = None
        _engine = None
        await engine.dispose()
