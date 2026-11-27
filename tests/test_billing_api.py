import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ai_seller_core.api.dependencies import get_payment_provider, require_billing_admin
from ai_seller_core.api.error_handlers import register_exception_handlers
from ai_seller_core.api.v1.router import router
from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.persistence.access_policy import AccountRole, AuthorizationError, Principal
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.billing_models import (
    Invoice,
    Payment,
    Subscription,
    TariffPlan,
)
from ai_seller_core.persistence.entitlements_service import _activate_due_subscription_change
from ai_seller_core.persistence.fake_payment_provider import FakePaymentProvider
from ai_seller_core.persistence.payment_service import (
    BillingCatalogConfigurationError,
    PaymentProviderNotConfiguredError,
    PaymentProviderRequest,
    _apply_success,
    get_tariff_billing_period,
    tariff_billing_periods,
)


def test_billing_routes_are_registered() -> None:
    app = FastAPI()
    app.include_router(router)
    paths = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if path.startswith("/api/v1/billing/")
    }

    assert paths == {
        ("/api/v1/billing/plans", "GET"),
        ("/api/v1/billing/subscription", "GET"),
        ("/api/v1/billing/dancoin", "GET"),
        ("/api/v1/billing/subscription/change", "POST"),
        ("/api/v1/billing/checkout", "POST"),
        ("/api/v1/billing/dancoin/purchase", "POST"),
        ("/api/v1/billing/refund-requests", "POST"),
        ("/api/v1/billing/invoices", "GET"),
        ("/api/v1/billing/usage", "GET"),
        ("/api/v1/billing/models", "GET"),
    }


def test_billing_periods_use_configured_catalog_values() -> None:
    plan = TariffPlan(
        id=str(uuid4()),
        billing_options={
            "periods": [
                {
                    "code": "quarterly",
                    "months": 3,
                    "amount": "37.50",
                    "currency": "USD",
                    "dancoin_grant_amount": "8.00",
                }
            ]
        },
    )

    periods = tariff_billing_periods(plan)
    selected = get_tariff_billing_period(plan, "quarterly")

    assert periods == [selected]
    assert (selected.months, selected.amount, selected.currency) == (
        3,
        Decimal("37.50"),
        "USD",
    )
    assert selected.dancoin_grant_amount == Decimal("8.00")


def test_billing_periods_reject_duplicate_codes() -> None:
    plan = TariffPlan(
        id=str(uuid4()),
        billing_options={
            "periods": [
                {
                    "code": "monthly",
                    "months": 1,
                    "amount": "10.00",
                    "currency": "USD",
                },
                {
                    "code": "monthly",
                    "months": 12,
                    "amount": "100.00",
                    "currency": "USD",
                },
            ]
        },
    )

    with pytest.raises(BillingCatalogConfigurationError):
        tariff_billing_periods(plan)


@pytest.mark.anyio
async def test_fake_payment_starts_pending_and_verifies_simulated_success() -> None:
    provider = FakePaymentProvider()
    request = PaymentProviderRequest(
        payment_id=str(uuid4()),
        invoice_id=str(uuid4()),
        amount=Decimal("12.00"),
        currency="USD",
        idempotency_key="test-key",
    )
    payment = Payment(
        id=request.payment_id,
        account_id=str(uuid4()),
        invoice_id=request.invoice_id,
        provider=provider.name,
        idempotency_key=request.idempotency_key,
        amount=request.amount,
        currency=request.currency,
        status="pending",
    )

    created = await provider.create_payment(request)
    assert created.status == "pending"

    provider.set_status(payment.id, "succeeded")
    event = provider.event(payment, event_id="fake-event-1")
    verified = await provider.verify_payment(event)

    assert verified.status == "succeeded"
    assert event.provider == provider.name


@pytest.mark.anyio
async def test_due_paid_subscription_change_is_activated() -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    end = start + timedelta(days=30)
    subscription = Subscription(
        id=str(uuid4()),
        account_id=str(uuid4()),
        tariff_plan_id="old-plan",
        status="active",
        trial_used=False,
        current_period_start=start - timedelta(days=30),
        current_period_end=start,
        snapshot={},
        pending_tariff_plan_id="new-plan",
        pending_billing_period="monthly",
        pending_effective_at=start,
        pending_period_start=start,
        pending_period_end=end,
        pending_snapshot={
            "catalog_item": {"id": "new-plan"},
            "billing_invoice_id": str(uuid4()),
            "dancoin_grant_amount": "0.00",
        },
    )

    class _Session:
        async def scalar(self, _statement):
            return subscription

        async def flush(self):
            return None

    activated = await _activate_due_subscription_change(
        _Session(),
        account_id=subscription.account_id,
        now=datetime.now(UTC),
    )

    assert activated is True
    assert subscription.tariff_plan_id == "new-plan"
    assert subscription.current_period_start == start
    assert subscription.current_period_end == end
    assert subscription.pending_snapshot is None
    assert subscription.pending_tariff_plan_id is None


@pytest.mark.anyio
async def test_successful_future_plan_change_stays_pending_until_period_start() -> None:
    now = datetime.now(UTC)
    current_period_end = now + timedelta(days=10)
    pending_period_end = current_period_end + timedelta(days=30)
    subscription = Subscription(
        id=str(uuid4()),
        account_id=str(uuid4()),
        tariff_plan_id="old-plan",
        status="active",
        trial_used=False,
        current_period_start=now - timedelta(days=20),
        current_period_end=current_period_end,
        snapshot={},
        pending_tariff_plan_id="new-plan",
        pending_billing_period="monthly",
        pending_effective_at=current_period_end,
    )
    invoice = Invoice(
        id=str(uuid4()),
        account_id=subscription.account_id,
        number="INV-TEST",
        purchase_type="tariff",
        status="pending",
        snapshot={
            "catalog_item": {"id": "new-plan"},
            "dancoin_grant_amount": "0.00",
        },
        period_start=current_period_end,
        period_end=pending_period_end,
        currency="USD",
        total=Decimal("10.00"),
        idempotency_key="test-key",
    )

    class _Session:
        async def scalar(self, _statement):
            return subscription

        async def flush(self):
            return None

    await _apply_success(_Session(), Payment(), invoice)

    assert subscription.tariff_plan_id == "old-plan"
    assert subscription.current_period_end == current_period_end
    assert subscription.pending_snapshot["billing_invoice_id"] == invoice.id
    assert subscription.pending_period_start == current_period_end


def test_billing_writes_require_owner_or_admin() -> None:
    owner = Principal(
        user_id=str(uuid4()),
        account_id=str(uuid4()),
        role=AccountRole.OWNER,
        scopes=frozenset({"billing:write"}),
    )
    operator = Principal(
        user_id=str(uuid4()),
        account_id=owner.account_id,
        role=AccountRole.OPERATOR,
        scopes=frozenset({"billing:write"}),
    )

    assert require_billing_admin(owner) is owner
    with pytest.raises(AuthorizationError):
        require_billing_admin(operator)


def test_billing_read_requires_billing_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    client, private_key, principal = _billing_auth_client(
        monkeypatch,
        role=AccountRole.OPERATOR,
        principal_scopes=frozenset({"account:read"}),
    )
    token = _billing_token(
        private_key,
        account_id=principal.account_id,
        scopes=["account:read"],
    )

    with client:
        response = client.get(
            "/api/v1/billing/plans",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_billing_write_rejects_operator_role(monkeypatch: pytest.MonkeyPatch) -> None:
    client, private_key, principal = _billing_auth_client(
        monkeypatch,
        role=AccountRole.OPERATOR,
        principal_scopes=frozenset({"billing:write"}),
    )
    token = _billing_token(
        private_key,
        account_id=principal.account_id,
        scopes=["billing:write"],
    )

    with client:
        response = client.post(
            "/api/v1/billing/subscription/change",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "operator-write-attempt",
            },
            json={
                "tariff_plan_id": str(uuid4()),
                "billing_period_code": "monthly",
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_fake_payment_provider_is_not_available_in_production() -> None:
    app = FastAPI()
    app.state.settings = SimpleNamespace(app_env="production")
    request = Request({"type": "http", "app": app, "headers": []})

    with pytest.raises(PaymentProviderNotConfiguredError):
        get_payment_provider(request)

    app.state.settings.app_env = "test"
    assert isinstance(get_payment_provider(request), FakePaymentProvider)


def _billing_auth_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    role: AccountRole,
    principal_scopes: frozenset[str],
) -> tuple[TestClient, object, Principal]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        jwt_public_keys={"test-key": public_key},
    )
    principal = Principal(
        user_id=str(uuid4()),
        account_id=str(uuid4()),
        role=role,
        scopes=principal_scopes,
    )

    class _Session:
        @asynccontextmanager
        async def begin(self):
            yield

    async def override_session():
        yield _Session()

    async def return_principal(*_args, **_kwargs):
        return principal

    monkeypatch.setattr("ai_seller_core.api.auth.build_principal", return_principal)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app), private_key, principal


def _billing_token(private_key: object, *, account_id: str, scopes: list[str]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "ai-manager-web",
            "aud": "ai-manager-api",
            "sub": str(uuid4()),
            "account_id": account_id,
            "scopes": scopes,
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
