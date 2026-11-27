from typing import Any
from uuid import UUID

from fastapi import APIRouter, Response, status

from ai_seller_core.api.dependencies import (
    AccountMemberReader,
    AccountMemberWriter,
    DatabaseSession,
    IdempotencyKey,
)
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    AccountMemberCreateRequest,
    AccountMemberListResponse,
    AccountMemberRead,
    AccountMemberUpdateRequest,
)
from ai_seller_core.persistence.access_policy import AccountRole
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.membership_service import (
    AccessStatus,
    add_member,
    change_member_role,
    list_account_memberships,
    remove_member,
    set_member_status,
)
from ai_seller_core.persistence.models import AccountMembership

router = APIRouter(
    prefix="/account/members",
    tags=["account"],
    responses=COMMON_ERROR_RESPONSES,
)


def _member_response(membership: AccountMembership) -> dict[str, Any]:
    return AccountMemberRead(
        user_id=membership.user_id,
        role=membership.role,
        status=membership.status,
    ).model_dump(mode="json")


@router.get(
    "",
    response_model=AccountMemberListResponse,
    summary="List account members",
)
async def list_members(
    principal: AccountMemberReader,
    session: DatabaseSession,
) -> AccountMemberListResponse:
    async with session.begin():
        memberships = await list_account_memberships(
            session,
            account_id=principal.account_id,
            actor_user_id=principal.user_id,
        )
    return AccountMemberListResponse(
        items=[AccountMemberRead.model_validate(_member_response(member)) for member in memberships]
    )


@router.post(
    "",
    response_model=AccountMemberRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add an account member",
)
async def create_member(
    body: AccountMemberCreateRequest,
    principal: AccountMemberWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AccountMemberRead:
    async def action() -> dict[str, Any]:
        membership = await add_member(
            session,
            account_id=principal.account_id,
            actor_user_id=principal.user_id,
            target_user_id=str(body.user_id),
            role=AccountRole(body.role),
        )
        return _member_response(membership)

    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="account.members.add",
            key=idempotency_key,
            request_data=body.model_dump(mode="json"),
            action=action,
        )
    return AccountMemberRead.model_validate(response_data)


@router.patch(
    "/{user_id}",
    response_model=AccountMemberRead,
    summary="Change an account member role or status",
)
async def update_member(
    user_id: UUID,
    body: AccountMemberUpdateRequest,
    principal: AccountMemberWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AccountMemberRead:
    async def action() -> dict[str, Any]:
        if body.role is not None:
            membership = await change_member_role(
                session,
                account_id=principal.account_id,
                actor_user_id=principal.user_id,
                target_user_id=str(user_id),
                role=AccountRole(body.role),
            )
        elif body.status is not None:
            membership = await set_member_status(
                session,
                account_id=principal.account_id,
                actor_user_id=principal.user_id,
                target_user_id=str(user_id),
                status=AccessStatus(body.status),
            )
        else:
            raise RuntimeError("Validated account member update has no change")
        return _member_response(membership)

    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="account.members.update",
            key=idempotency_key,
            request_data={
                "user_id": str(user_id),
                **body.model_dump(mode="json", exclude_unset=True),
            },
            action=action,
        )
    return AccountMemberRead.model_validate(response_data)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an account member",
)
async def delete_member(
    user_id: UUID,
    principal: AccountMemberWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> Response:
    async def action() -> dict[str, Any]:
        await remove_member(
            session,
            account_id=principal.account_id,
            actor_user_id=principal.user_id,
            target_user_id=str(user_id),
        )
        return {"removed": True, "user_id": str(user_id)}

    async with session.begin():
        await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation="account.members.remove",
            key=idempotency_key,
            request_data={"user_id": str(user_id)},
            action=action,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
