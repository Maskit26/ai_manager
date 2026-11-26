"""Idempotent account provisioning and membership management services."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.access_policy import (
    AccountRole,
    AuthorizationError,
    ProjectPermission,
    can_access_project,
    can_manage_roles,
)
from ai_seller_core.persistence.application_models import AuditLog, Escalation
from ai_seller_core.persistence.authorization_service import (
    has_assigned_dialog_access as _has_assigned_dialog_access,
)
from ai_seller_core.persistence.authorization_service import (
    require_project_belongs_to_account,
)
from ai_seller_core.persistence.identity_service import (
    IdentityConflictError,
    provision_account,
    provision_user,
    resolve_account_id,
    resolve_user_id,
)
from ai_seller_core.persistence.models import (
    Account,
    AccountMembership,
    DialogOperatorAssignment,
    DialogSession,
    Project,
    ProjectOperator,
    User,
)

PROVISIONING_SYSTEM_ACTOR = "identity_provisioning"


class MembershipError(ValueError):
    """Raised when a membership operation violates an account invariant."""


class AccessStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class ProvisionedAccount:
    account_id: str
    owner_user_id: str
    owner_membership_created: bool


def _require_transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise RuntimeError("Membership services require an active database transaction")


async def _lock_account(session: AsyncSession, account_id: str) -> None:
    account = await session.scalar(
        select(Account).where(Account.id == account_id).with_for_update()
    )
    if account is None:
        raise LookupError("Backend account not found")


async def _find_membership(
    session: AsyncSession, account_id: str, user_id: str
) -> AccountMembership | None:
    return await session.scalar(
        select(AccountMembership).where(
            AccountMembership.account_id == account_id,
            AccountMembership.user_id == user_id,
        )
    )


async def _require_membership(
    session: AsyncSession, account_id: str, user_id: str
) -> AccountMembership:
    membership = await _find_membership(session, account_id, user_id)
    if membership is None:
        raise LookupError("Account membership not found")
    return membership


def _audit(
    session: AsyncSession,
    *,
    account_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    diff: dict[str, str],
    project_id: str | None = None,
    actor_id: str | None = None,
    system_actor: str | None = None,
) -> None:
    session.add(
        AuditLog(
            account_id=account_id,
            project_id=project_id,
            actor_id=actor_id,
            system_actor=system_actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            diff=diff,
        )
    )


async def get_active_membership_role(
    session: AsyncSession, *, account_id: str, user_id: str
) -> AccountRole | None:
    """Return the role read from the database, or None when the user is not an active member."""
    _require_transaction(session)
    role = await session.scalar(
        select(AccountMembership.role).where(
            AccountMembership.account_id == account_id,
            AccountMembership.user_id == user_id,
            AccountMembership.status == AccessStatus.ACTIVE.value,
        )
    )
    return None if role is None else AccountRole(role)


async def _authorize(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    roles: set[AccountRole],
) -> None:
    actor_role = await get_active_membership_role(
        session, account_id=account_id, user_id=actor_user_id
    )
    if actor_role is None or not can_manage_roles(actor_role, roles):
        raise AuthorizationError("Actor is not allowed to manage memberships with these roles")


async def provision_account_with_owner(
    session: AsyncSession,
    *,
    identity_provider: str,
    external_account_id: str,
    external_owner_id: str,
) -> ProvisionedAccount:
    """Create or reuse an account and its owner membership.

    Repeated calls with the same external IDs return the existing owner membership.
    A conflicting owner or an account that already has other members raises
    IdentityConflictError without rebinding anything. The caller must roll back on error,
    because a new user row may already have been flushed.
    """
    _require_transaction(session)
    try:
        account_id: str | None = await resolve_account_id(
            session,
            identity_provider=identity_provider,
            external_account_id=external_account_id,
        )
    except LookupError:
        account_id = None

    if account_id is None:
        owner = await provision_user(
            session,
            identity_provider=identity_provider,
            external_user_id=external_owner_id,
        )
        account = await provision_account(
            session,
            identity_provider=identity_provider,
            external_account_id=external_account_id,
        )
        account_id = account.id
        owner_user_id = owner.id
    else:
        try:
            owner_user_id = await resolve_user_id(
                session,
                identity_provider=identity_provider,
                external_user_id=external_owner_id,
            )
        except LookupError as exc:
            raise IdentityConflictError("Account is already linked to a different owner") from exc

    await _lock_account(session, account_id)
    if await session.get(User, owner_user_id) is None:
        raise RuntimeError("Owner identity references a missing backend user")

    members = list(
        await session.scalars(
            select(AccountMembership).where(AccountMembership.account_id == account_id)
        )
    )
    for member in members:
        if member.user_id == owner_user_id:
            if member.role == AccountRole.OWNER.value:
                return ProvisionedAccount(
                    account_id=account_id,
                    owner_user_id=owner_user_id,
                    owner_membership_created=False,
                )
            raise IdentityConflictError("Account owner identity is bound to a non-owner role")
    if members:
        raise IdentityConflictError("Account already has members with a different owner")

    session.add(
        AccountMembership(
            account_id=account_id,
            user_id=owner_user_id,
            role=AccountRole.OWNER.value,
        )
    )
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        system_actor=PROVISIONING_SYSTEM_ACTOR,
        action="account.provisioned",
        entity_type="account",
        entity_id=account_id,
        diff={"owner_user_id": owner_user_id},
    )
    return ProvisionedAccount(
        account_id=account_id,
        owner_user_id=owner_user_id,
        owner_membership_created=True,
    )


async def add_member(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    target_user_id: str,
    role: AccountRole,
) -> AccountMembership:
    _require_transaction(session)
    requested = AccountRole(role)
    await _lock_account(session, account_id)
    existing = await _find_membership(session, account_id, target_user_id)
    roles = {requested} if existing is None else {requested, AccountRole(existing.role)}
    await _authorize(session, account_id=account_id, actor_user_id=actor_user_id, roles=roles)

    if existing is not None:
        if AccountRole(existing.role) == requested:
            return existing
        raise MembershipError("User already has a different role; use change_member_role")

    if await session.get(User, target_user_id) is None:
        raise LookupError("Backend user not found")

    membership = AccountMembership(
        account_id=account_id,
        user_id=target_user_id,
        role=requested.value,
    )
    session.add(membership)
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="membership.added",
        entity_type="account_membership",
        entity_id=membership.id,
        diff={"user_id": target_user_id, "role": requested.value},
    )
    return membership


async def change_member_role(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    target_user_id: str,
    role: AccountRole,
) -> AccountMembership:
    _require_transaction(session)
    requested = AccountRole(role)
    await _lock_account(session, account_id)
    membership = await _require_membership(session, account_id, target_user_id)
    current = AccountRole(membership.role)
    await _authorize(
        session,
        account_id=account_id,
        actor_user_id=actor_user_id,
        roles={current, requested},
    )
    if current == requested:
        return membership

    if current is AccountRole.OPERATOR:
        await _clear_operator_dialog_state(session, account_id, membership.id)
        await session.execute(
            delete(ProjectOperator).where(ProjectOperator.membership_id == membership.id)
        )
    membership.role = requested.value
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="membership.role_changed",
        entity_type="account_membership",
        entity_id=membership.id,
        diff={
            "user_id": target_user_id,
            "from_role": current.value,
            "to_role": requested.value,
        },
    )
    return membership


async def remove_member(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    target_user_id: str,
) -> None:
    _require_transaction(session)
    await _lock_account(session, account_id)
    membership = await _require_membership(session, account_id, target_user_id)
    current = AccountRole(membership.role)
    await _authorize(session, account_id=account_id, actor_user_id=actor_user_id, roles={current})
    membership_id = membership.id
    await _clear_operator_dialog_state(session, account_id, membership_id, clear_history=True)
    await session.execute(
        delete(ProjectOperator).where(ProjectOperator.membership_id == membership_id)
    )
    await session.delete(membership)
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="membership.removed",
        entity_type="account_membership",
        entity_id=membership_id,
        diff={"user_id": target_user_id, "role": current.value},
    )


async def set_member_status(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    target_user_id: str,
    status: AccessStatus,
) -> AccountMembership:
    """Enable or disable a membership. The owner can never be disabled."""
    _require_transaction(session)
    requested = AccessStatus(status)
    await _lock_account(session, account_id)
    membership = await _require_membership(session, account_id, target_user_id)
    current_role = AccountRole(membership.role)
    await _authorize(
        session, account_id=account_id, actor_user_id=actor_user_id, roles={current_role}
    )
    current = AccessStatus(membership.status)
    if current == requested:
        return membership

    if requested is AccessStatus.DISABLED:
        await _clear_operator_dialog_state(session, account_id, membership.id)
    membership.status = requested.value
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="membership.status_changed",
        entity_type="account_membership",
        entity_id=membership.id,
        diff={
            "user_id": target_user_id,
            "role": current_role.value,
            "from_status": current.value,
            "to_status": requested.value,
        },
    )
    return membership


async def _require_project_operator(
    session: AsyncSession, *, account_id: str, project_id: str, membership_id: str
) -> ProjectOperator:
    assignment = await session.scalar(
        select(ProjectOperator).where(
            ProjectOperator.account_id == account_id,
            ProjectOperator.project_id == project_id,
            ProjectOperator.membership_id == membership_id,
        )
    )
    if assignment is None:
        raise LookupError("Project operator assignment not found")
    return assignment


async def assign_operator_to_project(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    project_id: str,
    target_user_id: str,
) -> ProjectOperator:
    """Attach an active operator of the account to a project of the same account. Idempotent."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    await _authorize(
        session,
        account_id=account_id,
        actor_user_id=actor_user_id,
        roles={AccountRole.OPERATOR},
    )
    membership = await _require_membership(session, account_id, target_user_id)
    if AccountRole(membership.role) is not AccountRole.OPERATOR:
        raise MembershipError("Only operators can be assigned to projects")
    if AccessStatus(membership.status) is not AccessStatus.ACTIVE:
        raise MembershipError("Cannot assign a disabled membership to a project")
    project = await session.scalar(
        select(Project).where(Project.id == project_id, Project.account_id == account_id)
    )
    if project is None:
        raise LookupError("Project not found in account")

    existing = await session.scalar(
        select(ProjectOperator).where(
            ProjectOperator.project_id == project_id,
            ProjectOperator.membership_id == membership.id,
        )
    )
    if existing is not None:
        return existing

    assignment = ProjectOperator(
        account_id=account_id,
        project_id=project_id,
        membership_id=membership.id,
        status=AccessStatus.ACTIVE.value,
    )
    session.add(assignment)
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="project_operator.assigned",
        entity_type="project_operator",
        entity_id=assignment.id,
        diff={"project_id": project_id, "user_id": target_user_id},
    )
    return assignment


async def set_project_operator_status(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    project_id: str,
    target_user_id: str,
    status: AccessStatus,
) -> ProjectOperator:
    """Suspend or restore one operator on one project without touching the membership."""
    _require_transaction(session)
    requested = AccessStatus(status)
    await _lock_account(session, account_id)
    await _authorize(
        session,
        account_id=account_id,
        actor_user_id=actor_user_id,
        roles={AccountRole.OPERATOR},
    )
    membership = await _require_membership(session, account_id, target_user_id)
    assignment = await _require_project_operator(
        session, account_id=account_id, project_id=project_id, membership_id=membership.id
    )
    current = AccessStatus(assignment.status)
    if current == requested:
        return assignment

    if requested is AccessStatus.DISABLED:
        await _clear_operator_dialog_state(
            session,
            account_id,
            membership.id,
            project_id=project_id,
        )
    assignment.status = requested.value
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        actor_id=actor_user_id,
        action="project_operator.status_changed",
        entity_type="project_operator",
        entity_id=assignment.id,
        diff={
            "project_id": project_id,
            "user_id": target_user_id,
            "from_status": current.value,
            "to_status": requested.value,
        },
    )
    return assignment


async def has_active_project_operator_access(
    session: AsyncSession, *, account_id: str, project_id: str, user_id: str
) -> bool:
    """True only when the user is an active operator and the assignment to the project is active."""
    _require_transaction(session)
    row = await session.scalar(
        select(ProjectOperator.id)
        .join(AccountMembership, AccountMembership.id == ProjectOperator.membership_id)
        .where(
            ProjectOperator.account_id == account_id,
            ProjectOperator.project_id == project_id,
            ProjectOperator.status == AccessStatus.ACTIVE.value,
            AccountMembership.user_id == user_id,
            AccountMembership.role == AccountRole.OPERATOR.value,
            AccountMembership.status == AccessStatus.ACTIVE.value,
        )
    )
    return row is not None


async def has_owner_admin_project_access(
    session: AsyncSession, *, account_id: str, project_id: str, user_id: str
) -> bool:
    """Return whether an active account owner/admin can manage this account's project."""
    _require_transaction(session)
    project_exists = await session.scalar(
        select(Project.id).where(Project.id == project_id, Project.account_id == account_id)
    )
    if project_exists is None:
        return False
    role = await get_active_membership_role(session, account_id=account_id, user_id=user_id)
    return role is not None and can_access_project(role, ProjectPermission.MANAGE)


async def has_assigned_dialog_access(
    session: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    session_id: str,
    user_id: str,
) -> bool:
    return await _has_assigned_dialog_access(
        session,
        account_id=account_id,
        project_id=project_id,
        session_id=session_id,
        user_id=user_id,
    )


async def _require_owner_admin_project_access(
    session: AsyncSession, *, account_id: str, project_id: str, user_id: str
) -> None:
    if not await has_owner_admin_project_access(
        session, account_id=account_id, project_id=project_id, user_id=user_id
    ):
        raise AuthorizationError("Active account owner/admin access to the project is required")


async def _lock_dialog_session(
    session: AsyncSession, *, account_id: str, project_id: str, session_id: str
) -> DialogSession:
    await require_project_belongs_to_account(session, account_id=account_id, project_id=project_id)
    dialog = await session.scalar(
        select(DialogSession)
        .where(DialogSession.project_id == project_id, DialogSession.id == session_id)
        .with_for_update()
    )
    if dialog is None:
        raise LookupError("Dialog session not found")
    return dialog


async def _lock_open_escalation(
    session: AsyncSession, *, account_id: str, project_id: str, session_id: str
) -> Escalation | None:
    return await session.scalar(
        select(Escalation)
        .where(
            Escalation.account_id == account_id,
            Escalation.project_id == project_id,
            Escalation.session_id == session_id,
            Escalation.status == "open",
        )
        .with_for_update()
    )


async def _require_active_operator(
    session: AsyncSession, *, account_id: str, user_id: str
) -> AccountMembership:
    membership = await _require_membership(session, account_id, user_id)
    if (
        membership.role != AccountRole.OPERATOR.value
        or membership.status != AccessStatus.ACTIVE.value
    ):
        raise AuthorizationError("An active operator membership is required")
    return membership


async def _require_active_project_operator(
    session: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    membership_id: str,
) -> ProjectOperator:
    assignment = await session.scalar(
        select(ProjectOperator)
        .where(
            ProjectOperator.account_id == account_id,
            ProjectOperator.project_id == project_id,
            ProjectOperator.membership_id == membership_id,
            ProjectOperator.status == AccessStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    if assignment is None:
        raise MembershipError("Operator is not active on this project")
    return assignment


async def _clear_operator_dialog_state(
    session: AsyncSession,
    account_id: str,
    membership_id: str,
    *,
    project_id: str | None = None,
    clear_history: bool = False,
) -> None:
    statement = select(DialogOperatorAssignment).where(
        DialogOperatorAssignment.account_id == account_id,
        DialogOperatorAssignment.membership_id == membership_id,
    )
    if project_id is not None:
        statement = statement.where(DialogOperatorAssignment.project_id == project_id)
    assignments = list(
        await session.scalars(
            statement.order_by(
                DialogOperatorAssignment.project_id,
                DialogOperatorAssignment.session_id,
            ).with_for_update()
        )
    )
    for assignment in assignments:
        dialog = await session.scalar(
            select(DialogSession)
            .where(
                DialogSession.project_id == assignment.project_id,
                DialogSession.id == assignment.session_id,
            )
            .with_for_update()
        )
        if dialog is not None:
            dialog.version += 1
        escalations = await session.scalars(
            select(Escalation)
            .where(
                Escalation.account_id == account_id,
                Escalation.project_id == assignment.project_id,
                Escalation.session_id == assignment.session_id,
                Escalation.status == "open",
                Escalation.assignee_membership_id == membership_id,
            )
            .with_for_update()
        )
        for escalation in escalations:
            escalation.assignee_membership_id = None
        await session.delete(assignment)

    escalation_statement = update(Escalation).where(
        Escalation.account_id == account_id,
        Escalation.assignee_membership_id == membership_id,
        Escalation.status == "open",
    )
    if project_id is not None:
        escalation_statement = escalation_statement.where(Escalation.project_id == project_id)
    await session.execute(escalation_statement.values(assignee_membership_id=None))

    if clear_history:
        await session.execute(
            update(Escalation)
            .where(
                Escalation.account_id == account_id,
                Escalation.assignee_membership_id == membership_id,
            )
            .values(assignee_membership_id=None)
        )


async def assign_operator_to_dialog(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    project_id: str,
    session_id: str,
    target_user_id: str,
) -> DialogOperatorAssignment:
    """Assign or reassign one active project operator to a dialog."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    await _require_owner_admin_project_access(
        session,
        account_id=account_id,
        project_id=project_id,
        user_id=actor_user_id,
    )
    dialog = await _lock_dialog_session(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    if dialog.state != "active":
        raise MembershipError("Cannot assign an operator to a closed dialog")
    target = await _require_active_operator(session, account_id=account_id, user_id=target_user_id)
    await _require_active_project_operator(
        session,
        account_id=account_id,
        project_id=project_id,
        membership_id=target.id,
    )
    assignment = await session.scalar(
        select(DialogOperatorAssignment)
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.project_id == project_id,
            DialogOperatorAssignment.session_id == session_id,
        )
        .with_for_update()
    )
    busy_session_id = await session.scalar(
        select(DialogOperatorAssignment.session_id)
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.membership_id == target.id,
            DialogOperatorAssignment.session_id != session_id,
        )
        .with_for_update()
    )
    if busy_session_id is not None:
        raise MembershipError("Operator is already assigned to another dialog")

    escalation = await _lock_open_escalation(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    changed = (
        assignment is None
        or assignment.membership_id != target.id
        or (escalation is not None and escalation.assignee_membership_id != target.id)
    )
    if assignment is None:
        assignment = DialogOperatorAssignment(
            account_id=account_id,
            project_id=project_id,
            session_id=session_id,
            membership_id=target.id,
        )
        session.add(assignment)
    else:
        assignment.membership_id = target.id
    if escalation is not None:
        escalation.assignee_membership_id = target.id
    if changed:
        dialog.version += 1
        await session.flush()
        _audit(
            session,
            account_id=account_id,
            project_id=project_id,
            actor_id=actor_user_id,
            action="dialog_operator.assigned",
            entity_type="dialog_session",
            entity_id=session_id,
            diff={"user_id": target.user_id},
        )
    return assignment


async def release_dialog_assignment(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    project_id: str,
    session_id: str,
) -> None:
    """Release the caller's dialog or let an owner/admin unassign its operator."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    dialog = await _lock_dialog_session(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    if dialog.state != "active":
        raise MembershipError("Cannot claim an escalation for a closed dialog")
    actor = await _require_membership(session, account_id, actor_user_id)
    if actor.status != AccessStatus.ACTIVE.value:
        raise AuthorizationError("Active account membership is required")
    is_manager = can_access_project(AccountRole(actor.role), ProjectPermission.MANAGE)
    if is_manager:
        await _require_owner_admin_project_access(
            session,
            account_id=account_id,
            project_id=project_id,
            user_id=actor_user_id,
        )
    elif actor.role != AccountRole.OPERATOR.value:
        raise AuthorizationError("Only an assigned operator or account owner/admin can release")

    assignment = await session.scalar(
        select(DialogOperatorAssignment)
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.project_id == project_id,
            DialogOperatorAssignment.session_id == session_id,
        )
        .with_for_update()
    )
    if assignment is None:
        return
    if not is_manager and assignment.membership_id != actor.id:
        raise AuthorizationError("Operators can release only their own dialog assignment")

    escalation = await _lock_open_escalation(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    if escalation is not None and escalation.assignee_membership_id not in {
        None,
        assignment.membership_id,
    }:
        raise MembershipError("Dialog and escalation assignments are inconsistent")
    released_membership = await session.scalar(
        select(AccountMembership).where(
            AccountMembership.id == assignment.membership_id,
            AccountMembership.account_id == account_id,
        )
    )
    if released_membership is None:
        raise MembershipError("Assigned operator membership not found")
    if escalation is not None:
        escalation.assignee_membership_id = None
    await session.delete(assignment)
    dialog.version += 1
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        project_id=project_id,
        actor_id=actor_user_id,
        action="dialog_operator.released",
        entity_type="dialog_session",
        entity_id=session_id,
        diff={"user_id": released_membership.user_id},
    )


async def claim_open_escalation(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    escalation_id: str,
) -> Escalation:
    """Atomically claim an open, unassigned escalation for an active roster operator."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    scope = (
        await session.execute(
            select(Escalation.project_id, Escalation.session_id)
            .join(Project, Project.id == Escalation.project_id)
            .where(
                Escalation.id == escalation_id,
                Project.account_id == account_id,
            )
        )
    ).one_or_none()
    if scope is None:
        raise LookupError("Escalation not found")
    project_id, session_id = scope
    dialog = await _lock_dialog_session(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    if dialog.state != "active":
        raise MembershipError("Cannot route an escalation for a closed dialog")
    escalation = await session.scalar(
        select(Escalation)
        .where(
            Escalation.id == escalation_id,
            Escalation.account_id == account_id,
            Escalation.project_id == project_id,
            Escalation.session_id == session_id,
        )
        .with_for_update()
    )
    if escalation is None:
        raise LookupError("Escalation not found")
    if escalation.status != "open" or escalation.assignee_membership_id is not None:
        raise MembershipError("Escalation is not open and unassigned")

    operator = await _require_active_operator(session, account_id=account_id, user_id=actor_user_id)
    await _require_active_project_operator(
        session,
        account_id=account_id,
        project_id=project_id,
        membership_id=operator.id,
    )
    current_assignment = await session.scalar(
        select(DialogOperatorAssignment)
        .where(DialogOperatorAssignment.session_id == session_id)
        .with_for_update()
    )
    if current_assignment is not None:
        raise MembershipError("Dialog is already assigned")
    busy_session_id = await session.scalar(
        select(DialogOperatorAssignment.session_id)
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.membership_id == operator.id,
        )
        .with_for_update()
    )
    if busy_session_id is not None:
        raise MembershipError("Operator is already assigned to another dialog")

    session.add(
        DialogOperatorAssignment(
            account_id=account_id,
            project_id=project_id,
            session_id=session_id,
            membership_id=operator.id,
        )
    )
    escalation.assignee_membership_id = operator.id
    dialog.version += 1
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        project_id=project_id,
        actor_id=actor_user_id,
        action="escalation.claimed",
        entity_type="escalation",
        entity_id=escalation.id,
        diff={"user_id": operator.user_id},
    )
    return escalation


async def assign_open_escalation_to_first_free_operator(
    session: AsyncSession,
    *,
    account_id: str,
    project_id: str,
    session_id: str,
) -> AccountMembership | None:
    """Assign the oldest available project-roster operator to an open escalation."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    dialog = await _lock_dialog_session(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    escalation = await _lock_open_escalation(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    if escalation is None:
        raise LookupError("Open escalation not found")
    if escalation.assignee_membership_id is not None:
        assignment = await session.scalar(
            select(DialogOperatorAssignment).where(
                DialogOperatorAssignment.session_id == session_id,
                DialogOperatorAssignment.membership_id == escalation.assignee_membership_id,
            )
        )
        if assignment is None:
            raise MembershipError("Open escalation assignment is inconsistent")
        operator = await session.scalar(
            select(AccountMembership).where(
                AccountMembership.id == escalation.assignee_membership_id,
                AccountMembership.account_id == account_id,
                AccountMembership.role == AccountRole.OPERATOR.value,
                AccountMembership.status == AccessStatus.ACTIVE.value,
            )
        )
        if operator is None:
            raise MembershipError("Open escalation has no active assigned operator")
        await _require_active_project_operator(
            session,
            account_id=account_id,
            project_id=project_id,
            membership_id=operator.id,
        )
        return operator

    current_assignment = await session.scalar(
        select(DialogOperatorAssignment)
        .where(DialogOperatorAssignment.session_id == session_id)
        .with_for_update()
    )
    if current_assignment is not None:
        raise MembershipError("Dialog is already assigned")
    busy_assignment = (
        select(DialogOperatorAssignment.session_id)
        .where(DialogOperatorAssignment.membership_id == AccountMembership.id)
        .exists()
    )
    operator = await session.scalar(
        select(AccountMembership)
        .join(ProjectOperator, ProjectOperator.membership_id == AccountMembership.id)
        .where(
            ProjectOperator.account_id == account_id,
            ProjectOperator.project_id == project_id,
            ProjectOperator.status == AccessStatus.ACTIVE.value,
            AccountMembership.account_id == account_id,
            AccountMembership.role == AccountRole.OPERATOR.value,
            AccountMembership.status == AccessStatus.ACTIVE.value,
            ~busy_assignment,
        )
        .order_by(ProjectOperator.created_at, AccountMembership.id)
        .limit(1)
        .with_for_update()
    )
    if operator is None:
        return None

    session.add(
        DialogOperatorAssignment(
            account_id=account_id,
            project_id=project_id,
            session_id=session_id,
            membership_id=operator.id,
        )
    )
    escalation.assignee_membership_id = operator.id
    dialog.version += 1
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        project_id=project_id,
        system_actor="dialog_escalation_router",
        action="escalation.auto_assigned",
        entity_type="escalation",
        entity_id=escalation.id,
        diff={"user_id": operator.user_id},
    )
    return operator


async def resolve_open_escalation(
    session: AsyncSession,
    *,
    account_id: str,
    actor_user_id: str,
    escalation_id: str,
) -> Escalation:
    """Resolve an open escalation only for its currently assigned operator."""
    _require_transaction(session)
    await _lock_account(session, account_id)
    scope = (
        await session.execute(
            select(Escalation.project_id, Escalation.session_id)
            .join(Project, Project.id == Escalation.project_id)
            .where(
                Escalation.id == escalation_id,
                Project.account_id == account_id,
            )
        )
    ).one_or_none()
    if scope is None:
        raise LookupError("Escalation not found")
    project_id, session_id = scope
    dialog = await _lock_dialog_session(
        session, account_id=account_id, project_id=project_id, session_id=session_id
    )
    escalation = await session.scalar(
        select(Escalation)
        .where(
            Escalation.id == escalation_id,
            Escalation.account_id == account_id,
            Escalation.project_id == project_id,
            Escalation.session_id == session_id,
        )
        .with_for_update()
    )
    if escalation is None or escalation.status != "open":
        raise MembershipError("Escalation is not open")
    operator = await _require_active_operator(session, account_id=account_id, user_id=actor_user_id)
    await _require_active_project_operator(
        session,
        account_id=account_id,
        project_id=project_id,
        membership_id=operator.id,
    )
    if escalation.assignee_membership_id != operator.id:
        raise AuthorizationError("Only the assigned operator can resolve this escalation")
    assignment = await session.scalar(
        select(DialogOperatorAssignment)
        .where(
            DialogOperatorAssignment.account_id == account_id,
            DialogOperatorAssignment.project_id == project_id,
            DialogOperatorAssignment.session_id == session_id,
            DialogOperatorAssignment.membership_id == operator.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise MembershipError("Open escalation assignment is inconsistent")

    escalation.status = "resolved"
    escalation.assignee_membership_id = None
    await session.delete(assignment)
    dialog.version += 1
    await session.flush()
    _audit(
        session,
        account_id=account_id,
        project_id=project_id,
        actor_id=actor_user_id,
        action="escalation.resolved",
        entity_type="escalation",
        entity_id=escalation.id,
        diff={"user_id": operator.user_id, "status": "resolved"},
    )
    return escalation
