import asyncio
import io
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.application_models import UsageCounter
from ai_seller_core.persistence.billing_models import (
    DancoinBalance,
    DancoinReservation,
    LlmModel,
    Subscription,
    TariffPlan,
    UsageRecord,
)
from ai_seller_core.persistence.billing_service import (
    IdempotencyConflictError,
    InsufficientDancoinError,
    grant_dancoin,
    reserve_dancoin,
    timeout_reservation,
)
from ai_seller_core.persistence.entitlements_service import (
    EntitlementAccessError,
    check_access,
)
from ai_seller_core.persistence.llm_handoff_service import (
    LLMProviderCallError,
    LLMProviderResult,
    run_llm_handoff,
)
from ai_seller_core.persistence.models import Account, Project
from ai_seller_core.persistence.usage_service import (
    PricingConfigurationError,
    calculate_llm_cost,
    record_llm_usage_and_settle,
)

ROOT = Path(__file__).resolve().parents[1]


def _price_model() -> LlmModel:
    return LlmModel(
        id="unit-price-model",
        model_id="test-provider/model",
        version=1,
        name="Unit test model",
        is_active=True,
        input_price_per_1k=Decimal("1.00000000"),
        output_price_per_1k=Decimal("2.00000000"),
        long_context_threshold_tokens=100,
        long_context_input_price_per_1k=Decimal("3.00000000"),
        long_context_output_price_per_1k=Decimal("4.00000000"),
    )


def test_calculate_llm_cost_uses_version_threshold_and_ceil_rounding() -> None:
    model = _price_model()

    assert calculate_llm_cost(
        model,
        input_tokens=100,
        output_tokens=1000,
    ) == Decimal("2.10")
    assert calculate_llm_cost(
        model,
        input_tokens=101,
        output_tokens=1000,
    ) == Decimal("4.31")
    assert calculate_llm_cost(
        model,
        input_tokens=1,
        output_tokens=0,
    ) == Decimal("0.01")
    assert calculate_llm_cost(
        model,
        input_tokens=0,
        output_tokens=0,
    ) == Decimal("0.00")


def test_calculate_llm_cost_rejects_float_rates_and_incomplete_long_context_price() -> None:
    model = _price_model()
    model.long_context_output_price_per_1k = None

    with pytest.raises(PricingConfigurationError):
        calculate_llm_cost(model, input_tokens=101, output_tokens=1)

    model.input_price_per_1k = 1.0
    with pytest.raises(PricingConfigurationError):
        calculate_llm_cost(model, input_tokens=1, output_tokens=1)


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_usage_record_counter_and_settlement_postgresql() -> None:
    original_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        database_url = Settings().database_url.get_secret_value()
    except ValidationError as error:
        if any(
            item["loc"] == ("database_url",) and item["type"] == "missing"
            for item in error.errors()
        ):
            pytest.skip("Configure DATABASE_URL for PostgreSQL usage tests")
        raise
    finally:
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url

    async def verify() -> None:
        schema = f"test_usage_{uuid4().hex}"
        bootstrap_engine = create_async_engine(database_url)
        schema_created = False
        engine = None
        account_id = str(uuid4())
        project_id = str(uuid4())
        plan_id = str(uuid4())
        subscription_id = str(uuid4())
        period_start = datetime(2026, 11, 1, tzinfo=UTC)
        period_end = datetime(2026, 12, 1, tzinfo=UTC)
        now = datetime.now(UTC)

        async def in_transaction(operation):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                async with db.begin():
                    await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    return await operation(db)

        async def read_one(statement):
            return await in_transaction(lambda db: db.scalar(statement))

        async def reserve(operation_id: str, amount: Decimal, expires_at=None) -> None:
            async def operation(db: AsyncSession) -> None:
                await reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id=operation_id,
                    amount=amount,
                    expires_at=expires_at,
                )

            await in_transaction(operation)

        async def record(
            operation_id: str,
            input_tokens: int,
            output_tokens: int,
        ):
            return await in_transaction(
                lambda db: record_llm_usage_and_settle(
                    db,
                    account_id=account_id,
                    project_id=project_id,
                    operation_id=operation_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    model_id="test-provider/model",
                    price_version=1,
                    period_start=period_start,
                    period_end=period_end,
                )
            )

        try:
            async with bootstrap_engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True
            engine = create_async_engine(
                database_url,
                pool_size=4,
                max_overflow=0,
                connect_args={"server_settings": {"search_path": schema}},
            )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)

            async with engine.begin() as connection:

                def upgrade(sync_connection) -> None:
                    config = alembic_config()
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
                await connection.execute(
                    insert(Account),
                    {
                        "id": account_id,
                        "status": "active",
                    },
                )
                await connection.execute(
                    insert(Project),
                    {
                        "id": project_id,
                        "account_id": account_id,
                        "locale": "ru",
                        "timezone": "UTC",
                        "settings": {},
                        "status": "active",
                    },
                )
                await connection.execute(
                    insert(TariffPlan),
                    {
                        "id": plan_id,
                        "code": f"usage-test-{plan_id}",
                        "version": 1,
                        "name": "Usage test",
                        "is_active": True,
                        "included_agents": 1,
                        "max_agents": 1,
                        "features": {
                            "llm_calls_enabled": True,
                            "catalog_import_enabled": True,
                            "knowledge_base_enabled": False,
                            "multi_channel_enabled": False,
                        },
                        "limits": {},
                    },
                )
                await connection.execute(
                    insert(Subscription),
                    {
                        "id": subscription_id,
                        "account_id": account_id,
                        "tariff_plan_id": plan_id,
                        "status": "active",
                        "current_period_start": now - timedelta(days=1),
                        "current_period_end": now + timedelta(days=30),
                        "snapshot": {
                            "schema_version": 1,
                            "features": {
                                "llm_calls_enabled": True,
                                "catalog_import_enabled": True,
                                "knowledge_base_enabled": False,
                                "multi_channel_enabled": False,
                            },
                            "limits": {},
                            "included_agents": 1,
                            "max_agents": 1,
                        },
                    },
                )
                await connection.execute(
                    insert(LlmModel),
                    {
                        "id": "test-provider-model-v1",
                        "model_id": "test-provider/model",
                        "version": 1,
                        "name": "Test model",
                        "is_active": True,
                        "input_price_per_1k": Decimal("1.00000000"),
                        "output_price_per_1k": Decimal("0.50000000"),
                        "long_context_threshold_tokens": 100,
                        "long_context_input_price_per_1k": Decimal("2.00000000"),
                        "long_context_output_price_per_1k": Decimal("3.00000000"),
                    },
                )

            await in_transaction(
                lambda db: grant_dancoin(
                    db,
                    account_id=account_id,
                    reference="usage-test-grant",
                    amount=Decimal("10.00"),
                )
            )
            await reserve("llm-call-1", Decimal("5.00"))

            first_record, first_settlement = await record("llm-call-1", 101, 10)
            assert first_record.dancoin_cost == Decimal("0.24")
            assert first_record.input_tokens == 101
            assert first_record.output_tokens == 10
            assert first_record.project_id == project_id
            assert first_settlement is not None
            assert first_settlement.type == "settle"
            assert first_settlement.amount == Decimal("-0.24")
            assert first_settlement.balance_after == Decimal("9.76")

            repeated_record, repeated_settlement = await record("llm-call-1", 101, 10)
            assert repeated_record.id == first_record.id
            assert repeated_settlement is not None
            assert repeated_settlement.id == first_settlement.id
            with pytest.raises(IdempotencyConflictError):
                await record("llm-call-1", 101, 11)

            assert await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            ) == Decimal("1.000000")

            await reserve("llm-call-2", Decimal("1.00"))
            second_record, _ = await record("llm-call-2", 100, 100)
            assert second_record.dancoin_cost == Decimal("0.15")
            assert await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            ) == Decimal("2.000000")

            await reserve("llm-call-rollback", Decimal("0.50"))

            class RollbackRequested(Exception):
                pass

            with pytest.raises(RollbackRequested):
                async with AsyncSession(engine, expire_on_commit=False) as db:
                    async with db.begin():
                        await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                        await record_llm_usage_and_settle(
                            db,
                            account_id=account_id,
                            project_id=project_id,
                            operation_id="llm-call-rollback",
                            input_tokens=100,
                            output_tokens=100,
                            model_id="test-provider/model",
                            price_version=1,
                            period_start=period_start,
                            period_end=period_end,
                        )
                        raise RollbackRequested()
            assert await read_one(select(func.count()).select_from(UsageRecord)) == 2
            assert await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            ) == Decimal("2.000000")
            rollback_reservation = await read_one(
                select(DancoinReservation).where(
                    DancoinReservation.operation_id == "llm-call-rollback"
                )
            )
            assert rollback_reservation is not None
            assert rollback_reservation.status == "reserved"

            expired_at = datetime.now(UTC) - timedelta(seconds=1)
            await reserve("llm-call-late", Decimal("1.00"), expires_at=expired_at)
            await in_transaction(
                lambda db: timeout_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-late",
                    now=datetime.now(UTC),
                )
            )
            late_record, correction = await record("llm-call-late", 101, 10)
            assert late_record.dancoin_cost == Decimal("0.24")
            assert correction is not None
            assert correction.type == "adjustment"
            assert correction.amount == Decimal("0.76")

            await reserve("llm-call-zero-late", Decimal("0.25"), expires_at=expired_at)
            await in_transaction(
                lambda db: timeout_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-zero-late",
                    now=datetime.now(UTC),
                )
            )
            zero_record, zero_correction = await record("llm-call-zero-late", 0, 0)
            assert zero_record.dancoin_cost == Decimal("0.00")
            assert zero_correction is not None
            assert zero_correction.amount == Decimal("0.25")

            assert await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            ) == Decimal("4.000000")
            assert await read_one(
                select(DancoinBalance.reserved_grant).where(DancoinBalance.account_id == account_id)
            ) == Decimal("0.50")
            assert await read_one(
                select(DancoinBalance.grant_balance).where(DancoinBalance.account_id == account_id)
            ) == Decimal("9.37")

            counter_before = await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            )
            usage_count_before = await read_one(select(func.count()).select_from(UsageRecord))
            duplicate_operation_id = f"parallel-duplicate-{uuid4()}"
            await reserve(duplicate_operation_id, Decimal("1.00"))
            duplicate_results = await asyncio.gather(
                record(duplicate_operation_id, 100, 100),
                record(duplicate_operation_id, 100, 100),
            )
            assert duplicate_results[0][0].id == duplicate_results[1][0].id
            assert duplicate_results[0][1].id == duplicate_results[1][1].id

            parallel_operation_ids = [f"parallel-{uuid4()}" for _ in range(2)]
            for operation_id in parallel_operation_ids:
                await reserve(operation_id, Decimal("1.00"))
            parallel_results = await asyncio.gather(
                *(record(operation_id, 100, 100) for operation_id in parallel_operation_ids)
            )
            assert len({usage_record.id for usage_record, _ in parallel_results}) == 2
            assert all(settlement is not None for _, settlement in parallel_results)
            assert await read_one(
                select(UsageCounter.value).where(
                    UsageCounter.project_id == project_id,
                    UsageCounter.period_start == period_start,
                    UsageCounter.metric == "llm_calls",
                )
            ) == counter_before + Decimal("3.000000")
            assert await read_one(select(func.count()).select_from(UsageRecord)) == (
                usage_count_before + 3
            )

            usage_record_id = first_record.id
            with pytest.raises(DBAPIError):
                await in_transaction(
                    lambda db: db.execute(
                        update(UsageRecord)
                        .where(UsageRecord.id == usage_record_id)
                        .values(quantity=Decimal("2.000000"))
                    )
                )
            with pytest.raises(DBAPIError):
                await in_transaction(
                    lambda db: db.execute(
                        delete(UsageRecord).where(UsageRecord.id == usage_record_id)
                    )
                )

            provider_calls = 0
            reserved_before_handoff = await read_one(
                select(DancoinBalance).where(DancoinBalance.account_id == account_id)
            )

            async def fake_provider_call() -> LLMProviderResult[str]:
                nonlocal provider_calls
                provider_calls += 1
                reserved = await read_one(
                    select(DancoinReservation).where(
                        DancoinReservation.operation_id == successful_operation_id
                    )
                )
                assert reserved.status == "reserved"
                return LLMProviderResult(
                    response="fake response",
                    input_tokens=100,
                    output_tokens=10,
                )

            successful_operation_id = f"handoff-success-{uuid4()}"
            handoff = await run_llm_handoff(
                session_factory,
                provider_call=fake_provider_call,
                account_id=account_id,
                project_id=project_id,
                operation_id=successful_operation_id,
                maximum_cost=Decimal("1.00"),
                model_id="test-provider/model",
                price_version=1,
                period_start=period_start,
                period_end=period_end,
            )
            assert provider_calls == 1
            assert handoff.response == "fake response"
            assert handoff.usage_record.dancoin_cost == Decimal("0.11")
            assert handoff.settlement is not None
            assert handoff.settlement.type == "settle"
            settled_reservation = await read_one(
                select(DancoinReservation).where(
                    DancoinReservation.operation_id == successful_operation_id
                )
            )
            assert settled_reservation.status == "settled"
            assert settled_reservation.amount == Decimal("1.00")
            settled_balance = await read_one(
                select(DancoinBalance).where(DancoinBalance.account_id == account_id)
            )
            assert settled_balance.reserved_grant == reserved_before_handoff.reserved_grant
            assert settled_balance.reserved_purchased == reserved_before_handoff.reserved_purchased

            async def unexpected_provider_call() -> LLMProviderResult[str]:
                nonlocal provider_calls
                provider_calls += 1
                raise AssertionError("provider must not run without sufficient balance")

            with pytest.raises(InsufficientDancoinError):
                await run_llm_handoff(
                    session_factory,
                    provider_call=unexpected_provider_call,
                    account_id=account_id,
                    project_id=project_id,
                    operation_id=f"handoff-insufficient-{uuid4()}",
                    maximum_cost=Decimal("1000.00"),
                    model_id="test-provider/model",
                    price_version=1,
                    period_start=period_start,
                    period_end=period_end,
                )
            assert provider_calls == 1

            failed_operation_id = f"handoff-provider-failure-{uuid4()}"

            async def failing_provider_call() -> LLMProviderResult[str]:
                raise LLMProviderCallError("fake provider failure")

            with pytest.raises(LLMProviderCallError):
                await run_llm_handoff(
                    session_factory,
                    provider_call=failing_provider_call,
                    account_id=account_id,
                    project_id=project_id,
                    operation_id=failed_operation_id,
                    maximum_cost=Decimal("1.00"),
                    model_id="test-provider/model",
                    price_version=1,
                    period_start=period_start,
                    period_end=period_end,
                )
            released_reservation = await read_one(
                select(DancoinReservation).where(
                    DancoinReservation.operation_id == failed_operation_id
                )
            )
            assert released_reservation.status == "released"

            initial_entitlements = await in_transaction(
                lambda db: check_access(
                    db,
                    account_id=account_id,
                    action="llm_call",
                    project_id=project_id,
                )
            )
            concurrent_maximum = initial_entitlements.available_dancoin
            concurrent_calls = [0, 0]

            def concurrent_provider_call(index: int):
                async def call() -> LLMProviderResult[str]:
                    concurrent_calls[index] += 1
                    return LLMProviderResult(
                        response=f"fake concurrent response {index}",
                        input_tokens=100,
                        output_tokens=10,
                    )

                return call

            async def concurrent_handoff(index: int):
                return await run_llm_handoff(
                    session_factory,
                    provider_call=concurrent_provider_call(index),
                    account_id=account_id,
                    project_id=project_id,
                    operation_id=f"handoff-concurrent-{index}-{uuid4()}",
                    maximum_cost=concurrent_maximum,
                    model_id="test-provider/model",
                    price_version=1,
                    period_start=period_start,
                    period_end=period_end,
                )

            concurrent_results = await asyncio.gather(
                concurrent_handoff(0),
                concurrent_handoff(1),
                return_exceptions=True,
            )
            failures = [result for result in concurrent_results if isinstance(result, Exception)]
            assert len(failures) == 1
            assert sum(concurrent_calls) == 1
            assert isinstance(
                failures[0],
                (EntitlementAccessError, InsufficientDancoinError),
            )
            if isinstance(failures[0], EntitlementAccessError):
                assert failures[0].code == "insufficient_dancoin"
        finally:
            if engine is not None:
                await engine.dispose()
            if schema_created:
                async with bootstrap_engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await bootstrap_engine.dispose()

    asyncio.run(verify())
