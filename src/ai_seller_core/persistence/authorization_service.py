"""Authorization guards for tenant-scoped project and dialog access."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.access_policy import (
    AccountRole,
    AuthorizationError,
    Principal,
    ProjectPermission,
    can_access_project,
    require_scopes,
)
from ai_seller_core.persistence.models import (
    AccountMembership,
    DialogOperatorAssignment,
    DialogSession,
    Project,
    ProjectOperator,
)


async def require_project_belongs_to_account(
    session: AsyncSession, *, account_id: str, project_id: str
) -> Project:
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.account_id == account_id)
    )
    if project is None:
        raise LookupError("Project not found")
    return project


async def require_project_access(
    session: AsyncSession,
    principal: Principal,
    project_id: str,
    *,
    permission: ProjectPermission,
    required_scopes: Iterable[str] = (),
) -> Project:
    require_scopes(principal, required_scopes)
    if not can_access_project(principal.role, permission):
        raise AuthorizationError("Role is not authorized to access this project")
    return await require_project_belongs_to_account(
        session, account_id=principal.account_id, project_id=project_id
    )


async def has_assigned_dialog_access(
    session: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    session_id: str,
    user_id: str,
) -> bool:
    if not session.in_transaction():
        raise RuntimeError("Authorization checks require an active database transaction")
    assignment_id = await session.scalar(
        select(DialogOperatorAssignment.session_id)
        .join(
            AccountMembership,
            AccountMembership.id == DialogOperatorAssignment.membership_id,
        )
        .join(
            ProjectOperator,
            (ProjectOperator.membership_id == AccountMembership.id)
            & (ProjectOperator.project_id == DialogOperatorAssignment.project_id),
        )
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.project_id == project_id,
            DialogOperatorAssignment.session_id == session_id,
            AccountMembership.user_id == user_id,
            AccountMembership.role == AccountRole.OPERATOR.value,
            AccountMembership.status == "active",
            ProjectOperator.status == "active",
        )
    )
    return assignment_id is not None


async def require_dialog_access(
    session: AsyncSession,
    principal: Principal,
    *,
    project_id: str,
    session_id: str,
    required_scopes: Iterable[str] = (),
) -> DialogSession:
    require_scopes(principal, required_scopes)
    await require_project_belongs_to_account(
        session, account_id=principal.account_id, project_id=project_id
    )
    dialog = await session.scalar(
        select(DialogSession).where(
            DialogSession.project_id == project_id,
            DialogSession.id == session_id,
        )
    )
    if dialog is None:
        raise LookupError("Dialog session not found")
    if can_access_project(principal.role, ProjectPermission.READ):
        return dialog
    if principal.role == AccountRole.OPERATOR and await has_assigned_dialog_access(
        session,
        account_id=principal.account_id,
        project_id=project_id,
        session_id=session_id,
        user_id=principal.user_id,
    ):
        return dialog
    raise AuthorizationError("Principal is not authorized to access this dialog")
