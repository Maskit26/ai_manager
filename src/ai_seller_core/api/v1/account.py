from fastapi import APIRouter

from ai_seller_core.api.dependencies import AccountReader, DatabaseSession
from ai_seller_core.api.v1.schemas import COMMON_ERROR_RESPONSES, AccountRead
from ai_seller_core.persistence.models import Account

router = APIRouter(
    prefix="/account",
    tags=["account"],
    responses=COMMON_ERROR_RESPONSES,
)


@router.get(
    "",
    response_model=AccountRead,
    summary="Get current account",
    description=(
        "Возвращает сведения об аккаунте, определённом по учётным данным пользователя. "
        "Используется клиентом для отображения текущего аккаунта."
    ),
)
async def get_current_account(
    principal: AccountReader,
    session: DatabaseSession,
) -> AccountRead:
    account = await session.get(Account, principal.account_id)
    if account is None:
        raise LookupError("Account not found")
    return AccountRead.model_validate(account)
