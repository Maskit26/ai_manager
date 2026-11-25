import asyncio
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.billing_service import (
    IdempotencyConflictError,
    InsufficientDancoinError,
    compensate_timeout_charge,
    expire_grant,
    grant_dancoin,
    purchase_dancoin,
    refund_dancoin,
    release_reservation,
    reserve_dancoin,
    settle_reservation,
    spend_dancoin,
    timeout_reservation,
)
from ai_seller_core.persistence.models import Account, DancoinBalance, DancoinTransaction

ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_billing_service_postgresql_transactions() -> None:
    database_url = Settings().database_url.get_secret_value()

    async def verify() -> None:
        engine = create_async_engine(database_url, pool_size=4, max_overflow=0)
        schema = f"test_billing_{uuid4().hex}"
        schema_created = False
        account_id = str(uuid4())

        async def set_search_path(db: AsyncSession) -> None:
            await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))

        async def in_transaction(operation):
            async with AsyncSession(engine, expire_on_commit=False) as db:
                async with db.begin():
                    await set_search_path(db)
                    return await operation(db)

        async def read_balance() -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
            async def read(db: AsyncSession):
                balance = await db.get(DancoinBalance, account_id)
                assert balance is not None
                return (
                    balance.grant_balance,
                    balance.purchased_balance,
                    balance.reserved_grant,
                    balance.reserved_purchased,
                    balance.debt_balance,
                )

            return await in_transaction(read)

        try:
            async with engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            schema_created = True

            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def upgrade(sync_connection) -> None:
                    config = alembic_config()
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)

            async with engine.begin() as connection:
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    insert(Account),
                    {
                        "id": account_id,
                        "status": "active",
                    },
                )

            async def fund(
                db: AsyncSession,
            ) -> tuple[DancoinTransaction, DancoinTransaction]:
                grant = await grant_dancoin(
                    db,
                    account_id=account_id,
                    reference="grant:period-1",
                    amount=Decimal("5.00"),
                )
                purchase = await purchase_dancoin(
                    db,
                    account_id=account_id,
                    reference="purchase:payment-1",
                    amount=Decimal("10.00"),
                )
                return grant, purchase

            grant_transaction, purchase_transaction = await in_transaction(fund)
            assert grant_transaction.balance_after == Decimal("5.00")
            assert purchase_transaction.balance_after == Decimal("15.00")
            await in_transaction(
                lambda db: grant_dancoin(
                    db,
                    account_id=account_id,
                    reference="grant:period-1",
                    amount=Decimal("5.00"),
                )
            )
            with pytest.raises(IdempotencyConflictError):
                await in_transaction(
                    lambda db: grant_dancoin(
                        db,
                        account_id=account_id,
                        reference="grant:period-1",
                        amount=Decimal("6.00"),
                    )
                )

            async def reserve_initial(db: AsyncSession):
                reservation = await reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-initial",
                    amount=Decimal("8.00"),
                )
                replay = await reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-initial",
                    amount=Decimal("8.00"),
                )
                assert replay.id == reservation.id
                return reservation

            initial_reservation = await in_transaction(reserve_initial)
            assert initial_reservation.grant_amount == Decimal("5.00")
            assert initial_reservation.purchased_amount == Decimal("3.00")
            reserve_transaction = await in_transaction(
                lambda db: db.scalar(
                    select(DancoinTransaction)
                    .where(
                        DancoinTransaction.type == "reserve",
                        DancoinTransaction.amount == Decimal("-8.00"),
                    )
                    .limit(1)
                )
            )
            assert reserve_transaction is not None
            assert reserve_transaction.balance_after == Decimal("7.00")
            settlement = await in_transaction(
                lambda db: settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-initial",
                    actual_amount=Decimal("6.00"),
                )
            )
            assert settlement.amount == Decimal("-6.00")
            assert settlement.balance_after == Decimal("9.00")

            await in_transaction(
                lambda db: reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-zero",
                    amount=Decimal("1.00"),
                )
            )

            async def settle_zero_again(db: AsyncSession):
                first = await settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-zero",
                    actual_amount=Decimal("0.00"),
                )
                second = await settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-zero",
                    actual_amount=Decimal("0.00"),
                )
                assert first.id == second.id
                assert first.type == "release"
                assert first.amount == Decimal("1.00")

            await in_transaction(settle_zero_again)

            async def settle_again(db: AsyncSession):
                first = await settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-initial",
                    actual_amount=Decimal("6.00"),
                )
                second = await settle_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-initial",
                    actual_amount=Decimal("6.00"),
                )
                assert first.id == second.id

            await in_transaction(settle_again)
            with pytest.raises(IdempotencyConflictError):
                await in_transaction(
                    lambda db: settle_reservation(
                        db,
                        account_id=account_id,
                        operation_id="llm-call-initial",
                        actual_amount=Decimal("7.00"),
                    )
                )
            assert await read_balance() == (
                Decimal("0.00"),
                Decimal("9.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            )

            async def try_concurrent_reservation(operation_id: str):
                try:
                    return await in_transaction(
                        lambda db: reserve_dancoin(
                            db,
                            account_id=account_id,
                            operation_id=operation_id,
                            amount=Decimal("6.00"),
                        )
                    )
                except InsufficientDancoinError as error:
                    return error

            concurrent_results = await asyncio.gather(
                try_concurrent_reservation("llm-call-race-1"),
                try_concurrent_reservation("llm-call-race-2"),
            )
            winners = [
                result
                for result in concurrent_results
                if not isinstance(result, InsufficientDancoinError)
            ]
            failures = [
                result
                for result in concurrent_results
                if isinstance(result, InsufficientDancoinError)
            ]
            assert len(winners) == 1
            assert len(failures) == 1
            winning_operation_id = winners[0].operation_id
            assert await read_balance() == (
                Decimal("0.00"),
                Decimal("9.00"),
                Decimal("0.00"),
                Decimal("6.00"),
                Decimal("0.00"),
            )

            async def release_again(db: AsyncSession):
                first = await release_reservation(
                    db,
                    account_id=account_id,
                    operation_id=winning_operation_id,
                )
                second = await release_reservation(
                    db,
                    account_id=account_id,
                    operation_id=winning_operation_id,
                )
                assert first.id == second.id

            await in_transaction(release_again)

            now = datetime.now(UTC)
            timeout_expiry = now - timedelta(seconds=1)
            await in_transaction(
                lambda db: reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-timeout",
                    amount=Decimal("8.00"),
                    expires_at=timeout_expiry,
                )
            )
            await in_transaction(
                lambda db: timeout_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-timeout",
                    now=now,
                )
            )
            correction = await in_transaction(
                lambda db: compensate_timeout_charge(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-timeout",
                    actual_amount=Decimal("12.00"),
                )
            )
            assert correction is not None
            assert correction.amount == Decimal("-4.00")
            assert correction.balance_after == Decimal("0.00")
            assert await read_balance() == (
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("3.00"),
            )

            grant_while_in_debt = await in_transaction(
                lambda db: grant_dancoin(
                    db,
                    account_id=account_id,
                    reference="grant:period-2",
                    amount=Decimal("5.00"),
                )
            )
            assert grant_while_in_debt.balance_after == Decimal("0.00")
            assert await read_balance() == (
                Decimal("5.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("3.00"),
            )
            await in_transaction(
                lambda db: purchase_dancoin(
                    db,
                    account_id=account_id,
                    reference="purchase:payment-2",
                    amount=Decimal("2.00"),
                )
            )
            assert await read_balance() == (
                Decimal("5.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("1.00"),
            )
            await in_transaction(
                lambda db: purchase_dancoin(
                    db,
                    account_id=account_id,
                    reference="purchase:payment-3",
                    amount=Decimal("2.00"),
                )
            )
            assert await read_balance() == (
                Decimal("5.00"),
                Decimal("1.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            )

            correction_expiry = now - timedelta(seconds=1)
            await in_transaction(
                lambda db: reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-correction",
                    amount=Decimal("4.00"),
                    expires_at=correction_expiry,
                )
            )
            await in_transaction(
                lambda db: timeout_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-correction",
                    now=now,
                )
            )

            async def correct_again(db: AsyncSession):
                first = await compensate_timeout_charge(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-correction",
                    actual_amount=Decimal("1.00"),
                )
                second = await compensate_timeout_charge(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-correction",
                    actual_amount=Decimal("1.00"),
                )
                assert first is not None
                assert second is not None
                assert first.id == second.id

            await in_transaction(correct_again)
            assert await read_balance() == (
                Decimal("4.00"),
                Decimal("1.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            )

            await in_transaction(
                lambda db: expire_grant(
                    db,
                    account_id=account_id,
                    reference="expire:period-2",
                    amount=Decimal("2.00"),
                )
            )
            await in_transaction(
                lambda db: spend_dancoin(
                    db,
                    account_id=account_id,
                    reference="agent-spend-1",
                    amount=Decimal("1.50"),
                )
            )
            await in_transaction(
                lambda db: refund_dancoin(
                    db,
                    account_id=account_id,
                    reference="refund-1",
                    amount=Decimal("1.00"),
                    source="purchase",
                )
            )
            assert await read_balance() == (
                Decimal("0.50"),
                Decimal("2.00"),
                Decimal("0.00"),
                Decimal("0.00"),
                Decimal("0.00"),
            )

            async def transaction_count(db: AsyncSession) -> int:
                return await db.scalar(select(func.count()).select_from(DancoinTransaction))

            assert await in_transaction(transaction_count) == 20

            boundary_now = datetime.now(UTC)
            await in_transaction(
                lambda db: reserve_dancoin(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-timeout-boundary",
                    amount=Decimal("1.00"),
                    expires_at=boundary_now,
                )
            )
            boundary_timeout = await in_transaction(
                lambda db: timeout_reservation(
                    db,
                    account_id=account_id,
                    operation_id="llm-call-timeout-boundary",
                    now=boundary_now,
                )
            )
            assert boundary_timeout.type == "timeout_charge"
            assert boundary_timeout.amount == Decimal("-1.00")
        finally:
            try:
                if schema_created:
                    async with engine.begin() as connection:
                        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            finally:
                await engine.dispose()

    asyncio.run(verify())
