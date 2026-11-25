import asyncio
import io
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.billing_models import (
    DancoinBalance,
    DancoinPackage,
    DancoinTransaction,
    Invoice,
    Payment,
    Subscription,
    TariffPlan,
)
from ai_seller_core.persistence.billing_models import (
    PaymentEvent as PaymentEventRecord,
)
from ai_seller_core.persistence.models import Account
from ai_seller_core.persistence.payment_service import (
    IdempotencyConflictError,
    PaymentProviderRequest,
    ProviderPayment,
    ProviderPaymentEvent,
    ProviderRefund,
    _assert_invoice_matches,
    build_tariff_snapshot,
    create_dancoin_package_invoice,
    create_payment,
    create_tariff_invoice,
    handle_payment_event,
)

ROOT = Path(__file__).resolve().parents[1]


def test_tariff_snapshot_includes_agent_limits() -> None:
    tariff_plan = TariffPlan(
        id="plan-1",
        code="standard",
        version=1,
        name="Standard",
        included_agents=2,
        max_agents=5,
        features={"llm_calls_enabled": True},
        limits={"max_projects": 3},
        dancoin_grant_conditions={},
        billing_options={},
    )
    period_start = datetime(2026, 11, 23, tzinfo=UTC)
    period_end = datetime(2026, 12, 23, tzinfo=UTC)

    snapshot = build_tariff_snapshot(
        tariff_plan,
        amount=Decimal("10.00"),
        currency="USD",
        period_start=period_start,
        period_end=period_end,
    )

    assert snapshot["schema_version"] == 1
    assert snapshot["included_agents"] == 2
    assert snapshot["max_agents"] == 5


def test_invoice_replay_accepts_legacy_agent_limit_fields_only() -> None:
    period_start = datetime(2026, 11, 23, tzinfo=UTC)
    period_end = datetime(2026, 12, 23, tzinfo=UTC)
    legacy_snapshot = {
        "schema_version": 1,
        "purchase_type": "tariff",
        "price": {"amount": "10.00", "currency": "USD"},
        "features": {"llm_calls_enabled": True},
        "limits": {"max_projects": 3},
    }
    invoice = SimpleNamespace(
        purchase_type="tariff",
        snapshot=legacy_snapshot,
        currency="USD",
        total=Decimal("10.00"),
        period_start=period_start,
        period_end=period_end,
    )

    _assert_invoice_matches(
        invoice,
        purchase_type="tariff",
        snapshot={
            **legacy_snapshot,
            "included_agents": 2,
            "max_agents": 5,
        },
        currency="USD",
        total=Decimal("10.00"),
        period_start=period_start,
        period_end=period_end,
    )


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("price", {"amount": "11.00", "currency": "USD"}),
        ("features", {"llm_calls_enabled": False}),
        ("limits", {"max_projects": 4}),
    ],
)
def test_invoice_replay_rejects_changed_tariff_terms(
    field: str,
    changed_value: object,
) -> None:
    period_start = datetime(2026, 11, 23, tzinfo=UTC)
    period_end = datetime(2026, 12, 23, tzinfo=UTC)
    legacy_snapshot = {
        "schema_version": 1,
        "purchase_type": "tariff",
        "price": {"amount": "10.00", "currency": "USD"},
        "features": {"llm_calls_enabled": True},
        "limits": {"max_projects": 3},
    }
    invoice = SimpleNamespace(
        purchase_type="tariff",
        snapshot=legacy_snapshot,
        currency="USD",
        total=Decimal("10.00"),
        period_start=period_start,
        period_end=period_end,
    )

    with pytest.raises(IdempotencyConflictError):
        _assert_invoice_matches(
            invoice,
            purchase_type="tariff",
            snapshot={
                **legacy_snapshot,
                "included_agents": 2,
                "max_agents": 5,
                field: changed_value,
            },
            currency="USD",
            total=Decimal("10.00"),
            period_start=period_start,
            period_end=period_end,
        )


class InMemoryPaymentProvider:
    name = "test"

    def __init__(self) -> None:
        self.payments: dict[str, ProviderPayment] = {}
        self.payment_keys: dict[str, str] = {}
        self.refunds: dict[str, ProviderRefund] = {}

    def parse_event(self, payload: dict[str, object]) -> ProviderPaymentEvent:
        return ProviderPaymentEvent(
            provider=self.name,
            provider_event_id=str(payload["event_id"]),
            payment_id=str(payload["payment_id"]),
            external_payment_id=str(payload["external_payment_id"]),
            invoice_id=str(payload["invoice_id"]),
            amount=Decimal(str(payload["amount"])),
            currency=str(payload["currency"]),
            event_type=str(payload["event_type"]),
            provider_status=str(payload["status"]),
            occurred_at=datetime.now(UTC),
            payload=payload,
        )

    async def create_payment(self, request: PaymentProviderRequest) -> ProviderPayment:
        existing_payment_id = self.payment_keys.get(request.idempotency_key)
        if existing_payment_id is not None:
            return self.payments[existing_payment_id]
        payment = ProviderPayment(
            payment_id=request.payment_id,
            external_payment_id=f"external-{request.payment_id}",
            invoice_id=request.invoice_id,
            amount=request.amount,
            currency=request.currency,
            status="pending",
        )
        self.payment_keys[request.idempotency_key] = request.payment_id
        self.payments[request.payment_id] = payment
        return payment

    async def verify_payment(self, event: ProviderPaymentEvent) -> ProviderPayment:
        return self.payments[event.payment_id]

    async def refund_payment(
        self,
        *,
        payment_id: str,
        external_payment_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> ProviderRefund:
        existing = self.refunds.get(idempotency_key)
        if existing is not None:
            return existing
        assert self.payments[payment_id].external_payment_id == external_payment_id
        refund = ProviderRefund(
            external_refund_id=f"refund-{payment_id}",
            amount=amount,
            currency=currency,
            status="succeeded",
        )
        self.refunds[idempotency_key] = refund
        return refund

    def set_status(self, payment_id: str, status: str) -> None:
        self.payments[payment_id] = replace(
            self.payments[payment_id],
            status=status,
            paid_at=datetime.now(UTC) if status == "succeeded" else None,
        )

    def event(
        self,
        payment: Payment,
        *,
        event_id: str,
        event_type: str = "payment.succeeded",
        status: str = "succeeded",
        invoice_id: str | None = None,
        amount: Decimal | None = None,
        currency: str | None = None,
    ) -> ProviderPaymentEvent:
        return ProviderPaymentEvent(
            provider=self.name,
            provider_event_id=event_id,
            payment_id=payment.id,
            external_payment_id=self.payments[payment.id].external_payment_id,
            invoice_id=invoice_id or payment.invoice_id,
            amount=amount if amount is not None else payment.amount,
            currency=currency if currency is not None else payment.currency,
            event_type=event_type,
            provider_status=status,
            occurred_at=datetime.now(UTC),
            payload={"source": "test-adapter"},
        )


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=io.StringIO())
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def test_payment_workflow_postgresql() -> None:
    original_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        database_url = Settings().database_url.get_secret_value()
    except ValidationError as error:
        if any(
            item["loc"] == ("database_url",) and item["type"] == "missing"
            for item in error.errors()
        ):
            pytest.skip("Configure DATABASE_URL for PostgreSQL payment tests")
        raise
    finally:
        if original_database_url is not None:
            os.environ["DATABASE_URL"] = original_database_url

    async def verify() -> None:
        schema = f"test_payments_{uuid4().hex}"
        bootstrap_engine = create_async_engine(database_url)
        schema_created = False
        engine = None
        account_id = str(uuid4())
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

            async with engine.begin() as connection:

                def upgrade(sync_connection) -> None:
                    config = alembic_config()
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
                await connection.execute(
                    insert(TariffPlan),
                    {
                        "id": "plan-1",
                        "code": "standard",
                        "version": 1,
                        "name": "Standard",
                        "is_active": True,
                        "included_agents": 2,
                        "max_agents": 5,
                        "features": {"chat": True},
                        "limits": {"agents": 2},
                        "dancoin_grant_conditions": {},
                        "billing_options": {},
                    },
                )
                await connection.execute(
                    insert(DancoinPackage),
                    {
                        "id": "package-1",
                        "code": "coins",
                        "version": 1,
                        "name": "Test package",
                        "dancoin_amount": Decimal("25.00"),
                        "price_amount": Decimal("12.00"),
                        "currency": "USD",
                        "is_active": True,
                    },
                )
                await connection.execute(
                    insert(Account),
                    {
                        "id": account_id,
                        "status": "active",
                    },
                )

            provider = InMemoryPaymentProvider()
            period_start = datetime(2026, 11, 23, tzinfo=UTC)
            period_end = period_start + timedelta(days=30)

            async def with_session(operation):
                async with AsyncSession(engine, expire_on_commit=False) as db:
                    return await operation(db)

            async def make_tariff_invoice(
                key: str, start: datetime, end: datetime, number: str
            ) -> Invoice:
                async def create(db: AsyncSession) -> Invoice:
                    async with db.begin():
                        plan = await db.get(TariffPlan, "plan-1")
                        assert plan is not None
                        return await create_tariff_invoice(
                            db,
                            account_id=account_id,
                            tariff_plan=plan,
                            number=number,
                            amount=Decimal("10.00"),
                            currency="USD",
                            period_start=start,
                            period_end=end,
                            idempotency_key=key,
                            dancoin_grant_amount=Decimal("5.00"),
                        )

                return await with_session(create)

            async def start_payment(invoice: Invoice, key: str) -> Payment:
                async def create(db: AsyncSession) -> Payment:
                    return await create_payment(
                        db,
                        provider=provider,
                        account_id=account_id,
                        invoice_id=invoice.id,
                        idempotency_key=key,
                    )

                return await with_session(create)

            async def send_event(event: ProviderPaymentEvent):
                async def handle(db: AsyncSession):
                    return await handle_payment_event(
                        db,
                        provider=provider,
                        event=event,
                    )

                return await with_session(handle)

            first_invoice = await make_tariff_invoice(
                "invoice-period-1",
                period_start,
                period_end,
                "INV-PERIOD-1",
            )
            replayed_invoice = await make_tariff_invoice(
                "invoice-period-1",
                period_start,
                period_end,
                "INV-IGNORED-ON-REPLAY",
            )
            assert replayed_invoice.id == first_invoice.id

            stalled_payment = await start_payment(first_invoice, "checkout-stalled")
            fast_payment = await start_payment(first_invoice, "checkout-fast")
            provider.set_status(fast_payment.id, "succeeded")
            fast_event = provider.event(fast_payment, event_id="evt-fast-success")
            fast_result = await send_event(fast_event)
            assert fast_result.status == "processed"
            replayed_payment = await start_payment(first_invoice, "checkout-fast")
            assert replayed_payment.id == fast_payment.id

            subscription = await with_session(
                lambda db: db.scalar(
                    select(Subscription).where(Subscription.account_id == account_id)
                )
            )
            assert subscription is not None
            assert subscription.status == "active"
            assert subscription.current_period_end == period_end
            assert subscription.snapshot["billing_invoice_id"] == first_invoice.id
            assert subscription.snapshot["included_agents"] == 2
            assert subscription.snapshot["max_agents"] == 5

            balance = await with_session(lambda db: db.get(DancoinBalance, account_id))
            assert balance is not None
            assert balance.grant_balance == Decimal("5.00")

            grant_count = await with_session(
                lambda db: db.scalar(
                    select(func.count())
                    .select_from(DancoinTransaction)
                    .where(DancoinTransaction.type == "grant")
                )
            )
            assert grant_count == 1

            duplicate_event_result = await send_event(fast_event)
            assert duplicate_event_result.duplicate is True
            provider.set_status(stalled_payment.id, "succeeded")
            stalled_event = provider.event(
                stalled_payment,
                event_id="evt-stalled-success",
            )
            stalled_result = await send_event(stalled_event)
            assert stalled_result.status == "processed"
            assert provider.refunds["duplicate-payment-refund:" + stalled_payment.id]
            assert (
                await with_session(
                    lambda db: db.scalar(
                        select(Payment.status).where(Payment.id == stalled_payment.id)
                    )
                )
                == "refunded"
            )

            stale_pending = provider.event(
                fast_payment,
                event_id="evt-stale-pending",
                event_type="payment.pending",
                status="pending",
            )
            assert (await send_event(stale_pending)).status == "processed"
            assert (
                await with_session(
                    lambda db: db.scalar(
                        select(Payment.status).where(Payment.id == fast_payment.id)
                    )
                )
                == "succeeded"
            )

            async with AsyncSession(engine, expire_on_commit=False) as db:
                async with db.begin():
                    stored_subscription = await db.get(Subscription, subscription.id)
                    assert stored_subscription is not None
                    stored_subscription.status = "canceled"
            next_start = period_end
            next_end = next_start + timedelta(days=30)
            next_invoice = await make_tariff_invoice(
                "invoice-period-2",
                next_start,
                next_end,
                "INV-PERIOD-2",
            )
            next_payment = await start_payment(next_invoice, "checkout-period-2")
            provider.set_status(next_payment.id, "succeeded")
            assert (
                await send_event(provider.event(next_payment, event_id="evt-period-2-success"))
            ).status == "processed"
            resumed_subscription = await with_session(
                lambda db: db.get(Subscription, subscription.id)
            )
            assert resumed_subscription is not None
            assert resumed_subscription.status == "active"
            assert resumed_subscription.current_period_end == next_end
            assert resumed_subscription.canceled_at is None

            async with AsyncSession(engine, expire_on_commit=False) as db:
                async with db.begin():
                    expired_subscription = await db.get(Subscription, subscription.id)
                    assert expired_subscription is not None
                    expired_subscription.status = "parking"
                    expired_end = datetime.now(UTC) - timedelta(seconds=1)
                    expired_subscription.current_period_start = expired_end - timedelta(days=30)
                    expired_subscription.current_period_end = expired_end
            expired_period_start = datetime.now(UTC)
            expired_period_end = expired_period_start + timedelta(days=30)
            expired_invoice = await make_tariff_invoice(
                "invoice-after-expiration",
                expired_period_start,
                expired_period_end,
                "INV-AFTER-EXPIRATION",
            )
            expired_payment = await start_payment(
                expired_invoice,
                "checkout-after-expiration",
            )
            provider.set_status(expired_payment.id, "succeeded")
            assert (
                await send_event(
                    provider.event(
                        expired_payment,
                        event_id="evt-after-expiration-success",
                    )
                )
            ).status == "processed"
            expired_resumption = await with_session(
                lambda db: db.get(Subscription, subscription.id)
            )
            assert expired_resumption is not None
            assert expired_resumption.status == "active"
            assert expired_resumption.current_period_end == expired_period_end

            grant_count = await with_session(
                lambda db: db.scalar(
                    select(func.count())
                    .select_from(DancoinTransaction)
                    .where(DancoinTransaction.type == "grant")
                )
            )
            assert grant_count == 3

            package_invoice = await with_session(
                lambda db: _make_package_invoice(
                    db,
                    account_id=account_id,
                    number="INV-PACKAGE-1",
                )
            )
            package_payment = await start_payment(
                package_invoice,
                "checkout-package-1",
            )
            provider.set_status(package_payment.id, "succeeded")
            package_event = provider.event(
                package_payment,
                event_id="evt-package-success",
            )
            assert (await send_event(package_event)).status == "processed"
            assert (await send_event(package_event)).duplicate is True
            balance = await with_session(lambda db: db.get(DancoinBalance, account_id))
            assert balance is not None
            assert balance.purchased_balance == Decimal("25.00")

            mismatch_invoice = await make_tariff_invoice(
                "invoice-mismatch",
                expired_period_end,
                expired_period_end + timedelta(days=30),
                "INV-MISMATCH",
            )
            mismatch_payment = await start_payment(
                mismatch_invoice,
                "checkout-mismatch",
            )
            provider.set_status(mismatch_payment.id, "succeeded")

            amount_mismatch = provider.event(
                mismatch_payment,
                event_id="evt-amount-mismatch",
                amount=Decimal("11.00"),
            )
            assert (await send_event(amount_mismatch)).status == "rejected"

            currency_mismatch = provider.event(
                mismatch_payment,
                event_id="evt-currency-mismatch",
                currency="EUR",
            )
            assert (await send_event(currency_mismatch)).status == "rejected"

            invoice_mismatch = provider.event(
                mismatch_payment,
                event_id="evt-invoice-mismatch",
                invoice_id="different-invoice",
            )
            assert (await send_event(invoice_mismatch)).status == "rejected"
            assert (
                await with_session(
                    lambda db: db.scalar(
                        select(Payment.status).where(Payment.id == mismatch_payment.id)
                    )
                )
                == "pending"
            )
            assert (
                await with_session(
                    lambda db: db.scalar(
                        select(Invoice.status).where(Invoice.id == mismatch_invoice.id)
                    )
                )
                == "pending"
            )

            provider_record = provider.payments[mismatch_payment.id]
            provider.payments[mismatch_payment.id] = replace(
                provider_record,
                currency="EUR",
            )
            verified_mismatch = provider.event(
                mismatch_payment,
                event_id="evt-provider-currency-mismatch",
            )
            assert (await send_event(verified_mismatch)).status == "rejected"
            assert (
                await with_session(
                    lambda db: db.scalar(
                        select(PaymentEventRecord.processing_status).where(
                            PaymentEventRecord.provider_event_id == "evt-provider-currency-mismatch"
                        )
                    )
                )
                == "rejected"
            )

            payment_count = await with_session(
                lambda db: db.scalar(
                    select(Payment.id).where(
                        Payment.account_id == account_id,
                        Payment.status == "succeeded",
                    )
                )
            )
            assert payment_count is not None
        finally:
            if engine is not None:
                await engine.dispose()
            if schema_created:
                async with bootstrap_engine.begin() as connection:
                    await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            await bootstrap_engine.dispose()

    asyncio.run(verify())


async def _make_package_invoice(db: AsyncSession, *, account_id: str, number: str) -> Invoice:
    async with db.begin():
        package = await db.get(DancoinPackage, "package-1")
        assert package is not None
        return await create_dancoin_package_invoice(
            db,
            account_id=account_id,
            package=package,
            number=number,
            idempotency_key="package-invoice-1",
        )
