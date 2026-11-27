from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from ai_seller_core.persistence.billing_models import Payment
from ai_seller_core.persistence.payment_service import (
    PaymentProviderRequest,
    ProviderPayment,
    ProviderPaymentEvent,
    ProviderRefund,
)


class FakePaymentProvider:
    name = "fake"

    def __init__(self) -> None:
        self.payments: dict[str, ProviderPayment] = {}
        self.payment_keys: dict[str, str] = {}
        self.refunds: dict[str, ProviderRefund] = {}

    def parse_event(self, payload: dict[str, object]) -> ProviderPaymentEvent:
        required = (
            "event_id",
            "payment_id",
            "external_payment_id",
            "invoice_id",
            "amount",
            "currency",
            "event_type",
            "status",
        )
        if any(key not in payload for key in required):
            raise ValueError("Fake payment event is missing required fields")
        try:
            amount = Decimal(str(payload["amount"]))
        except (ArithmeticError, ValueError) as error:
            raise ValueError("Fake payment event amount is invalid") from error
        return ProviderPaymentEvent(
            provider=self.name,
            provider_event_id=str(payload["event_id"]),
            payment_id=str(payload["payment_id"]),
            external_payment_id=str(payload["external_payment_id"]),
            invoice_id=str(payload["invoice_id"]),
            amount=amount,
            currency=str(payload["currency"]),
            event_type=str(payload["event_type"]),
            provider_status=str(payload["status"]),
            occurred_at=datetime.now(UTC),
            payload=payload,
        )

    async def create_payment(self, request: PaymentProviderRequest) -> ProviderPayment:
        existing_id = self.payment_keys.get(request.idempotency_key)
        if existing_id is not None:
            existing = self.payments[existing_id]
            if (
                existing.payment_id != request.payment_id
                or existing.invoice_id != request.invoice_id
                or existing.amount != request.amount
                or existing.currency != request.currency
            ):
                raise ValueError("Fake payment idempotency key was reused with different terms")
            return existing
        payment = ProviderPayment(
            payment_id=request.payment_id,
            external_payment_id=f"fake-{request.payment_id}",
            invoice_id=request.invoice_id,
            amount=request.amount,
            currency=request.currency,
            status="pending",
        )
        self.payment_keys[request.idempotency_key] = request.payment_id
        self.payments[request.payment_id] = payment
        return payment

    async def verify_payment(self, event: ProviderPaymentEvent) -> ProviderPayment:
        payment = self.payments.get(event.payment_id)
        if payment is None:
            raise ValueError("Fake payment event refers to an unknown payment")
        return payment

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
            if existing.amount != amount or existing.currency != currency:
                raise ValueError("Fake refund idempotency key was reused with different terms")
            return existing
        payment = self.payments.get(payment_id)
        if payment is None or payment.external_payment_id != external_payment_id:
            raise ValueError("Fake refund refers to an unknown payment")
        refund = ProviderRefund(
            external_refund_id=f"fake-refund-{payment_id}",
            amount=amount,
            currency=currency,
            status="succeeded",
        )
        self.refunds[idempotency_key] = refund
        return refund

    def set_status(self, payment_id: str, status: str) -> None:
        if status not in {"pending", "succeeded", "canceled", "failed"}:
            raise ValueError("Unsupported fake payment status")
        payment = self.payments.get(payment_id)
        if payment is None:
            raise ValueError("Unknown fake payment")
        self.payments[payment_id] = replace(
            payment,
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
        provider_payment = self.payments.get(payment.id)
        if provider_payment is None:
            raise ValueError("Unknown fake payment")
        return ProviderPaymentEvent(
            provider=self.name,
            provider_event_id=event_id,
            payment_id=payment.id,
            external_payment_id=provider_payment.external_payment_id,
            invoice_id=invoice_id or payment.invoice_id,
            amount=amount if amount is not None else payment.amount,
            currency=currency if currency is not None else payment.currency,
            event_type=event_type,
            provider_status=status,
            occurred_at=datetime.now(UTC),
            payload={"source": "fake-payment-provider"},
        )
