import pytest

from ai_seller_core.persistence.access_policy import (
    AccountRole,
    AuthorizationError,
    Principal,
    ProjectPermission,
    can_access_project,
    require_scopes,
)


@pytest.mark.parametrize("role", [AccountRole.OWNER, AccountRole.ADMIN])
def test_owner_and_admin_can_access_projects(role: AccountRole) -> None:
    assert can_access_project(role, ProjectPermission.READ)
    assert can_access_project(role, ProjectPermission.MANAGE)


def test_operator_has_no_project_level_access() -> None:
    assert not can_access_project(AccountRole.OPERATOR, ProjectPermission.READ)
    assert not can_access_project(AccountRole.OPERATOR, ProjectPermission.MANAGE)


def test_require_scopes_rejects_missing_scope() -> None:
    principal = Principal(
        user_id="user-1",
        account_id="account-1",
        role=AccountRole.ADMIN,
        scopes=frozenset({"projects:read"}),
    )

    with pytest.raises(AuthorizationError, match="scope"):
        require_scopes(principal, {"projects:read", "projects:write"})
