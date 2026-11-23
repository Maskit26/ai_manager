from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_seller_core.persistence.billing_models import (
    DancoinTransaction,
    UsageRecord,
)
from ai_seller_core.persistence.billing_service import (
    release_reservation,
    reserve_dancoin,
)
from ai_seller_core.persistence.entitlements_service import check_access
from ai_seller_core.persistence.usage_service import record_llm_usage_and_settle


class LLMProviderCallError(Exception):
    pass


@dataclass(frozen=True)
class LLMProviderResult[ResponseT]:
    response: ResponseT
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMHandoffResult[ResponseT]:
    response: ResponseT
    usage_record: UsageRecord
    settlement: DancoinTransaction | None


async def run_llm_handoff[ResponseT](
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider_call: Callable[[], Awaitable[LLMProviderResult[ResponseT]]],
    account_id: str,
    project_id: str,
    operation_id: str,
    maximum_cost: Decimal,
    model_id: str,
    price_version: int,
    period_start: datetime,
    period_end: datetime | None = None,
) -> LLMHandoffResult[ResponseT]:
    """Reserve funds, call the provider outside a transaction, and settle usage."""
    async with session_factory() as db:
        async with db.begin():
            await check_access(
                db,
                account_id=account_id,
                action="llm_call",
                project_id=project_id,
            )
            await reserve_dancoin(
                db,
                account_id=account_id,
                operation_id=operation_id,
                amount=maximum_cost,
            )

    try:
        provider_result = await provider_call()
    except LLMProviderCallError:
        async with session_factory() as db:
            async with db.begin():
                await release_reservation(
                    db,
                    account_id=account_id,
                    operation_id=operation_id,
                )
        raise

    async with session_factory() as db:
        async with db.begin():
            usage_record, settlement = await record_llm_usage_and_settle(
                db,
                account_id=account_id,
                project_id=project_id,
                operation_id=operation_id,
                input_tokens=provider_result.input_tokens,
                output_tokens=provider_result.output_tokens,
                model_id=model_id,
                price_version=price_version,
                period_start=period_start,
                period_end=period_end,
            )

    return LLMHandoffResult(
        response=provider_result.response,
        usage_record=usage_record,
        settlement=settlement,
    )
