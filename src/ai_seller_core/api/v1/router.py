from fastapi import APIRouter

from ai_seller_core.api.v1.account import router as account_router
from ai_seller_core.api.v1.projects import router as projects_router
from ai_seller_core.api.v1.schemas import COMMON_ERROR_RESPONSES

router = APIRouter(
    prefix="/api/v1",
    responses=COMMON_ERROR_RESPONSES,
)
router.include_router(account_router)
router.include_router(projects_router)
