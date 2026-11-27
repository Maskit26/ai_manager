import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from pydantic import ValidationError

from ai_seller_core.api.auth import validate_public_keyring
from ai_seller_core.api.error_handlers import register_exception_handlers
from ai_seller_core.api.health import router as health_router
from ai_seller_core.api.v1.router import router as v1_router
from ai_seller_core.core.config import get_settings
from ai_seller_core.core.logging import configure_logging
from ai_seller_core.persistence.base import database_lifespan

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging("api", settings)
    try:
        validate_public_keyring(settings)
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(database_lifespan(settings))
            app.state.settings = settings
            app.state.qdrant_client = None
            if settings.qdrant_url is not None:
                app.state.qdrant_client = await stack.enter_async_context(
                    httpx.AsyncClient(timeout=2.0)
                )
            logger.info("API application started")
            yield
    except Exception:
        logger.exception("API application startup or shutdown failed")
        raise
    else:
        logger.info("API application stopped")


def create_app() -> FastAPI:
    try:
        settings = get_settings()
    except ValidationError:
        configure_logging("api")
        logger.exception("API configuration validation failed")
        raise
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        debug=False,
        openapi_tags=[
            {"name": "health", "description": "Service health and readiness."},
            {
                "name": "account",
                "description": "Current account context and membership management.",
            },
            {
                "name": "onboarding",
                "description": "Trusted server-to-server account provisioning.",
            },
            {
                "name": "projects",
                "description": "Tenant-scoped project management and lifecycle.",
            },
            {"name": "agents", "description": "Agent draft and release management."},
            {"name": "billing", "description": "Account billing, subscriptions, and usage."},
        ],
    )
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(v1_router)
    return app


app = create_app()
