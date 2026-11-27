from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.application_models import AuditLog
from ai_seller_core.persistence.billing_models import (
    BillingRefundRequest,
    DancoinBalance,
    DancoinPackage,
    Invoice,
    Payment,
    Subscription,
    TariffPlan,
)
from ai_seller_core.persistence.billing_models import (
    PaymentEvent as PaymentEventRecord,
)
from ai_seller_core.persistence.billing_service import (
    BillingConsistencyError,
    IdempotencyConflictError,
    grant_dancoin,
    purchase_dancoin,
)

_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_PURCHASE_TARIFF = "tariff"
_PURCHASE_DANCOIN_PACKAGE = "dancoin_package"
_EVENT_FINAL_STATUSES = {"processed", "rejected"}
_PROVIDER_PAYMENT_STATUSES = {"pending", "succeeded", "canceled", "failed"}


class PaymentWorkflowError(ValueError):
    pass


class InvoiceConflictError(PaymentWorkflowError):
    pass


class InvoiceAlreadyPaidError(PaymentWorkflowError):
    pass


class PaymentProviderMismatchError(PaymentWorkflowError):
    pass


class UnsupportedProviderStatusError(PaymentWorkflowError):
    pass


class BillingCatalogConfigurationError(RuntimeError):
    pass


class PaymentProviderNotConfiguredError(RuntimeError):
    pass


class BillingPeriodNotFoundError(ValueError):
    pass


class SubscriptionChangeConflictError(PaymentWorkflowError):
    pass


class RefundRequestConflictError(PaymentWorkflowError):
    pass


@dataclass(frozen=True, slots=True)
class BillingPeriodOption:
    code: str
    months: int
    amount: Decimal
    currency: str
    dancoin_grant_amount: Decimal


@dataclass(frozen=True, slots=True)
class PaymentProviderRequest:
    payment_id: str
    invoice_id: str
    amount: Decimal
    currency: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    payment_id: str
    external_payment_id: str
    invoice_id: str
    amount: Decimal
    currency: str
    status: str
    paid_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderPaymentEvent:
    provider: str
    provider_event_id: str
    payment_id: str
    external_payment_id: str
    invoice_id: str
    amount: Decimal
    currency: str
    event_type: str
    provider_status: str
    occurred_at: datetime | None = None
    payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ProviderRefund:
    external_refund_id: str
    amount: Decimal
    currency: str
    status: str


@dataclass(frozen=True, slots=True)
class PaymentEventResult:
    status: str
    payment_id: str
    duplicate: bool = False


class PaymentProvider(Protocol):
    name: str

    def parse_event(self, payload: dict[str, object]) -> ProviderPaymentEvent: ...

    async def create_payment(self, request: PaymentProviderRequest) -> ProviderPayment: ...

    async def verify_payment(self, event: ProviderPaymentEvent) -> ProviderPayment: ...

    async def refund_payment(
        self,
        *,
        payment_id: str,
        external_payment_id: str,
        amount: Decimal,
        currency: str,
        idempotency_key: str,
    ) -> ProviderRefund: ...


def tariff_billing_periods(tariff_plan: TariffPlan) -> list[BillingPeriodOption]:
    options = tariff_plan.billing_options
    if not isinstance(options, dict):
        raise BillingCatalogConfigurationError(
            f"Tariff plan {tariff_plan.id} billing_options must be an object"
        )
    raw_periods = options.get("periods", [])
    if not isinstance(raw_periods, list):
        raise BillingCatalogConfigurationError(
            f"Tariff plan {tariff_plan.id} billing_options.periods must be a list"
        )

    periods: list[BillingPeriodOption] = []
    seen_codes: set[str] = set()
    for raw_period in raw_periods:
        if not isinstance(raw_period, dict):
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has a non-object billing period"
            )
        code = raw_period.get("code")
        months = raw_period.get("months")
        if not isinstance(code, str) or not code.strip() or len(code) > 100 or code != code.strip():
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has an invalid billing period code"
            )
        if code in seen_codes:
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has duplicate billing period codes"
            )
        if isinstance(months, bool) or not isinstance(months, int) or months < 1:
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has an invalid billing period length"
            )
        amount = _catalog_decimal(raw_period.get("amount"), "amount", tariff_plan.id)
        grant_amount = _catalog_decimal(
            raw_period.get("dancoin_grant_amount", "0.00"),
            "dancoin_grant_amount",
            tariff_plan.id,
            allow_zero=True,
        )
        currency = raw_period.get("currency")
        if not isinstance(currency, str):
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has an invalid billing currency"
            )
        try:
            amount = _money(amount)
            grant_amount = _money(grant_amount, allow_zero=True)
            currency = _currency(currency)
        except ValueError as error:
            raise BillingCatalogConfigurationError(
                f"Tariff plan {tariff_plan.id} has invalid billing terms"
            ) from error
        periods.append(
            BillingPeriodOption(
                code=code,
                months=months,
                amount=amount,
                currency=currency,
                dancoin_grant_amount=grant_amount,
            )
        )
        seen_codes.add(code)
    return periods


def get_tariff_billing_period(
    tariff_plan: TariffPlan,
    period_code: str,
) -> BillingPeriodOption:
    for period in tariff_billing_periods(tariff_plan):
        if period.code == period_code:
            return period
    raise BillingPeriodNotFoundError("Billing period is not available for this tariff plan")


def add_billing_months(value: datetime, months: int) -> datetime:
    start = _aware(value, "period_start")
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _catalog_decimal(
    value: object,
    field: str,
    tariff_plan_id: str,
    *,
    allow_zero: bool = False,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise BillingCatalogConfigurationError(
            f"Tariff plan {tariff_plan_id} has an invalid {field}"
        )
    try:
        amount = Decimal(str(value))
        return _money(amount, allow_zero=allow_zero)
    except (InvalidOperation, ValueError) as error:
        raise BillingCatalogConfigurationError(
            f"Tariff plan {tariff_plan_id} has an invalid {field}"
        ) from error


def _require_transaction(db: AsyncSession) -> None:
    if not db.in_transaction():
        raise RuntimeError("Payment database operations require an active transaction")


def _require_no_transaction(db: AsyncSession) -> None:
    if db.in_transaction():
        raise RuntimeError("Provider calls must not run inside an open database transaction")


def _money(value: Decimal, *, allow_zero: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("Payment amounts must be finite Decimal values")
    try:
        normalized = value.quantize(_CENT)
    except InvalidOperation as error:
        raise ValueError("Payment amount is outside the supported range") from error
    if normalized != value or normalized < _ZERO or (not allow_zero and normalized == _ZERO):
        raise ValueError("Payment amount must have at most two decimals and be positive")
    return normalized


def _currency(value: str) -> str:
    if not _CURRENCY_PATTERN.fullmatch(value):
        raise ValueError("Currency must be a three-letter uppercase code")
    return value


def _key(value: str) -> str:
    if not value or not value.strip() or len(value) > 200:
        raise ValueError("Idempotency keys must contain 1 to 200 non-whitespace characters")
    return value


async def schedule_subscription_change(
    db: AsyncSession,
    *,
    account_id: str,
    tariff_plan_id: str,
    billing_period: str,
    actor_id: str,
    now: datetime | None = None,
) -> Subscription:
    _require_transaction(db)
    plan = await db.scalar(select(TariffPlan).where(TariffPlan.id == tariff_plan_id))
    if plan is None:
        raise LookupError("Tariff plan does not exist")
    if not plan.is_active:
        raise SubscriptionChangeConflictError("Tariff plan is inactive")
    get_tariff_billing_period(plan, billing_period)

    subscription = await db.scalar(
        select(Subscription)
        .where(
            Subscription.account_id == account_id,
            Subscription.status.in_(("trial", "active", "grace_period", "parking")),
        )
        .with_for_update()
    )
    if subscription is None:
        raise SubscriptionChangeConflictError(
            "A current subscription is required to schedule a plan change"
        )
    instant = _aware(now or datetime.now(UTC), "now")
    effective_at = subscription.current_period_end
    if effective_at is None or _aware(effective_at, "current_period_end") <= instant:
        raise SubscriptionChangeConflictError(
            "A plan change can only be scheduled before the current period ends"
        )
    if subscription.pending_snapshot is not None:
        raise SubscriptionChangeConflictError(
            "A paid subscription change is already waiting to take effect"
        )
    if subscription.pending_tariff_plan_id is not None:
        if (
            subscription.pending_tariff_plan_id == tariff_plan_id
            and subscription.pending_billing_period == billing_period
        ):
            return subscription
        raise SubscriptionChangeConflictError("Another subscription change is already scheduled")

    subscription.pending_tariff_plan_id = tariff_plan_id
    subscription.pending_billing_period = billing_period
    subscription.pending_effective_at = _aware(effective_at, "current_period_end")
    subscription.pending_period_start = None
    subscription.pending_period_end = None
    subscription.pending_snapshot = None
    db.add(
        AuditLog(
            account_id=account_id,
            actor_id=actor_id,
            action="billing.subscription_change_scheduled",
            entity_type="subscription",
            entity_id=subscription.id,
            diff={
                "from_tariff_plan_id": subscription.tariff_plan_id,
                "to_tariff_plan_id": tariff_plan_id,
                "billing_period": billing_period,
                "effective_at": subscription.pending_effective_at.isoformat(),
            },
        )
    )
    await db.flush()
    return subscription


async def create_refund_request(
    db: AsyncSession,
    *,
    account_id: str,
    payment_id: str,
    requested_by: str,
    reason: str,
) -> BillingRefundRequest:
    _require_transaction(db)
    payment = await db.scalar(
        select(Payment)
        .where(Payment.id == payment_id, Payment.account_id == account_id)
        .with_for_update()
    )
    if payment is None:
        raise LookupError("Payment does not exist")
    if payment.status != "succeeded":
        raise RefundRequestConflictError("Only successful payments can be submitted for refund")
    existing = await db.scalar(
        select(BillingRefundRequest).where(
            BillingRefundRequest.account_id == account_id,
            BillingRefundRequest.payment_id == payment_id,
        )
    )
    if existing is not None:
        raise RefundRequestConflictError("A refund request already exists for this payment")

    request = BillingRefundRequest(
        id=str(uuid4()),
        account_id=account_id,
        payment_id=payment.id,
        invoice_id=payment.invoice_id,
        requested_by=requested_by,
        reason=reason,
        status="pending",
        created_at=datetime.now(UTC),
    )
    db.add(request)
    db.add(
        AuditLog(
            account_id=account_id,
            actor_id=requested_by,
            action="billing.refund_requested",
            entity_type="payment",
            entity_id=payment.id,
            diff={"payment_id": payment.id},
        )
    )
    await db.flush()
    return request


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _snapshot_amount(snapshot: dict[str, object], field: str) -> Decimal:
    value = snapshot.get(field)
    if not isinstance(value, str):
        raise PaymentWorkflowError(f"Invoice snapshot is missing string field {field!r}")
    try:
        return _money(Decimal(value), allow_zero=field == "dancoin_grant_amount")
    except (InvalidOperation, ValueError) as error:
        raise PaymentWorkflowError(
            f"Invoice snapshot field {field!r} is not a valid Dancoin amount"
        ) from error


def build_tariff_snapshot(
    tariff_plan: TariffPlan,
    *,
    amount: Decimal,
    currency: str,
    period_start: datetime,
    period_end: datetime,
    dancoin_grant_amount: Decimal = _ZERO,
) -> dict[str, object]:
    amount = _money(amount)
    currency = _currency(currency)
    period_start = _aware(period_start, "period_start")
    period_end = _aware(period_end, "period_end")
    dancoin_grant_amount = _money(dancoin_grant_amount, allow_zero=True)
    if period_end <= period_start:
        raise ValueError("Tariff period_end must be after period_start")

    return {
        "schema_version": 1,
        "purchase_type": _PURCHASE_TARIFF,
        "catalog_item": {
            "id": tariff_plan.id,
            "code": tariff_plan.code,
            "version": tariff_plan.version,
            "name": tariff_plan.name,
        },
        "price": {"amount": f"{amount:.2f}", "currency": currency},
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "features": tariff_plan.features,
        "limits": tariff_plan.limits,
        "included_agents": tariff_plan.included_agents,
        "max_agents": tariff_plan.max_agents,
        "dancoin_grant_conditions": tariff_plan.dancoin_grant_conditions,
        "dancoin_grant_amount": f"{dancoin_grant_amount:.2f}",
    }


def build_dancoin_package_snapshot(package: DancoinPackage) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purchase_type": _PURCHASE_DANCOIN_PACKAGE,
        "catalog_item": {
            "id": package.id,
            "code": package.code,
            "version": package.version,
            "name": package.name,
        },
        "price": {
            "amount": f"{package.price_amount:.2f}",
            "currency": package.currency,
        },
        "dancoin_amount": f"{package.dancoin_amount:.2f}",
    }


async def create_invoice(
    db: AsyncSession,
    *,
    account_id: str,
    number: str,
    purchase_type: str,
    snapshot: dict[str, object],
    currency: str,
    total: Decimal,
    idempotency_key: str,
    period_start: datetime | None = None,
    period_end: datetime | None = None,
) -> Invoice:
    _require_transaction(db)
    total = _money(total)
    currency = _currency(currency)
    idempotency_key = _key(idempotency_key)
    if not number or len(number) > 100:
        raise ValueError("Invoice number must contain 1 to 100 characters")
    if snapshot.get("schema_version") != 1:
        raise PaymentWorkflowError("Unsupported invoice snapshot schema version")
    if snapshot.get("purchase_type") != purchase_type:
        raise PaymentWorkflowError("Invoice and snapshot purchase types do not match")
    price = snapshot.get("price")
    if not isinstance(price, dict):
        raise PaymentWorkflowError("Invoice snapshot must contain a price object")
    if price.get("amount") != f"{total:.2f}" or price.get("currency") != currency:
        raise PaymentWorkflowError("Invoice price does not match its snapshot")
    if purchase_type not in {_PURCHASE_TARIFF, _PURCHASE_DANCOIN_PACKAGE}:
        raise ValueError("Unsupported invoice purchase type")

    if purchase_type == _PURCHASE_TARIFF:
        if period_start is None or period_end is None:
            raise ValueError("Tariff invoices require a billing period")
        period_start = _aware(period_start, "period_start")
        period_end = _aware(period_end, "period_end")
        if period_end <= period_start:
            raise ValueError("Tariff period_end must be after period_start")
        _snapshot_amount(snapshot, "dancoin_grant_amount")
    elif period_start is not None or period_end is not None:
        raise ValueError("Dancoin package invoices cannot have a subscription period")
    else:
        _snapshot_amount(snapshot, "dancoin_amount")

    existing = await db.scalar(
        select(Invoice).where(
            Invoice.account_id == account_id,
            Invoice.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        _assert_invoice_matches(
            existing,
            purchase_type=purchase_type,
            snapshot=snapshot,
            currency=currency,
            total=total,
            period_start=period_start,
            period_end=period_end,
        )
        return existing

    invoice_id = str(uuid4())
    result = await db.execute(
        insert(Invoice)
        .values(
            id=invoice_id,
            account_id=account_id,
            number=number,
            purchase_type=purchase_type,
            status="pending",
            snapshot=snapshot,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            total=total,
            idempotency_key=idempotency_key,
        )
        .on_conflict_do_nothing(constraint="uq_invoices_account_idempotency")
        .returning(Invoice.id)
    )
    inserted_id = result.scalar_one_or_none()
    if inserted_id is None:
        existing = await db.scalar(
            select(Invoice).where(
                Invoice.account_id == account_id,
                Invoice.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise BillingConsistencyError("Conflicting invoice could not be loaded")
        _assert_invoice_matches(
            existing,
            purchase_type=purchase_type,
            snapshot=snapshot,
            currency=currency,
            total=total,
            period_start=period_start,
            period_end=period_end,
        )
        return existing

    invoice = await db.get(Invoice, inserted_id)
    if invoice is None:
        raise BillingConsistencyError("Inserted invoice could not be loaded")
    return invoice


async def create_tariff_invoice(
    db: AsyncSession,
    *,
    account_id: str,
    tariff_plan: TariffPlan,
    number: str,
    amount: Decimal,
    currency: str,
    period_start: datetime,
    period_end: datetime,
    idempotency_key: str,
    dancoin_grant_amount: Decimal = _ZERO,
) -> Invoice:
    if not tariff_plan.is_active:
        raise PaymentWorkflowError("Cannot invoice an inactive tariff plan")
    snapshot = build_tariff_snapshot(
        tariff_plan,
        amount=amount,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        dancoin_grant_amount=dancoin_grant_amount,
    )
    return await create_invoice(
        db,
        account_id=account_id,
        number=number,
        purchase_type=_PURCHASE_TARIFF,
        snapshot=snapshot,
        currency=currency,
        total=amount,
        idempotency_key=idempotency_key,
        period_start=period_start,
        period_end=period_end,
    )


async def create_dancoin_package_invoice(
    db: AsyncSession,
    *,
    account_id: str,
    package: DancoinPackage,
    number: str,
    idempotency_key: str,
) -> Invoice:
    if not package.is_active:
        raise PaymentWorkflowError("Cannot invoice an inactive Dancoin package")
    snapshot = build_dancoin_package_snapshot(package)
    return await create_invoice(
        db,
        account_id=account_id,
        number=number,
        purchase_type=_PURCHASE_DANCOIN_PACKAGE,
        snapshot=snapshot,
        currency=package.currency,
        total=package.price_amount,
        idempotency_key=idempotency_key,
    )


def _assert_invoice_matches(
    invoice: Invoice,
    *,
    purchase_type: str,
    snapshot: dict[str, object],
    currency: str,
    total: Decimal,
    period_start: datetime | None,
    period_end: datetime | None,
) -> None:
    existing_snapshot = invoice.snapshot
    if isinstance(existing_snapshot, dict):
        existing_snapshot = dict(existing_snapshot)
        for key in ("included_agents", "max_agents"):
            if key not in existing_snapshot and key in snapshot:
                existing_snapshot[key] = snapshot[key]

    if (
        invoice.purchase_type != purchase_type
        or existing_snapshot != snapshot
        or invoice.currency != currency
        or invoice.total != total
        or invoice.period_start != period_start
        or invoice.period_end != period_end
    ):
        raise IdempotencyConflictError("Invoice idempotency key was reused with different terms")


async def create_payment(
    db: AsyncSession,
    *,
    provider: PaymentProvider,
    account_id: str,
    invoice_id: str,
    idempotency_key: str,
) -> Payment:
    _require_no_transaction(db)
    idempotency_key = _key(idempotency_key)

    async with db.begin():
        invoice = await db.scalar(
            select(Invoice)
            .where(Invoice.id == invoice_id, Invoice.account_id == account_id)
            .with_for_update()
        )
        if invoice is None:
            raise PaymentWorkflowError("Invoice does not belong to the requested account")
        payment = await db.scalar(
            select(Payment).where(
                Payment.account_id == account_id,
                Payment.idempotency_key == idempotency_key,
            )
        )
        if payment is not None:
            if (
                payment.invoice_id != invoice.id
                or payment.provider != provider.name
                or payment.amount != invoice.total
                or payment.currency != invoice.currency
            ):
                raise IdempotencyConflictError(
                    "Payment idempotency key was reused with different terms"
                )
        else:
            if invoice.status == "paid":
                raise InvoiceAlreadyPaidError("Invoice has already been paid")
            payment_id = str(uuid4())
            result = await db.execute(
                insert(Payment)
                .values(
                    id=payment_id,
                    account_id=account_id,
                    invoice_id=invoice.id,
                    provider=provider.name,
                    idempotency_key=idempotency_key,
                    amount=invoice.total,
                    currency=invoice.currency,
                    status="pending",
                )
                .on_conflict_do_nothing(constraint="uq_payments_account_idempotency")
                .returning(Payment.id)
            )
            inserted_id = result.scalar_one_or_none()
            if inserted_id is None:
                payment = await db.scalar(
                    select(Payment).where(
                        Payment.account_id == account_id,
                        Payment.idempotency_key == idempotency_key,
                    )
                )
                if payment is None:
                    raise BillingConsistencyError("Conflicting payment could not be loaded")
                if (
                    payment.invoice_id != invoice.id
                    or payment.provider != provider.name
                    or payment.amount != invoice.total
                    or payment.currency != invoice.currency
                ):
                    raise IdempotencyConflictError(
                        "Payment idempotency key was reused with different terms"
                    )
            else:
                payment = await db.get(Payment, inserted_id)
                if payment is None:
                    raise BillingConsistencyError("Inserted payment could not be loaded")
        payment_id = payment.id
        invoice_id = invoice.id
        payment_amount = payment.amount
        payment_currency = payment.currency

    request = PaymentProviderRequest(
        payment_id=payment_id,
        invoice_id=invoice_id,
        amount=payment_amount,
        currency=payment_currency,
        idempotency_key=idempotency_key,
    )
    provider_payment = await provider.create_payment(request)
    _validate_provider_payment(
        provider_payment,
        payment_id=payment_id,
        invoice_id=invoice_id,
        amount=payment_amount,
        currency=payment_currency,
    )

    async with db.begin():
        payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        if payment is None:
            raise BillingConsistencyError("Payment disappeared after provider creation")
        if (
            payment.external_payment_id is not None
            and payment.external_payment_id != provider_payment.external_payment_id
        ):
            raise IdempotencyConflictError(
                "Provider returned a different payment ID for the same idempotency key"
            )
        payment.external_payment_id = provider_payment.external_payment_id
        if provider_payment.status in _PROVIDER_PAYMENT_STATUSES:
            if payment.status not in {"succeeded", "refund_pending", "refunded"}:
                payment.status = provider_payment.status
        await db.flush()
        return payment


def _validate_provider_payment(
    provider_payment: ProviderPayment,
    *,
    payment_id: str,
    invoice_id: str,
    amount: Decimal,
    currency: str,
) -> None:
    if (
        provider_payment.payment_id != payment_id
        or provider_payment.invoice_id != invoice_id
        or provider_payment.amount != amount
        or provider_payment.currency != currency
        or not provider_payment.external_payment_id
    ):
        raise PaymentProviderMismatchError(
            "Provider payment does not match the local payment and invoice"
        )
    if provider_payment.status not in _PROVIDER_PAYMENT_STATUSES:
        raise UnsupportedProviderStatusError(
            f"Unsupported provider payment status {provider_payment.status!r}"
        )


def _event_payload(event: ProviderPaymentEvent) -> dict[str, object]:
    return {
        **(event.payload or {}),
        "claimed_invoice_id": event.invoice_id,
        "external_payment_id": event.external_payment_id,
        "amount": f"{event.amount:.2f}",
        "currency": event.currency,
    }


def _event_matches(existing: PaymentEventRecord, event: ProviderPaymentEvent) -> bool:
    payload = existing.payload or {}
    return (
        existing.event_type == event.event_type
        and existing.payment_id == event.payment_id
        and payload.get("claimed_invoice_id") == event.invoice_id
        and payload.get("external_payment_id") == event.external_payment_id
        and payload.get("amount") == f"{event.amount:.2f}"
        and payload.get("currency") == event.currency
    )


async def _locked_balance(db: AsyncSession, account_id: str) -> DancoinBalance:
    await db.execute(
        insert(DancoinBalance)
        .values(account_id=account_id)
        .on_conflict_do_nothing(index_elements=[DancoinBalance.account_id])
    )
    balance = await db.scalar(
        select(DancoinBalance).where(DancoinBalance.account_id == account_id).with_for_update()
    )
    if balance is None:
        raise BillingConsistencyError("Could not lock account Dancoin balance")
    return balance


async def handle_payment_event(
    db: AsyncSession,
    *,
    provider: PaymentProvider,
    event: ProviderPaymentEvent,
) -> PaymentEventResult:
    _require_no_transaction(db)
    if event.provider != provider.name:
        raise PaymentProviderMismatchError("Event provider does not match the adapter")
    _key(event.provider_event_id)
    if not event.payment_id or not event.invoice_id or not event.external_payment_id:
        raise ValueError("Payment event must identify its payment and invoice")
    if not event.event_type or len(event.event_type) > 100:
        raise ValueError("Payment event type must contain 1 to 100 characters")

    async with db.begin():
        payment = await db.get(Payment, event.payment_id)
        if payment is None or payment.provider != provider.name:
            raise PaymentWorkflowError("Event refers to an unknown provider payment")
        invoice = await db.get(Invoice, payment.invoice_id)
        if invoice is None:
            raise BillingConsistencyError("Payment refers to a missing invoice")
        event_record = await db.scalar(
            select(PaymentEventRecord)
            .where(
                PaymentEventRecord.provider == provider.name,
                PaymentEventRecord.provider_event_id == event.provider_event_id,
            )
            .with_for_update()
        )
        if event_record is not None:
            if not _event_matches(event_record, event):
                raise IdempotencyConflictError(
                    "Provider event ID was reused with different event data"
                )
            if event_record.processing_status in _EVENT_FINAL_STATUSES:
                return PaymentEventResult(
                    status=event_record.processing_status,
                    payment_id=payment.id,
                    duplicate=True,
                )
            if event_record.processing_status == "refund_pending":
                payment_id = payment.id
                event_record_id = event_record.id
                retry_refund = True
            else:
                retry_refund = False
        else:
            event_record_id = str(uuid4())
            result = await db.execute(
                insert(PaymentEventRecord)
                .values(
                    id=event_record_id,
                    account_id=payment.account_id,
                    payment_id=payment.id,
                    invoice_id=invoice.id,
                    provider=provider.name,
                    provider_event_id=event.provider_event_id,
                    event_type=event.event_type,
                    provider_status=event.provider_status,
                    processing_status="received",
                    payload=_event_payload(event),
                    provider_occurred_at=event.occurred_at,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        PaymentEventRecord.provider,
                        PaymentEventRecord.provider_event_id,
                    ]
                )
                .returning(PaymentEventRecord.id)
            )
            inserted_id = result.scalar_one_or_none()
            if inserted_id is None:
                event_record = await db.scalar(
                    select(PaymentEventRecord)
                    .where(
                        PaymentEventRecord.provider == provider.name,
                        PaymentEventRecord.provider_event_id == event.provider_event_id,
                    )
                    .with_for_update()
                )
                if event_record is None or not _event_matches(event_record, event):
                    raise IdempotencyConflictError(
                        "Provider event ID was concurrently reused with different data"
                    )
                if event_record.processing_status in _EVENT_FINAL_STATUSES:
                    return PaymentEventResult(
                        status=event_record.processing_status,
                        payment_id=payment.id,
                        duplicate=True,
                    )
                event_record_id = event_record.id
                retry_refund = event_record.processing_status == "refund_pending"
            else:
                event_record_id = inserted_id
                retry_refund = False
            payment_id = payment.id

    if retry_refund:
        return await _refund_duplicate_payment(
            db,
            provider=provider,
            payment_id=payment_id,
            event_record_id=event_record_id,
        )

    verified = await provider.verify_payment(event)

    refund_required = False
    result: PaymentEventResult
    async with db.begin():
        event_record = await db.scalar(
            select(PaymentEventRecord)
            .where(PaymentEventRecord.id == event_record_id)
            .with_for_update()
        )
        payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        if event_record is None or payment is None:
            raise BillingConsistencyError("Payment event or payment disappeared")
        invoice = await db.scalar(
            select(Invoice).where(Invoice.id == payment.invoice_id).with_for_update()
        )
        if invoice is None:
            raise BillingConsistencyError("Payment refers to a missing invoice")

        mismatch = _payment_mismatch(
            event=event,
            verified=verified,
            payment=payment,
            invoice=invoice,
        )
        if mismatch is not None:
            event_record.processing_status = "rejected"
            event_record.processed_at = datetime.now(UTC)
            event_record.payload = {
                **(event_record.payload or {}),
                "rejection_reason": mismatch,
            }
            result = PaymentEventResult(status="rejected", payment_id=payment.id)
        elif verified.status not in _PROVIDER_PAYMENT_STATUSES:
            raise UnsupportedProviderStatusError(
                f"Unsupported provider payment status {verified.status!r}"
            )
        elif payment.status == "refund_pending":
            refund_required = True
            event_record.processing_status = "refund_pending"
            result = PaymentEventResult(status="refund_pending", payment_id=payment.id)
        elif payment.status in {"succeeded", "refunded"}:
            event_record.processing_status = "processed"
            event_record.processed_at = datetime.now(UTC)
            result = PaymentEventResult(status="processed", payment_id=payment.id, duplicate=True)
        elif verified.status == "succeeded":
            await _locked_balance(db, payment.account_id)
            duplicate = await _is_duplicate_success(db, payment, invoice)
            if duplicate:
                payment.status = "refund_pending"
                payment.paid_at = verified.paid_at or datetime.now(UTC)
                event_record.processing_status = "refund_pending"
                refund_required = True
                result = PaymentEventResult(status="refund_pending", payment_id=payment.id)
            else:
                await _apply_success(db, payment, invoice)
                payment.status = "succeeded"
                payment.paid_at = verified.paid_at or datetime.now(UTC)
                invoice.status = "paid"
                event_record.processing_status = "processed"
                event_record.processed_at = datetime.now(UTC)
                result = PaymentEventResult(status="processed", payment_id=payment.id)
        elif verified.status == "pending":
            if payment.status not in {"canceled", "failed"}:
                payment.status = "pending"
            event_record.processing_status = "received"
            result = PaymentEventResult(status="received", payment_id=payment.id)
        else:
            if payment.status not in {"canceled", "failed"}:
                payment.status = verified.status
            event_record.processing_status = "processed"
            event_record.processed_at = datetime.now(UTC)
            result = PaymentEventResult(status="processed", payment_id=payment.id)
        if payment.external_payment_id is None and mismatch is None:
            payment.external_payment_id = verified.external_payment_id
        if mismatch is None:
            event_record.provider_status = verified.status
        await db.flush()

    if refund_required:
        return await _refund_duplicate_payment(
            db,
            provider=provider,
            payment_id=payment_id,
            event_record_id=event_record_id,
        )
    return result


def _payment_mismatch(
    *,
    event: ProviderPaymentEvent,
    verified: ProviderPayment,
    payment: Payment,
    invoice: Invoice,
) -> str | None:
    if event.invoice_id != invoice.id or verified.invoice_id != invoice.id:
        return "invoice_id_mismatch"
    if event.payment_id != payment.id or verified.payment_id != payment.id:
        return "payment_id_mismatch"
    if event.external_payment_id != verified.external_payment_id or (
        payment.external_payment_id is not None
        and payment.external_payment_id != verified.external_payment_id
    ):
        return "external_payment_id_mismatch"
    if (
        event.amount != payment.amount
        or verified.amount != payment.amount
        or payment.amount != invoice.total
    ):
        return "amount_mismatch"
    if (
        event.currency != payment.currency
        or verified.currency != payment.currency
        or payment.currency != invoice.currency
    ):
        return "currency_mismatch"
    return None


async def _is_duplicate_success(db: AsyncSession, payment: Payment, invoice: Invoice) -> bool:
    if invoice.status == "paid":
        return True
    other_success = await db.scalar(
        select(Payment.id).where(
            Payment.invoice_id == invoice.id,
            Payment.account_id == invoice.account_id,
            Payment.status == "succeeded",
            Payment.id != payment.id,
        )
    )
    if other_success is not None:
        return True
    if invoice.purchase_type != _PURCHASE_TARIFF:
        return False
    period_success = await db.scalar(
        select(Payment.id)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .where(
            Invoice.account_id == invoice.account_id,
            Invoice.purchase_type == _PURCHASE_TARIFF,
            Invoice.period_start == invoice.period_start,
            Invoice.period_end == invoice.period_end,
            Invoice.status == "paid",
            Payment.status == "succeeded",
            Invoice.id != invoice.id,
        )
        .limit(1)
    )
    if period_success is not None:
        return True
    subscription = await db.scalar(
        select(Subscription)
        .where(
            Subscription.account_id == invoice.account_id,
            Subscription.status.in_(("trial", "active", "grace_period", "parking")),
        )
        .with_for_update()
    )
    return (
        subscription is not None
        and subscription.current_period_end is not None
        and subscription.current_period_end > invoice.period_end
    )


async def _apply_success(db: AsyncSession, payment: Payment, invoice: Invoice) -> None:
    if invoice.purchase_type == _PURCHASE_TARIFF:
        if invoice.period_start is None or invoice.period_end is None:
            raise PaymentWorkflowError("Paid tariff invoice has no billing period")
        catalog_item = invoice.snapshot.get("catalog_item")
        if not isinstance(catalog_item, dict) or not isinstance(catalog_item.get("id"), str):
            raise PaymentWorkflowError("Tariff snapshot has no catalog item ID")
        tariff_plan_id = catalog_item["id"]
        grant_amount = _snapshot_amount(invoice.snapshot, "dancoin_grant_amount")
        subscription = await db.scalar(
            select(Subscription)
            .where(
                Subscription.account_id == invoice.account_id,
                Subscription.status.in_(("trial", "active", "grace_period", "parking")),
            )
            .with_for_update()
        )
        if subscription is None:
            subscription = await db.scalar(
                select(Subscription)
                .where(
                    Subscription.account_id == invoice.account_id,
                    Subscription.status == "canceled",
                )
                .order_by(Subscription.updated_at.desc())
                .limit(1)
                .with_for_update()
            )
        subscription_snapshot = {
            **invoice.snapshot,
            "billing_invoice_id": invoice.id,
        }
        period_start = _aware(invoice.period_start, "period_start")
        period_end = _aware(invoice.period_end, "period_end")
        if period_start > datetime.now(UTC):
            if subscription is None:
                raise PaymentWorkflowError(
                    "A future tariff invoice requires an existing subscription"
                )
            if subscription.pending_tariff_plan_id is not None:
                if subscription.pending_tariff_plan_id != tariff_plan_id:
                    raise PaymentWorkflowError(
                        "Paid tariff invoice does not match the scheduled subscription change"
                    )
                if subscription.current_period_end is not None and period_start < _aware(
                    subscription.current_period_end, "current_period_end"
                ):
                    raise PaymentWorkflowError(
                        "Paid tariff invoice starts before the current subscription period ends"
                    )
                subscription.pending_effective_at = period_start
                subscription.pending_period_start = period_start
                subscription.pending_period_end = period_end
                subscription.pending_snapshot = subscription_snapshot
                await db.flush()
                return
            if tariff_plan_id != subscription.tariff_plan_id:
                raise PaymentWorkflowError("A future plan change must be scheduled before payment")

        if subscription is None:
            subscription = Subscription(
                id=str(uuid4()),
                account_id=invoice.account_id,
                tariff_plan_id=tariff_plan_id,
                status="active",
                trial_used=False,
                current_period_start=period_start,
                current_period_end=period_end,
                grace_until=None,
                canceled_at=None,
                snapshot=subscription_snapshot,
            )
            db.add(subscription)
        else:
            subscription.tariff_plan_id = tariff_plan_id
            subscription.status = "active"
            subscription.current_period_start = period_start
            subscription.current_period_end = period_end
            subscription.grace_until = None
            subscription.canceled_at = None
            subscription.snapshot = subscription_snapshot
        subscription.pending_tariff_plan_id = None
        subscription.pending_billing_period = None
        subscription.pending_effective_at = None
        subscription.pending_period_start = None
        subscription.pending_period_end = None
        subscription.pending_snapshot = None
        if grant_amount > _ZERO:
            await grant_dancoin(
                db,
                account_id=invoice.account_id,
                reference=f"subscription-grant:{invoice.id}",
                amount=grant_amount,
            )
    elif invoice.purchase_type == _PURCHASE_DANCOIN_PACKAGE:
        dancoin_amount = _snapshot_amount(invoice.snapshot, "dancoin_amount")
        await purchase_dancoin(
            db,
            account_id=invoice.account_id,
            reference=f"payment-purchase:{payment.id}",
            amount=dancoin_amount,
        )
    else:
        raise PaymentWorkflowError(f"Unsupported invoice purchase type {invoice.purchase_type!r}")
    await db.flush()


async def _refund_duplicate_payment(
    db: AsyncSession,
    *,
    provider: PaymentProvider,
    payment_id: str,
    event_record_id: str,
) -> PaymentEventResult:
    _require_no_transaction(db)
    async with db.begin():
        payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        event_record = await db.scalar(
            select(PaymentEventRecord)
            .where(PaymentEventRecord.id == event_record_id)
            .with_for_update()
        )
        if payment is None or event_record is None:
            raise BillingConsistencyError("Refund payment or event could not be loaded")
        if payment.status == "refunded":
            event_record.processing_status = "processed"
            event_record.processed_at = datetime.now(UTC)
            return PaymentEventResult(status="processed", payment_id=payment.id, duplicate=True)
        if payment.status != "refund_pending" or payment.external_payment_id is None:
            raise BillingConsistencyError("Duplicate payment is missing its provider refund state")
        request = {
            "payment_id": payment.id,
            "external_payment_id": payment.external_payment_id,
            "amount": payment.amount,
            "currency": payment.currency,
            "idempotency_key": f"duplicate-payment-refund:{payment.id}",
        }

    refund = await provider.refund_payment(**request)
    if (
        not refund.external_refund_id
        or refund.amount != request["amount"]
        or refund.currency != request["currency"]
    ):
        raise PaymentProviderMismatchError("Provider refund does not match the duplicate payment")
    if refund.status not in {"succeeded", "pending"}:
        raise UnsupportedProviderStatusError(
            f"Unsupported provider refund status {refund.status!r}"
        )

    async with db.begin():
        payment = await db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
        event_record = await db.scalar(
            select(PaymentEventRecord)
            .where(PaymentEventRecord.id == event_record_id)
            .with_for_update()
        )
        if payment is None or event_record is None:
            raise BillingConsistencyError("Refund state could not be reloaded")
        invoice = await db.scalar(
            select(Invoice).where(Invoice.id == payment.invoice_id).with_for_update()
        )
        if invoice is None:
            raise BillingConsistencyError("Refund invoice could not be reloaded")
        if refund.status == "succeeded":
            payment.status = "refunded"
            other_succeeded = await db.scalar(
                select(Payment.id).where(
                    Payment.invoice_id == invoice.id,
                    Payment.status == "succeeded",
                    Payment.id != payment.id,
                )
            )
            if other_succeeded is None and invoice.status != "paid":
                invoice.status = "refunded"
            event_record.processing_status = "processed"
            event_record.processed_at = datetime.now(UTC)
            result_status = "processed"
        else:
            event_record.processing_status = "refund_pending"
            result_status = "refund_pending"
        event_record.payload = {
            **(event_record.payload or {}),
            "external_refund_id": refund.external_refund_id,
            "refund_status": refund.status,
        }
        await db.flush()
    return PaymentEventResult(status=result_status, payment_id=payment_id)
