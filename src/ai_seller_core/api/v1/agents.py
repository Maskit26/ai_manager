from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from ai_seller_core.api.dependencies import (
    AgentReader,
    AgentWriter,
    DatabaseSession,
    IdempotencyKey,
)
from ai_seller_core.api.v1.schemas import (
    COMMON_ERROR_RESPONSES,
    AgentDraftUpdateRequest,
    AgentReleaseRead,
    AgentRollbackRequest,
    AgentStateRead,
)
from ai_seller_core.persistence.agent_releases import (
    create_agent,
    get_agent_state,
    publish_agent_release,
    rollback_agent_release,
    save_agent_draft,
)
from ai_seller_core.persistence.idempotency_service import execute_idempotently
from ai_seller_core.persistence.models import Agent, AgentDraft, AgentRelease

router = APIRouter(
    prefix="/projects/{project_id}/agent",
    tags=["agents"],
    responses=COMMON_ERROR_RESPONSES,
)


def _state_response(agent: Agent, draft: AgentDraft) -> dict[str, Any]:
    return AgentStateRead(
        id=agent.id,
        project_id=agent.project_id,
        status=agent.status,
        active_release_id=agent.active_release_id,
        draft_config=draft.config,
    ).model_dump(mode="json")


def _release_response(release: AgentRelease) -> dict[str, Any]:
    return AgentReleaseRead(
        id=release.id,
        project_id=release.project_id,
        agent_id=release.agent_id,
        version=release.version,
        snapshot=release.snapshot,
        created_at=release.created_at,
    ).model_dump(mode="json")


async def _execute_agent_write(
    session: DatabaseSession,
    *,
    principal: AgentWriter,
    operation: str,
    idempotency_key: str,
    request_data: dict[str, Any],
    action: Callable[[], Awaitable[dict[str, Any]]],
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


@router.get(
    "",
    response_model=AgentStateRead,
    summary="Get project agent and current draft",
)
async def get_project_agent(
    project_id: UUID,
    principal: AgentReader,
    session: DatabaseSession,
) -> AgentStateRead:
    async with session.begin():
        agent, draft = await get_agent_state(
            session,
            principal=principal,
            project_id=str(project_id),
        )
    return AgentStateRead.model_validate(_state_response(agent, draft))


@router.post(
    "",
    response_model=AgentStateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create the project agent",
)
async def create_project_agent(
    project_id: UUID,
    principal: AgentWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AgentStateRead:
    async def action() -> dict[str, Any]:
        agent = await create_agent(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        _, draft = await get_agent_state(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _state_response(agent, draft)

    response_data = await _execute_agent_write(
        session,
        principal=principal,
        operation=f"agents.create:{project_id}",
        idempotency_key=idempotency_key,
        request_data={"project_id": str(project_id)},
        action=action,
    )
    return AgentStateRead.model_validate(response_data)


@router.patch(
    "/draft",
    response_model=AgentStateRead,
    summary="Save the agent draft",
)
async def update_agent_draft(
    project_id: UUID,
    body: AgentDraftUpdateRequest,
    principal: AgentWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AgentStateRead:
    async def action() -> dict[str, Any]:
        await save_agent_draft(
            session,
            principal=principal,
            project_id=str(project_id),
            config=body.config,
        )
        agent, draft = await get_agent_state(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _state_response(agent, draft)

    response_data = await _execute_agent_write(
        session,
        principal=principal,
        operation=f"agents.draft.update:{project_id}",
        idempotency_key=idempotency_key,
        request_data={
            "project_id": str(project_id),
            **body.model_dump(mode="json"),
        },
        action=action,
    )
    return AgentStateRead.model_validate(response_data)


@router.post(
    "/publish",
    response_model=AgentReleaseRead,
    status_code=status.HTTP_201_CREATED,
    summary="Publish the current agent draft",
)
async def publish_project_agent(
    project_id: UUID,
    principal: AgentWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AgentReleaseRead:
    async def action() -> dict[str, Any]:
        release = await publish_agent_release(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _release_response(release)

    response_data = await _execute_agent_write(
        session,
        principal=principal,
        operation=f"agents.publish:{project_id}",
        idempotency_key=idempotency_key,
        request_data={"project_id": str(project_id)},
        action=action,
    )
    return AgentReleaseRead.model_validate(response_data)


@router.post(
    "/rollback",
    response_model=AgentStateRead,
    summary="Roll back to an agent release",
)
async def rollback_project_agent(
    project_id: UUID,
    body: AgentRollbackRequest,
    principal: AgentWriter,
    session: DatabaseSession,
    idempotency_key: IdempotencyKey,
) -> AgentStateRead:
    async def action() -> dict[str, Any]:
        await rollback_agent_release(
            session,
            principal=principal,
            project_id=str(project_id),
            release_id=str(body.release_id),
        )
        agent, draft = await get_agent_state(
            session,
            principal=principal,
            project_id=str(project_id),
        )
        return _state_response(agent, draft)

    response_data = await _execute_agent_write(
        session,
        principal=principal,
        operation=f"agents.rollback:{project_id}",
        idempotency_key=idempotency_key,
        request_data={
            "project_id": str(project_id),
            **body.model_dump(mode="json"),
        },
        action=action,
    )
    return AgentStateRead.model_validate(response_data)
