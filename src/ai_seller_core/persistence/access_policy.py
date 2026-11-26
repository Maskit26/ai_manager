"""Account role matrix for owner, admin and operator memberships."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class AccountRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"


class ProjectPermission(StrEnum):
    READ = "read"
    MANAGE = "manage"


class AuthorizationError(PermissionError):
    """Raised when a principal lacks the required scope or role."""


@dataclass(frozen=True)
class Principal:
    user_id: str
    account_id: str
    role: AccountRole
    scopes: frozenset[str]


# The owner role is in no set: it is created only by provisioning and never managed by the service.
_MANAGEABLE_ROLES: dict[AccountRole, frozenset[AccountRole]] = {
    AccountRole.OWNER: frozenset({AccountRole.ADMIN, AccountRole.OPERATOR}),
    AccountRole.ADMIN: frozenset({AccountRole.OPERATOR}),
    AccountRole.OPERATOR: frozenset(),
}

_PROJECT_ROLES: dict[ProjectPermission, frozenset[AccountRole]] = {
    ProjectPermission.READ: frozenset({AccountRole.OWNER, AccountRole.ADMIN}),
    ProjectPermission.MANAGE: frozenset({AccountRole.OWNER, AccountRole.ADMIN}),
}


def can_manage_roles(actor_role: AccountRole, roles: Iterable[AccountRole]) -> bool:
    """Return whether ``actor_role`` may create, change or remove memberships with ``roles``."""
    return set(roles) <= _MANAGEABLE_ROLES[actor_role]


def can_access_project(role: AccountRole, permission: ProjectPermission) -> bool:
    return role in _PROJECT_ROLES[permission]


def missing_scopes(principal: Principal, required_scopes: Iterable[str]) -> frozenset[str]:
    return frozenset(required_scopes).difference(principal.scopes)


def require_scopes(principal: Principal, required_scopes: Iterable[str]) -> None:
    if missing_scopes(principal, required_scopes):
        raise AuthorizationError("Required access scope is missing")
