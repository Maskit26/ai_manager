from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.access_policy import (
    AuthorizationError,
    Principal,
    ProjectPermission,
    can_access_project,
)
from ai_seller_core.persistence.authorization_service import require_project_access
from ai_seller_core.persistence.entitlements_service import check_access
from ai_seller_core.persistence.models import (
    Account,
    AuditLog,
    Project,
    ProjectDeletionRequest,
)
from ai_seller_core.persistence.project_deletion import archive_project, restore_project


class ProjectStateConflictError(Exception):
    """Raised when an operation is not valid for the current project state."""


class ProjectRestoreConflictError(Exception):
    """Raised when a project has no restorable pending deletion request."""


def _require_transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise RuntimeError("Project services require an active database transaction")


def _require_project_manager(principal: Principal) -> None:
    if not can_access_project(principal.role, ProjectPermission.MANAGE):
        raise AuthorizationError("Role is not authorized to manage projects")


def _audit_project(
    session: AsyncSession,
    *,
    principal: Principal,
    project: Project,
    action: str,
    diff: dict[str, str],
) -> None:
    session.add(
        AuditLog(
            account_id=project.account_id,
            project_id=project.id,
            actor_id=principal.user_id,
            action=action,
            entity_type="project",
            entity_id=project.id,
            diff=diff,
        )
    )


async def list_account_projects(
    session: AsyncSession,
    *,
    principal: Principal,
    status: str | None = None,
) -> Sequence[Project]:
    _require_transaction(session)
    if not can_access_project(principal.role, ProjectPermission.READ):
        raise AuthorizationError("Role is not authorized to access projects")

    statement = select(Project).where(Project.account_id == principal.account_id)
    if status is not None:
        statement = statement.where(Project.status == status)
    result = await session.scalars(statement.order_by(Project.id))
    return result.all()


async def get_account_project(
    session: AsyncSession,
    *,
    principal: Principal,
    project_id: str,
) -> Project:
    _require_transaction(session)
    return await require_project_access(
        session,
        principal,
        project_id,
        permission=ProjectPermission.READ,
    )


async def create_project(
    session: AsyncSession,
    *,
    principal: Principal,
    locale: str,
    timezone: str,
) -> Project:
    _require_transaction(session)
    _require_project_manager(principal)
    await check_access(
        session,
        account_id=principal.account_id,
        action="project_create",
    )

    account = await session.get(Account, principal.account_id)
    if account is None:
        raise LookupError("Account not found")

    project = Project(
        account_id=principal.account_id,
        locale=locale,
        timezone=timezone,
        settings={},
        status="active",
    )
    session.add(project)
    await session.flush()
    _audit_project(
        session,
        principal=principal,
        project=project,
        action="project.created",
        diff={"to_status": project.status},
    )
    return project


async def update_project(
    session: AsyncSession,
    *,
    principal: Principal,
    project_id: str,
    locale: str | None = None,
    timezone: str | None = None,
) -> Project:
    _require_transaction(session)
    project = await require_project_access(
        session,
        principal,
        project_id,
        permission=ProjectPermission.MANAGE,
    )
    if project.status == "archived":
        raise ProjectStateConflictError("Archived projects cannot be updated")
    await check_access(
        session,
        account_id=principal.account_id,
        action="configuration",
        project_id=project_id,
    )

    changed_fields: list[str] = []
    if locale is not None and locale != project.locale:
        project.locale = locale
        changed_fields.append("locale")
    if timezone is not None and timezone != project.timezone:
        project.timezone = timezone
        changed_fields.append("timezone")

    if changed_fields:
        await session.flush()
        _audit_project(
            session,
            principal=principal,
            project=project,
            action="project.updated",
            diff={"changed_fields": ",".join(changed_fields)},
        )
    return project


async def archive_account_project(
    session: AsyncSession,
    *,
    principal: Principal,
    project_id: str,
) -> ProjectDeletionRequest:
    _require_transaction(session)
    await require_project_access(
        session,
        principal,
        project_id,
        permission=ProjectPermission.MANAGE,
    )
    return await archive_project(
        session,
        project_id,
        actor_id=principal.user_id,
    )


async def restore_account_project(
    session: AsyncSession,
    *,
    principal: Principal,
    project_id: str,
) -> Project:
    _require_transaction(session)
    await require_project_access(
        session,
        principal,
        project_id,
        permission=ProjectPermission.MANAGE,
    )
    restored = await restore_project(
        session,
        project_id,
        actor_id=principal.user_id,
    )
    if not restored:
        raise ProjectRestoreConflictError("Project is not eligible for restoration")
    return await require_project_access(
        session,
        principal,
        project_id,
        permission=ProjectPermission.READ,
    )
