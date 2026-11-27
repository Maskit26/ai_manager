from fastapi import APIRouter

from ai_seller_core.api.v1.account import router as account_router
from ai_seller_core.api.v1.agents import router as agents_router
from ai_seller_core.api.v1.dialogs import router as dialogs_router
from ai_seller_core.api.v1.members import router as members_router
from ai_seller_core.api.v1.onboarding import router as onboarding_router
from ai_seller_core.api.v1.projects import router as projects_router
from ai_seller_core.api.v1.schemas import COMMON_ERROR_RESPONSES

router = APIRouter(
    prefix="/api/v1",
    responses=COMMON_ERROR_RESPONSES,
)
router.include_router(account_router)
router.include_router(members_router)
router.include_router(onboarding_router)
router.include_router(projects_router)
router.include_router(agents_router)
router.include_router(dialogs_router)
