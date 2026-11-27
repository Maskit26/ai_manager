from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, desc, or_, select

from ai_seller_core.api.dependencies import (
    BillingAdminWriter,
    BillingPaymentProvider,
    BillingReader,
    DatabaseSession,
    IdempotencyKey,
)
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    BillingEntitlementsRead,
    BillingModelRead,
    BillingModelsResponse,
    BillingPendingChangeRead,
    BillingPlanRead,
    BillingPlansResponse,
    BillingSubscriptionRead,
    BillingSubscriptionResponse,
    CheckoutRequest,
    CheckoutResponse,
    DancoinBalanceRead,
    DancoinPackageRead,
    DancoinPurchaseRequest,
    DancoinResponse,
    DancoinTransactionRead,
    InvoicePageResponse,
    InvoiceRead,
    RefundRequestBody,
    RefundRequestRead,
    SubscriptionChangeRequest,
    SubscriptionChangeResponse,
    UsagePageResponse,
    UsageRecordRead,
)
from ai_seller_core.persistence.billing_models import (
    DancoinBalance,
    DancoinPackage,
    DancoinTransaction,
    Invoice,
    LlmModel,
    Subscription,
    TariffPlan,
    UsageRecord,
)
from ai_seller_core.persistence.entitlements_service import get_entitlements
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.payment_service import (
    SubscriptionChangeConflictError,
    add_billing_months,
    create_dancoin_package_invoice,
    create_payment,
    create_refund_request,
    create_tariff_invoice,
    get_tariff_billing_period,
    schedule_subscription_change,
    tariff_billing_periods,
)

router = APIRouter(tags=["billing"], responses=COMMON_ERROR_RESPONSES)
_CURRENT_SUBSCRIPTION_STATUSES = ("trial", "active", "grace_period", "parking")


@router.get(
    "/billing/plans",
    response_model=BillingPlansResponse,
    description=(
        "Возвращает активные тарифные планы и пакеты Dancoin из каталога. "
        "Используется интерфейсом для отображения условий подписки и вариантов пополнения баланса."
    ),
)
async def list_billing_plans(
    session: DatabaseSession,
    _principal: BillingReader,
) -> BillingPlansResponse:
    async with session.begin():
        plans = (
            await session.scalars(
                select(TariffPlan)
                .where(TariffPlan.is_active.is_(True))
                .order_by(TariffPlan.code, TariffPlan.version)
            )
        ).all()
        packages = (
            await session.scalars(
                select(DancoinPackage)
                .where(DancoinPackage.is_active.is_(True))
                .order_by(DancoinPackage.code, DancoinPackage.version)
            )
        ).all()
    return BillingPlansResponse(
        plans=[
            BillingPlanRead(
                id=plan.id,
                code=plan.code,
                version=plan.version,
                name=plan.name,
                included_agents=plan.included_agents,
                max_agents=plan.max_agents,
                features=plan.features,
                limits=plan.limits,
                billing_periods=tariff_billing_periods(plan),
            )
            for plan in plans
        ],
        dancoin_packages=[
            DancoinPackageRead(
                id=package.id,
                code=package.code,
                version=package.version,
                name=package.name,
                dancoin_amount=package.dancoin_amount,
                price_amount=package.price_amount,
                currency=package.currency,
            )
            for package in packages
        ],
    )


@router.get(
    "/billing/subscription",
    response_model=BillingSubscriptionResponse,
    description=(
        "Возвращает данные о подписке аккаунта и доступных ему возможностях. "
        "Если активной подписки нет, возвращается последняя отменённая подписка."
    ),
)
async def read_subscription(
    session: DatabaseSession,
    principal: BillingReader,
) -> BillingSubscriptionResponse:
    async with session.begin():
        entitlements = await get_entitlements(session, account_id=principal.account_id)
        subscription = await session.scalar(
            select(Subscription)
            .where(
                Subscription.account_id == principal.account_id,
                Subscription.status.in_(_CURRENT_SUBSCRIPTION_STATUSES),
            )
            .order_by(desc(Subscription.updated_at))
            .limit(1)
        )
        if subscription is None:
            subscription = await session.scalar(
                select(Subscription)
                .where(
                    Subscription.account_id == principal.account_id,
                    Subscription.status == "canceled",
                )
                .order_by(desc(Subscription.updated_at))
                .limit(1)
            )
        current_plan = (
            await session.get(TariffPlan, subscription.tariff_plan_id)
            if subscription is not None
            else None
        )
    subscription_read = None
    if subscription is not None:
        if current_plan is None:
            raise RuntimeError("Subscription refers to a missing tariff plan")
        pending_change = None
        if (
            subscription.pending_tariff_plan_id is not None
            and subscription.pending_billing_period is not None
            and subscription.pending_effective_at is not None
        ):
            pending_change = BillingPendingChangeRead(
                tariff_plan_id=subscription.pending_tariff_plan_id,
                billing_period_code=subscription.pending_billing_period,
                effective_at=subscription.pending_effective_at,
                status="paid" if subscription.pending_snapshot is not None else "awaiting_payment",
            )
        subscription_read = BillingSubscriptionRead(
            id=subscription.id,
            tariff_plan_id=subscription.tariff_plan_id,
            tariff_plan_code=current_plan.code,
            tariff_plan_name=current_plan.name,
            status=subscription.status,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            grace_until=subscription.grace_until,
            pending_change=pending_change,
        )
    return BillingSubscriptionResponse(
        subscription=subscription_read,
        entitlements=BillingEntitlementsRead(
            subscription_status=entitlements.subscription_status,
            current_period_end=entitlements.current_period_end,
            grace_until=entitlements.grace_until,
            features=entitlements.features,
            limits=entitlements.limits,
            available_dancoin=entitlements.available_dancoin,
            included_agents=entitlements.included_agents,
            max_agents=entitlements.max_agents,
        ),
    )


@router.get(
    "/billing/dancoin",
    response_model=DancoinResponse,
    description=(
        "Возвращает баланс Dancoin и историю транзакций аккаунта. "
        "Поддерживает курсорную пагинацию для загрузки истории частями."
    ),
)
async def read_dancoin(
    session: DatabaseSession,
    principal: BillingReader,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> DancoinResponse:
    cursor_values = _decode_cursor(cursor) if cursor is not None else None
    async with session.begin():
        entitlements = await get_entitlements(session, account_id=principal.account_id)
        balance = await session.get(DancoinBalance, principal.account_id)
        statement = select(DancoinTransaction).where(
            DancoinTransaction.account_id == principal.account_id
        )
        if cursor_values is not None:
            statement = statement.where(
                _cursor_clause(
                    DancoinTransaction.occurred_at,
                    DancoinTransaction.id,
                    cursor_values,
                )
            )
        transactions = (
            await session.scalars(
                statement.order_by(
                    desc(DancoinTransaction.occurred_at),
                    desc(DancoinTransaction.id),
                ).limit(limit + 1)
            )
        ).all()
    has_more = len(transactions) > limit
    page = transactions[:limit]
    return DancoinResponse(
        balance=DancoinBalanceRead(
            grant_balance=balance.grant_balance if balance is not None else Decimal("0.00"),
            purchased_balance=(
                balance.purchased_balance if balance is not None else Decimal("0.00")
            ),
            reserved_grant=balance.reserved_grant if balance is not None else Decimal("0.00"),
            reserved_purchased=(
                balance.reserved_purchased if balance is not None else Decimal("0.00")
            ),
            debt_balance=balance.debt_balance if balance is not None else Decimal("0.00"),
            available=entitlements.available_dancoin,
        ),
        transactions=[
            DancoinTransactionRead(
                id=transaction.id,
                type=transaction.type,
                source=transaction.source,
                amount=transaction.amount,
                balance_after=transaction.balance_after,
                occurred_at=transaction.occurred_at,
            )
            for transaction in page
        ],
        next_cursor=(
            _encode_cursor(page[-1].occurred_at, page[-1].id) if has_more and page else None
        ),
    )


@router.post(
    "/billing/subscription/change",
    response_model=SubscriptionChangeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Планирует смену тарифного плана подписки на выбранный период. "
        "Новые условия вступают в силу с начала указанного оплаченного периода."
    ),
)
async def change_subscription(
    body: SubscriptionChangeRequest,
    session: DatabaseSession,
    principal: BillingAdminWriter,
    idempotency_key: IdempotencyKey,
    _provider: BillingPaymentProvider,
) -> SubscriptionChangeResponse:
    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="billing.subscription.change",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=_schedule_change_action(
                session,
                principal.account_id,
                principal.user_id,
                body,
            ),
        )
    return SubscriptionChangeResponse.model_validate(response_data)


@router.post(
    "/billing/checkout",
    response_model=CheckoutResponse,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Создаёт счёт и инициирует оплату подписки на выбранный тариф. "
        "Изменение подписки выполняется после подтверждения платежа."
    ),
)
async def checkout_subscription(
    body: CheckoutRequest,
    session: DatabaseSession,
    principal: BillingAdminWriter,
    idempotency_key: IdempotencyKey,
    provider: BillingPaymentProvider,
) -> CheckoutResponse:
    provider_key = _provider_idempotency_key("checkout", idempotency_key)
    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="billing.checkout",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=_checkout_invoice_action(
                session,
                principal.account_id,
                body,
                provider_key,
            ),
        )
    payment = await create_payment(
        session,
        provider=provider,
        account_id=principal.account_id,
        invoice_id=response_data["invoice_id"],
        idempotency_key=provider_key,
    )
    return CheckoutResponse(
        invoice_id=response_data["invoice_id"],
        invoice_number=response_data["invoice_number"],
        payment_id=payment.id,
        provider=payment.provider,
        payment_status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
    )


@router.post(
    "/billing/dancoin/purchase",
    response_model=CheckoutResponse,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Создаёт счёт и инициирует оплату выбранного активного пакета Dancoin. "
        "Баланс пополняется после подтверждения платежа."
    ),
)
async def purchase_dancoin(
    body: DancoinPurchaseRequest,
    session: DatabaseSession,
    principal: BillingAdminWriter,
    idempotency_key: IdempotencyKey,
    provider: BillingPaymentProvider,
) -> CheckoutResponse:
    provider_key = _provider_idempotency_key("dancoin", idempotency_key)

    async def action() -> dict[str, Any]:
        package = await session.scalar(
            select(DancoinPackage).where(
                DancoinPackage.id == str(body.package_id),
                DancoinPackage.is_active.is_(True),
            )
        )
        if package is None:
            raise LookupError("Dancoin package does not exist")
        invoice = await create_dancoin_package_invoice(
            session,
            account_id=principal.account_id,
            package=package,
            number=f"INV-{uuid4().hex.upper()}",
            idempotency_key=provider_key,
        )
        return {"invoice_id": invoice.id, "invoice_number": invoice.number}

    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="billing.dancoin.purchase",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=action,
        )
    payment = await create_payment(
        session,
        provider=provider,
        account_id=principal.account_id,
        invoice_id=response_data["invoice_id"],
        idempotency_key=provider_key,
    )
    return CheckoutResponse(
        invoice_id=response_data["invoice_id"],
        invoice_number=response_data["invoice_number"],
        payment_id=payment.id,
        provider=payment.provider,
        payment_status=payment.status,
        amount=payment.amount,
        currency=payment.currency,
    )


@router.post(
    "/billing/refund-requests",
    response_model=RefundRequestRead,
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Регистрирует запрос на возврат средств по указанному платежу и "
        "сохраняет причину обращения. "
        "Сам возврат этим запросом не проводится, решение обрабатывается отдельно."
    ),
)
async def request_refund(
    body: RefundRequestBody,
    session: DatabaseSession,
    principal: BillingAdminWriter,
    idempotency_key: IdempotencyKey,
) -> RefundRequestRead:
    async def action() -> dict[str, Any]:
        refund_request = await create_refund_request(
            session,
            account_id=principal.account_id,
            payment_id=str(body.payment_id),
            requested_by=principal.user_id,
            reason=body.reason,
        )
        return {
            "id": refund_request.id,
            "payment_id": refund_request.payment_id,
            "status": refund_request.status,
            "created_at": refund_request.created_at.isoformat(),
        }

    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="billing.refund_request",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=action,
        )
    return RefundRequestRead.model_validate(response_data)


@router.get(
    "/billing/invoices",
    response_model=InvoicePageResponse,
    description=(
        "Возвращает счета текущего аккаунта с суммой, валютой, периодом и статусом. "
        "Поддерживает курсорную пагинацию для просмотра истории счетов."
    ),
)
async def list_invoices(
    session: DatabaseSession,
    principal: BillingReader,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
) -> InvoicePageResponse:
    cursor_values = _decode_cursor(cursor) if cursor is not None else None
    async with session.begin():
        statement = select(Invoice).where(Invoice.account_id == principal.account_id)
        if cursor_values is not None:
            statement = statement.where(
                _cursor_clause(Invoice.created_at, Invoice.id, cursor_values)
            )
        invoices = (
            await session.scalars(
                statement.order_by(desc(Invoice.created_at), desc(Invoice.id)).limit(limit + 1)
            )
        ).all()
    has_more = len(invoices) > limit
    page = invoices[:limit]
    return InvoicePageResponse(
        items=[
            InvoiceRead(
                id=invoice.id,
                number=invoice.number,
                purchase_type=invoice.purchase_type,
                status=invoice.status,
                total=invoice.total,
                currency=invoice.currency,
                period_start=invoice.period_start,
                period_end=invoice.period_end,
                created_at=invoice.created_at,
            )
            for invoice in page
        ],
        next_cursor=_encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None,
    )


@router.get(
    "/billing/usage",
    response_model=UsagePageResponse,
    description=(
        "Возвращает записи потребления ресурсов аккаунта за заданный временной период. "
        "Поддерживает фильтрацию по датам и постраничную загрузку результатов."
    ),
)
async def list_usage(
    session: DatabaseSession,
    principal: BillingReader,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    since: datetime | None = None,
    until: datetime | None = None,
) -> UsagePageResponse:
    if since is not None and (since.tzinfo is None or since.utcoffset() is None):
        raise HTTPException(status_code=422, detail="since must include a timezone")
    if until is not None and (until.tzinfo is None or until.utcoffset() is None):
        raise HTTPException(status_code=422, detail="until must include a timezone")
    if since is not None and until is not None and since > until:
        raise HTTPException(status_code=422, detail="since must not be after until")
    cursor_values = _decode_cursor(cursor) if cursor is not None else None
    async with session.begin():
        statement = select(UsageRecord).where(UsageRecord.account_id == principal.account_id)
        if since is not None:
            statement = statement.where(UsageRecord.occurred_at >= since)
        if until is not None:
            statement = statement.where(UsageRecord.occurred_at < until)
        if cursor_values is not None:
            statement = statement.where(
                _cursor_clause(UsageRecord.occurred_at, UsageRecord.id, cursor_values)
            )
        usage_records = (
            await session.scalars(
                statement.order_by(
                    desc(UsageRecord.occurred_at),
                    desc(UsageRecord.id),
                ).limit(limit + 1)
            )
        ).all()
    has_more = len(usage_records) > limit
    page = usage_records[:limit]
    return UsagePageResponse(
        items=[
            UsageRecordRead(
                id=record.id,
                project_id=record.project_id,
                metric=record.metric,
                quantity=record.quantity,
                unit=record.unit,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                model_id=record.model_id,
                price_version=record.price_version,
                dancoin_cost=record.dancoin_cost,
                period_start=record.period_start,
                period_end=record.period_end,
                occurred_at=record.occurred_at,
            )
            for record in page
        ],
        next_cursor=(
            _encode_cursor(page[-1].occurred_at, page[-1].id) if has_more and page else None
        ),
    )


@router.get(
    "/billing/models",
    response_model=BillingModelsResponse,
    description=(
        "Возвращает активные языковые модели и сведения об их стоимости. "
        "Ответ включает цены за входные и выходные токены и доступные ограничения контекста."
    ),
)
async def list_billing_models(
    session: DatabaseSession,
    _principal: BillingReader,
) -> BillingModelsResponse:
    async with session.begin():
        models = (
            await session.scalars(
                select(LlmModel)
                .where(LlmModel.is_active.is_(True))
                .order_by(LlmModel.model_id, LlmModel.version)
            )
        ).all()
    return BillingModelsResponse(
        items=[
            BillingModelRead(
                model_id=model.model_id,
                version=model.version,
                name=model.name,
                input_price_per_1k=model.input_price_per_1k,
                output_price_per_1k=model.output_price_per_1k,
                long_context_threshold_tokens=model.long_context_threshold_tokens,
                long_context_input_price_per_1k=model.long_context_input_price_per_1k,
                long_context_output_price_per_1k=model.long_context_output_price_per_1k,
                capabilities=model.capabilities,
            )
            for model in models
        ]
    )


def _schedule_change_action(
    session: DatabaseSession,
    account_id: str,
    user_id: str,
    body: SubscriptionChangeRequest,
):
    async def action() -> dict[str, Any]:
        subscription = await schedule_subscription_change(
            session,
            account_id=account_id,
            tariff_plan_id=str(body.tariff_plan_id),
            billing_period=body.billing_period_code,
            actor_id=user_id,
        )
        if subscription.pending_effective_at is None:
            raise RuntimeError("Scheduled subscription change has no effective time")
        return {
            "status": "scheduled",
            "tariff_plan_id": subscription.pending_tariff_plan_id,
            "billing_period_code": subscription.pending_billing_period,
            "effective_at": subscription.pending_effective_at.isoformat(),
        }

    return action


def _checkout_invoice_action(
    session: DatabaseSession,
    account_id: str,
    body: CheckoutRequest,
    idempotency_key: str,
):
    async def action() -> dict[str, Any]:
        plan = await session.scalar(
            select(TariffPlan).where(
                TariffPlan.id == str(body.tariff_plan_id),
                TariffPlan.is_active.is_(True),
            )
        )
        if plan is None:
            raise LookupError("Tariff plan does not exist")
        period = get_tariff_billing_period(plan, body.billing_period_code)
        now = datetime.now(UTC)
        subscription = await session.scalar(
            select(Subscription)
            .where(
                Subscription.account_id == account_id,
                Subscription.status.in_(_CURRENT_SUBSCRIPTION_STATUSES),
            )
            .with_for_update()
        )
        if subscription is None:
            period_start = now
        else:
            if subscription.pending_snapshot is not None:
                raise SubscriptionChangeConflictError(
                    "A paid subscription change is already pending activation"
                )
            if subscription.pending_tariff_plan_id is not None:
                if (
                    subscription.pending_tariff_plan_id != plan.id
                    or subscription.pending_billing_period != body.billing_period_code
                ):
                    raise SubscriptionChangeConflictError(
                        "Checkout does not match the scheduled subscription change"
                    )
                scheduled_at = (
                    subscription.pending_effective_at or subscription.current_period_end or now
                )
                period_start = max(scheduled_at, now)
            elif (
                subscription.current_period_end is not None
                and subscription.current_period_end > now
            ):
                if plan.id != subscription.tariff_plan_id:
                    raise SubscriptionChangeConflictError(
                        "Schedule a plan change before checking out a different tariff"
                    )
                period_start = subscription.current_period_end
                subscription.pending_tariff_plan_id = plan.id
                subscription.pending_billing_period = body.billing_period_code
                subscription.pending_effective_at = period_start
            else:
                period_start = now
        period_end = add_billing_months(period_start, period.months)
        invoice = await create_tariff_invoice(
            session,
            account_id=account_id,
            tariff_plan=plan,
            number=f"INV-{uuid4().hex.upper()}",
            amount=period.amount,
            currency=period.currency,
            period_start=period_start,
            period_end=period_end,
            idempotency_key=idempotency_key,
            dancoin_grant_amount=period.dancoin_grant_amount,
        )
        return {"invoice_id": invoice.id, "invoice_number": invoice.number}

    return action


def _provider_idempotency_key(operation: str, key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"billing:{operation}:{digest}"


def _encode_cursor(timestamp: datetime, record_id: str) -> str:
    value = f"{timestamp.astimezone(UTC).isoformat()}|{record_id}".encode()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        value = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8")
        timestamp_value, record_id = value.split("|", maxsplit=1)
        timestamp = datetime.fromisoformat(timestamp_value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="Invalid cursor") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None or not record_id:
        raise HTTPException(status_code=422, detail="Invalid cursor")
    return timestamp.astimezone(UTC), record_id


def _cursor_clause(timestamp_column: Any, id_column: Any, cursor: tuple[datetime, str]):
    timestamp, record_id = cursor
    return or_(
        timestamp_column < timestamp,
        and_(timestamp_column == timestamp, id_column < record_id),
    )
