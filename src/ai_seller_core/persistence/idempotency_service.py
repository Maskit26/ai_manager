import hashlib
import hmac
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import ApiIdempotencyRecord

IDEMPOTENCY_RETENTION = timedelta(days=30)


class IdempotencyConflictError(Exception):
    """Raised when an idempotency key is reused with a different request body."""


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _digest(canonical)


def _replay_response(
    record: ApiIdempotencyRecord,
    request_hash: str,
) -> dict[str, Any]:
    if not hmac.compare_digest(record.request_hash, request_hash):
        raise IdempotencyConflictError
    if record.response_data is None:
        raise RuntimeError("Completed idempotency record has no response data")
    return record.response_data


async def execute_idempotently(
    session: AsyncSession,
    *,
    scope: str,
    operation: str,
    key: str,
    request_data: dict[str, Any],
    action: Callable[[], Awaitable[dict[str, Any]]],
    now: datetime | None = None,
) -> tuple[dict[str, Any], bool]:
    if not session.in_transaction():
        raise RuntimeError("Idempotency operations require an active transaction")

    current_time = now or datetime.now(UTC)
    key_hash = _digest(key)
    body_hash = _request_hash(request_data)
    query = (
        select(ApiIdempotencyRecord)
        .where(
            ApiIdempotencyRecord.scope == scope,
            ApiIdempotencyRecord.operation == operation,
            ApiIdempotencyRecord.key_hash == key_hash,
        )
        .with_for_update()
    )

    for _attempt in range(3):
        existing = await session.scalar(query)
        if existing is not None:
            if existing.expires_at > current_time:
                return _replay_response(existing, body_hash), True
            await session.delete(existing)
            await session.flush()

        record = ApiIdempotencyRecord(
            scope=scope,
            operation=operation,
            key_hash=key_hash,
            request_hash=body_hash,
            expires_at=current_time + IDEMPOTENCY_RETENTION,
        )
        try:
            async with session.begin_nested():
                session.add(record)
                await session.flush()
        except IntegrityError:
            continue

        response_data = await action()
        record.response_data = response_data
        await session.flush()
        return response_data, False

    raise RuntimeError("Unable to claim idempotency key after concurrent retries")


async def purge_expired_idempotency_records(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    if not session.in_transaction():
        raise RuntimeError("Idempotency cleanup requires an active transaction")
    result = await session.execute(
        delete(ApiIdempotencyRecord).where(
            ApiIdempotencyRecord.expires_at <= (now or datetime.now(UTC))
        )
    )
    return result.rowcount or 0
