from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.api.auth import get_current_principal
from ai_seller_core.core.config import get_settings
from ai_seller_core.persistence.access_policy import AccountRole, AuthorizationError, Principal
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.fake_payment_provider import FakePaymentProvider
from ai_seller_core.persistence.payment_service import (
    PaymentProvider,
    PaymentProviderNotConfiguredError,
)

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
AccountReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["account:read"]),
]
ProjectReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["projects:read"]),
]
ProjectManager = Annotated[
    Principal,
    Security(get_current_principal, scopes=["projects:write"]),
]
AccountMemberReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["members:read"]),
]
AccountMemberWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["members:write"]),
]
AgentReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["agents:read"]),
]
AgentWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["agents:write"]),
]
DialogReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["dialogs:read"]),
]
DialogWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["dialogs:write"]),
]
BillingReader = Annotated[
    Principal,
    Security(get_current_principal, scopes=["billing:read"]),
]
BillingWriter = Annotated[
    Principal,
    Security(get_current_principal, scopes=["billing:write"]),
]


def require_billing_admin(principal: BillingWriter) -> Principal:
    if principal.role not in {AccountRole.OWNER, AccountRole.ADMIN}:
        raise AuthorizationError("Billing write access is restricted to account owners and admins")
    return principal


BillingAdminWriter = Annotated[Principal, Depends(require_billing_admin)]


def get_payment_provider(request: Request) -> PaymentProvider:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    provider = getattr(request.app.state, "payment_provider", None)
    if provider is not None:
        if isinstance(provider, FakePaymentProvider) and settings.app_env not in {
            "local",
            "development",
            "test",
        }:
            raise PaymentProviderNotConfiguredError(
                "The fake payment provider is not allowed in this deployment profile"
            )
        return provider
    if settings.app_env in {"local", "development", "test"}:
        provider = FakePaymentProvider()
        request.app.state.payment_provider = provider
        return provider
    raise PaymentProviderNotConfiguredError(
        "No payment provider is configured for this deployment profile"
    )


BillingPaymentProvider = Annotated[PaymentProvider, Depends(get_payment_provider)]


def require_idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ],
) -> str:
    if not value.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be empty",
        )
    return value


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]
