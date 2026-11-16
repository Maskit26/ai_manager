from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_seller_core.api.health import router as health_router
from ai_seller_core.core.config import get_settings
from ai_seller_core.persistence.base import database_lifespan


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with database_lifespan(get_settings()):
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
