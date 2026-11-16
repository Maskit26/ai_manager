from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI

from ai_seller_core.api.health import router as health_router
from ai_seller_core.core.config import get_settings
from ai_seller_core.persistence.base import database_lifespan


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(database_lifespan(settings))
        app.state.settings = settings
        app.state.qdrant_client = None
        if settings.qdrant_url is not None:
            app.state.qdrant_client = await stack.enter_async_context(
                httpx.AsyncClient(timeout=2.0)
            )
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
