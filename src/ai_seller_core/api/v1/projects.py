from collections.abc import Awaitable, Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status

from ai_seller_core.api.dependencies import (
    DatabaseSession,
    IdempotencyKey,
    ProjectManager,
    ProjectReader,
)
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    ProjectCreateRequest,
    ProjectDeletionRead,
    ProjectListQuery,
    ProjectListResponse,
    ProjectRead,
    ProjectUpdateRequest,
)
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.models import Project, ProjectDeletionRequest
from ai_seller_core.persistence.project_service import (
    archive_account_project,
    get_account_project,
    list_account_projects,
    restore_account_project,
)
from ai_seller_core.persistence.project_service import (
    create_project as create_project_record,
)
from ai_seller_core.persistence.project_service import (
    update_project as update_project_record,
)

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses=COMMON_ERROR_RESPONSES,
)

IdempotentAction = Callable[[], Awaitable[dict[str, Any]]]


async def _execute_project_write(
    session: DatabaseSession,
    *,
    principal: ProjectManager,
    idempotency_key: str,
    operation: str,
    request_data: dict[str, Any],
    action: IdempotentAction,
) -> dict[str, Any]:
    async with session.begin():
        response_data, _ = await execute_idempotently(
            session,
            scope=f"account:{principal.account_id}",
            operation=operation,
            key=idempotency_key,
            request_data=request_data,
            action=action,
        )
    return response_data


def _project_response(project: Project) -> dict[str, Any]:
    return ProjectRead.model_validate(project).model_dump(mode="json")


def _deletion_response(request: ProjectDeletionRequest) -> dict[str, Any]:
    return ProjectDeletionRead(
        project_id=request.project_id,
        project_status="archived",
        deletion_status=request.status,
        previous_status=request.previous_status,
        requested_at=request.requested_at,
    ).model_dump(mode="json")


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List current account projects",
    description=(
        "Возвращает проекты текущего аккаунта с фильтрацией по статусу. "
        "Поддерживает постраничную навигацию через параметры запроса."
    ),
)
async def list_projects(
    principal: ProjectReader,
    session: DatabaseSession,
    filters: Annotated[ProjectListQuery, Query()],
) -> ProjectListResponse:
    async with session.begin():
        projects = await list_account_projects(
            session,
            principal=principal,
            status=filters.status,
        )
    return ProjectListResponse(items=[ProjectRead.model_validate(project) for project in projects])


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Get project",
    description=(
        "Возвращает данные проекта по его идентификатору. "
        "Доступ предоставляется только к проектам текущего аккаунта."
    ),
)
async def get_project(
    project_id: UUID,
    principal: ProjectReader,
    session: DatabaseSession,
) -> ProjectRead:
    async with session.begin():
        project = await get_account_project(
            session,
            principal=principal,
            project_id=str(project_id),
        )
    return ProjectRead.model_validate(project)


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
    description=(
        "Создаёт проект в текущем аккаунте с заданными настройками локали и часового пояса. "
        "Используется при добавлении нового рабочего пространства."
    ),
)
async def create_project(
    body: ProjectCreateRequest,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> ProjectRead:
    async def action() -> dict[str, Any]:
        project = await create_project_record(
            session,
            principal=principal,
            locale=body.locale,
            timezone=body.timezone,
        )
        return _project_response(project)

    response_data = await _execute_project_write(
        session,
        principal=principal,
        idempotency_key=idempotency_key,
        operation="projects.create",
        request_data=body.model_dump(mode="json"),
        action=action,
    )
    return ProjectRead.model_validate(response_data)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    summary="Update a project",
    description=(
        "Обновляет настройки проекта по его идентификатору. "
        "В запросе можно передать новые значения локали и часового пояса."
    ),
)
async def update_project(
    project_id: UUID,
    body: ProjectUpdateRequest,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> ProjectRead:
    async def action() -> dict[str, Any]:
        project = await update_project_record(
            session,
            principal=principal,
            project_id=str(project_id),
            locale=body.locale,
            timezone=body.timezone,
        )
        return _project_response(project)

    response_data = await _execute_project_write(
        session,
        principal=principal,
        idempotency_key=idempotency_key,
        operation=f"projects.update:{project_id}",
        request_data={
            "project_id": str(project_id),
            **body.model_dump(mode="json", exclude_unset=True),
        },
        action=action,
    )
    return ProjectRead.model_validate(response_data)


async def _archive_project_request(
    project_id: UUID,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: str,
    *,
    operation: str,
) -> ProjectDeletionRead:
    async def action() -> dict[str, Any]:
        request = await archive_account_project(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _deletion_response(request)

    response_data = await _execute_project_write(
        session,
        principal=principal,
        idempotency_key=idempotency_key,
        operation=operation,
        request_data={"project_id": str(project_id)},
        action=action,
    )
    return ProjectDeletionRead.model_validate(response_data)


@router.post(
    "/{project_id}/archive",
    response_model=ProjectDeletionRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Archive a project",
    description=(
        "Переводит проект в архивное состояние и создаёт запрос на удаление. "
        "Данные не удаляются немедленно, а обрабатываются по предусмотренному процессу хранения."
    ),
)
async def archive_project(
    project_id: UUID,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> ProjectDeletionRead:
    return await _archive_project_request(
        project_id,
        principal,
        session,
        idempotency_key,
        operation=f"projects.archive:{project_id}",
    )


@router.delete(
    "/{project_id}",
    response_model=ProjectDeletionRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request project deletion",
    description=(
        "Создаёт запрос на удаление проекта текущего аккаунта. "
        "Проект переводится в состояние ожидания удаления, а данные не удаляются немедленно."
    ),
)
async def delete_project(
    project_id: UUID,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> ProjectDeletionRead:
    return await _archive_project_request(
        project_id,
        principal,
        session,
        idempotency_key,
        operation=f"projects.delete:{project_id}",
    )


@router.post(
    "/{project_id}/restore",
    response_model=ProjectRead,
    summary="Restore an archived project",
    description=(
        "Восстанавливает архивированный проект и возвращает его в активное состояние. "
        "Используется для возобновления работы с проектом до завершения удаления."
    ),
)
async def restore_project(
    project_id: UUID,
    principal: ProjectManager,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> ProjectRead:
    async def action() -> dict[str, Any]:
        project = await restore_account_project(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _project_response(project)

    response_data = await _execute_project_write(
        session,
        principal=principal,
        idempotency_key=idempotency_key,
        operation=f"projects.restore:{project_id}",
        request_data={"project_id": str(project_id)},
        action=action,
    )
    return ProjectRead.model_validate(response_data)
