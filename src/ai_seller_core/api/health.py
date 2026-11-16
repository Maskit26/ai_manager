import asyncio
from typing import Literal

import httpx
from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.base import get_session_factory

router = APIRouter(tags=["health"])
CheckStatus = Literal["ok", "unavailable", "not_configured"]
READINESS_TIMEOUT_SECONDS = 2.0


@router.get(
    "/health",
    summary="Проверка liveness",
    description=(
        "Проверяет, что API-процесс запущен и отвечает. "
        "Не проверяет доступность зависимостей."
    ),
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgresql() -> CheckStatus:
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_SECONDS):
            async with get_session_factory()() as session:
                await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, TimeoutError):
        return "unavailable"
    return "ok"


async def _check_qdrant(request: Request, settings: Settings) -> CheckStatus:
    if settings.qdrant_url is None:
        return "not_configured"

    client: httpx.AsyncClient = request.app.state.qdrant_client
    headers = {}
    if settings.qdrant_api_key is not None:
        headers["api-key"] = settings.qdrant_api_key.get_secret_value()

    url = f"{str(settings.qdrant_url).rstrip('/')}/collections"
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError:
        return "unavailable"
    return "ok"


@router.get(
    "/ready",
    summary="Проверка readiness",
    description=(
        "Проверяет PostgreSQL запросом `SELECT 1` и Qdrant через `/collections`. "
        "При недоступной или ненастроенной зависимости возвращает HTTP 503."
    ),
)
async def readiness(request: Request, response: Response) -> dict[str, object]:
    settings: Settings = request.app.state.settings
    checks = {
        "postgresql": await _check_postgresql(),
        "qdrant": await _check_qdrant(request, settings),
    }
    is_ready = all(status == "ok" for status in checks.values())
    if not is_ready:
        response.status_code = 503
    return {
        "status": "ready" if is_ready else "not_ready",
        "checks": checks,
    }
