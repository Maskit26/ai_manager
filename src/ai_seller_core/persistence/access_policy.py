"""Account role matrix for owner, admin and operator memberships."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class AccountRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"


# The owner role is in no set: it is created only by provisioning and never managed by the service.
_MANAGEABLE_ROLES: dict[AccountRole, frozenset[AccountRole]] = {
    AccountRole.OWNER: frozenset({AccountRole.ADMIN, AccountRole.OPERATOR}),
    AccountRole.ADMIN: frozenset({AccountRole.OPERATOR}),
    AccountRole.OPERATOR: frozenset(),
}


def can_manage_roles(actor_role: AccountRole, roles: Iterable[AccountRole]) -> bool:
    """Return whether ``actor_role`` may create, change or remove memberships with ``roles``."""
    return set(roles) <= _MANAGEABLE_ROLES[actor_role]
