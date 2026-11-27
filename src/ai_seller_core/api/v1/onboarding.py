from typing import Any

from fastapi import APIRouter, Depends

from ai_seller_core.api.dependencies import DatabaseSession, IdempotencyKey
from ai_seller_core.api.onboarding_auth import require_onboarding_server_credential
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    OnboardingAccountRead,
    OnboardingAccountRequest,
)
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.membership_service import provision_account_with_owner

router = APIRouter(
    prefix="/internal/onboarding/accounts",
    tags=["onboarding"],
    dependencies=[Depends(require_onboarding_server_credential)],
    responses=COMMON_ERROR_RESPONSES,
)


@router.post(
    "",
    response_model=OnboardingAccountRead,
    summary="Provision an account and its initial owner",
)
async def provision_account(
    body: OnboardingAccountRequest,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> OnboardingAccountRead:
    async def action() -> dict[str, Any]:
        provisioned = await provision_account_with_owner(
            session,
            identity_provider=body.identity_provider,
            external_account_id=body.external_account_id,
            external_owner_id=body.external_owner_id,
        )
        return OnboardingAccountRead(
            account_id=provisioned.account_id,
            owner_user_id=provisioned.owner_user_id,
            owner_membership_created=provisioned.owner_membership_created,
        ).model_dump(mode="json")

    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope="internal:onboarding",
            operation="account.provision",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=action,
        )
    return OnboardingAccountRead.model_validate(response_data)
