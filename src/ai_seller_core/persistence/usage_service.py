from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.application_models import UsageCounter
from ai_seller_core.persistence.billing_models import (
    DancoinTransaction,
    LlmModel,
    UsageRecord,
)
from ai_seller_core.persistence.billing_service import (
    BillingConsistencyError,
    IdempotencyConflictError,
    InvalidReservationStateError,
    _locked_balance,
    _locked_reservation,
    compensate_timeout_charge,
    settle_reservation,
)

_CENT = Decimal("0.01")
_THOUSAND = Decimal("1000")
_ONE_CALL = Decimal("1.000000")
_MAX_DANCOIN_COST = Decimal("9999999999.99")
_MAX_BIGINT = 2**63 - 1


class PricingConfigurationError(ValueError):
    pass


def _validate_token_count(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer token count")
    if value < 0 or value > _MAX_BIGINT:
        raise ValueError(f"{field} is outside the supported token-count range")


def _rate(value: Decimal | None, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise PricingConfigurationError(f"Price version has no valid non-negative {field}")
    return value


def calculate_llm_cost(
    model: LlmModel,
    *,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    _validate_token_count(input_tokens, "input_tokens")
    _validate_token_count(output_tokens, "output_tokens")

    input_price = _rate(model.input_price_per_1k, "input_price_per_1k")
    output_price = _rate(model.output_price_per_1k, "output_price_per_1k")
    if model.long_context_threshold_tokens is not None:
        threshold = model.long_context_threshold_tokens
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise PricingConfigurationError(
                "Price version has an invalid long-context token threshold"
            )
        if input_tokens > threshold:
            input_price = _rate(
                model.long_context_input_price_per_1k,
                "long_context_input_price_per_1k",
            )
            output_price = _rate(
                model.long_context_output_price_per_1k,
                "long_context_output_price_per_1k",
            )

    raw_cost = (
        Decimal(input_tokens) * input_price / _THOUSAND
        + Decimal(output_tokens) * output_price / _THOUSAND
    )
    try:
        cost = raw_cost.quantize(_CENT, rounding=ROUND_CEILING)
    except InvalidOperation as error:
        raise ValueError("Calculated Dancoin cost exceeds supported precision") from error
    if cost > _MAX_DANCOIN_COST:
        raise ValueError("Calculated Dancoin cost exceeds Numeric(12, 2)")
    return cost


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_operation_id(value: str) -> str:
    if not value or not value.strip() or len(value) > 200:
        raise ValueError("operation_id must contain 1 to 200 non-whitespace characters")
    return value


def _usage_matches(
    record: UsageRecord,
    *,
    account_id: str,
    project_id: str,
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    price_version: int,
    cost: Decimal,
    period_start: datetime,
    period_end: datetime | None,
) -> bool:
    return (
        record.account_id == account_id
        and record.project_id == project_id
        and record.metric == "llm_calls"
        and record.quantity == _ONE_CALL
        and record.unit == "call"
        and record.input_tokens == input_tokens
        and record.output_tokens == output_tokens
        and record.model_id == model_id
        and record.price_version == price_version
        and record.dancoin_cost == cost
        and record.period_start == period_start
        and record.period_end == period_end
    )


async def record_llm_usage_and_settle(
    db: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    operation_id: str,
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    price_version: int,
    period_start: datetime,
    period_end: datetime | None = None,
) -> tuple[UsageRecord, DancoinTransaction | None]:
    if not db.in_transaction():
        raise RuntimeError("Usage recording and settlement require an active database transaction")
    if not project_id or not project_id.strip():
        raise ValueError("LLM usage must be associated with a project")
    operation_id = _validate_operation_id(operation_id)
    if not model_id or not model_id.strip():
        raise ValueError("model_id must not be empty")
    if isinstance(price_version, bool) or not isinstance(price_version, int) or price_version <= 0:
        raise ValueError("price_version must be a positive integer")
    _validate_token_count(input_tokens, "input_tokens")
    _validate_token_count(output_tokens, "output_tokens")
    period_start = _aware_utc(period_start, "period_start")
    if period_end is not None:
        period_end = _aware_utc(period_end, "period_end")
        if period_end <= period_start:
            raise ValueError("period_end must be after period_start")

    async with db.begin_nested():
        await _locked_balance(db, account_id)
        reservation = await _locked_reservation(db, operation_id, account_id)
        model = await db.scalar(
            select(LlmModel).where(
                LlmModel.model_id == model_id,
                LlmModel.version == price_version,
            )
        )
        if model is None:
            raise PricingConfigurationError(
                f"Price version {model_id!r} v{price_version} does not exist"
            )
        cost = calculate_llm_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        existing = await db.scalar(
            select(UsageRecord).where(UsageRecord.idempotency_key == operation_id).with_for_update()
        )
        if existing is not None:
            if not _usage_matches(
                existing,
                account_id=account_id,
                project_id=project_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_id=model_id,
                price_version=price_version,
                cost=cost,
                period_start=period_start,
                period_end=period_end,
            ):
                raise IdempotencyConflictError(
                    "Usage operation ID was reused with different consumption data"
                )
            if reservation.status == "settled":
                settlement = await settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id=operation_id,
                    actual_amount=cost,
                )
            elif reservation.status == "timeout_charged":
                settlement = await compensate_timeout_charge(
                    db,
                    account_id=account_id,
                    operation_id=operation_id,
                    actual_amount=cost,
                )
            else:
                raise BillingConsistencyError(
                    "Usage record exists but its reservation is not financially settled"
                )
            return existing, settlement

        if reservation.status == "reserved":
            if cost > reservation.amount:
                raise ValueError("Calculated LLM cost exceeds the reserved maximum")
        elif reservation.status != "timeout_charged":
            raise InvalidReservationStateError(
                f"Cannot record usage for a reservation in state {reservation.status!r}"
            )

        usage_record = UsageRecord(
            id=str(uuid4()),
            account_id=account_id,
            project_id=project_id,
            metric="llm_calls",
            quantity=_ONE_CALL,
            unit="call",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=model_id,
            price_version=price_version,
            dancoin_cost=cost,
            period_start=period_start,
            period_end=period_end,
            idempotency_key=operation_id,
        )
        db.add(usage_record)
        await db.flush()

        counter_insert = insert(UsageCounter).values(
            id=str(uuid4()),
            account_id=account_id,
            project_id=project_id,
            period_start=period_start,
            metric="llm_calls",
            value=_ONE_CALL,
        )
        await db.execute(
            counter_insert.on_conflict_do_update(
                index_elements=[
                    UsageCounter.project_id,
                    UsageCounter.period_start,
                    UsageCounter.metric,
                ],
                set_={
                    "value": UsageCounter.value + counter_insert.excluded.value,
                    "updated_at": func.now(),
                },
            )
        )

        if reservation.status == "reserved":
            settlement = await settle_reservation(
                db,
                account_id=account_id,
                operation_id=operation_id,
                actual_amount=cost,
            )
        else:
            settlement = await compensate_timeout_charge(
                db,
                account_id=account_id,
                operation_id=operation_id,
                actual_amount=cost,
            )
        await db.flush()
        return usage_record, settlement
