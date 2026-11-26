from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from ai_seller_core.api.dependencies import DatabaseSession, ProjectReader
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    ProjectListQuery,
    ProjectListResponse,
    ProjectRead,
)
from ai_seller_core.persistence.access_policy import (
    AuthorizationError,
    ProjectPermission,
    can_access_project,
)
from ai_seller_core.persistence.authorization_service import require_project_access
from ai_seller_core.persistence.models import Project

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses=COMMON_ERROR_RESPONSES,
)


@router.get("", response_model=ProjectListResponse, summary="List current account projects")
async def list_projects(
    principal: ProjectReader,
    session: DatabaseSession,
    filters: Annotated[ProjectListQuery, Query()],
) -> ProjectListResponse:
    if not can_access_project(principal.role, ProjectPermission.READ):
        raise AuthorizationError("Role is not authorized to access projects")

    statement = select(Project).where(Project.account_id == principal.account_id)
    if filters.status is not None:
        statement = statement.where(Project.status == filters.status)
    statement = statement.order_by(Project.id)
    result = await session.scalars(statement)
    return ProjectListResponse(
        items=[ProjectRead.model_validate(project) for project in result.all()]
    )


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Get project",
)
async def get_project(
    project_id: UUID,
    principal: ProjectReader,
    session: DatabaseSession,
) -> ProjectRead:
    project = await require_project_access(
        session,
        principal,
        str(project_id),
        permission=ProjectPermission.READ,
    )
    return ProjectRead.model_validate(project)
