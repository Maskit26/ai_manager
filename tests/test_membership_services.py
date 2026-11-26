import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from ai_seller_core.api.auth import (
    ActiveMembershipRequired,
    UnknownIdentityError,
    VerifiedAccessToken,
    build_principal,
)
from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.access_policy import AccountRole, can_manage_roles
from ai_seller_core.persistence.application_models import AuditLog, Escalation
from ai_seller_core.persistence.identity_service import IdentityConflictError, provision_user
from ai_seller_core.persistence.membership_service import (
    PROVISIONING_SYSTEM_ACTOR,
    AccessStatus,
    AuthorizationError,
    MembershipError,
    add_member,
    assign_open_escalation_to_first_free_operator,
    assign_operator_to_dialog,
    assign_operator_to_project,
    change_member_role,
    claim_open_escalation,
    get_active_membership_role,
    has_active_project_operator_access,
    has_assigned_dialog_access,
    has_owner_admin_project_access,
    provision_account_with_owner,
    release_dialog_assignment,
    remove_member,
    resolve_open_escalation,
    set_member_status,
    set_project_operator_status,
)
from ai_seller_core.persistence.models import (
    AccountMembership,
    Agent,
    AgentRelease,
    Channel,
    DialogOperatorAssignment,
    DialogSession,
    Project,
    ProjectOperator,
)

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = "bitrix"
OWNER, ADMIN, OPERATOR = AccountRole.OWNER, AccountRole.ADMIN, AccountRole.OPERATOR


@pytest.mark.parametrize(
    ("actor", "roles", "allowed"),
    [
        (OWNER, {OWNER}, False),
        (OWNER, {ADMIN, OPERATOR}, True),
        (ADMIN, {ADMIN}, False),
        (ADMIN, {OPERATOR}, True),
        (ADMIN, {OWNER}, False),
        (ADMIN, {ADMIN, OWNER}, False),
        (OPERATOR, {OPERATOR}, False),
        (OPERATOR, {ADMIN}, False),
    ],
)
def test_role_matrix_limits_membership_management(
    actor: AccountRole, roles: set[AccountRole], allowed: bool
) -> None:
    assert can_manage_roles(actor, roles) is allowed


def _database_url() -> str:
    test_database_url = os.environ.pop("DATABASE_URL", None)
    try:
        return Settings().database_url.get_secret_value()
    except ValidationError as error:
        if any(
            item["loc"] == ("database_url",) and item["type"] == "missing"
            for item in error.errors()
        ):
            pytest.skip("Configure DATABASE_URL for PostgreSQL tests")
        raise
    finally:
        if test_database_url is not None:
            os.environ["DATABASE_URL"] = test_database_url


def _upgrade_head(sync_connection) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = sync_connection
    command.upgrade(config, "head")


def _run_in_schema(scenario: Callable[[AsyncEngine], Awaitable[None]]) -> None:
    database_url = _database_url()

    async def run() -> None:
        admin_engine = create_async_engine(database_url)
        schema = f"test_membership_{uuid4().hex}"
        try:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            engine = create_async_engine(
                database_url,
                connect_args={"server_settings": {"search_path": schema}},
            )
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(_upgrade_head)
                await scenario(engine)
            finally:
                await engine.dispose()
        finally:
            async with admin_engine.begin() as connection:
                await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await admin_engine.dispose()

    asyncio.run(run())


@dataclass(frozen=True)
class _Seed:
    account_id: str
    owner_id: str
    admin_id: str
    operator_id: str
    outsider_id: str


async def _seed_account(engine: AsyncEngine) -> _Seed:
    async with AsyncSession(engine) as session, session.begin():
        provisioned = await provision_account_with_owner(
            session,
            identity_provider=PROVIDER,
            external_account_id=str(uuid4()),
            external_owner_id=str(uuid4()),
        )
        admin = await provision_user(
            session, identity_provider=PROVIDER, external_user_id=str(uuid4())
        )
        operator = await provision_user(
            session, identity_provider=PROVIDER, external_user_id=str(uuid4())
        )
        outsider = await provision_user(
            session, identity_provider=PROVIDER, external_user_id=str(uuid4())
        )
        await add_member(
            session,
            account_id=provisioned.account_id,
            actor_user_id=provisioned.owner_user_id,
            target_user_id=admin.id,
            role=ADMIN,
        )
        await add_member(
            session,
            account_id=provisioned.account_id,
            actor_user_id=provisioned.owner_user_id,
            target_user_id=operator.id,
            role=OPERATOR,
        )
        return _Seed(
            account_id=provisioned.account_id,
            owner_id=provisioned.owner_user_id,
            admin_id=admin.id,
            operator_id=operator.id,
            outsider_id=outsider.id,
        )


def test_principal_resolves_bitrix_sub_and_requires_active_membership() -> None:
    owner_external_id = str(uuid4())
    outsider_external_id = str(uuid4())
    unknown_external_id = str(uuid4())

    async def scenario(engine: AsyncEngine) -> None:
        async with AsyncSession(engine) as session, session.begin():
            provisioned = await provision_account_with_owner(
                session,
                identity_provider=PROVIDER,
                external_account_id=str(uuid4()),
                external_owner_id=owner_external_id,
            )
            await provision_user(
                session,
                identity_provider=PROVIDER,
                external_user_id=outsider_external_id,
            )

        async with AsyncSession(engine) as session:
            owner_principal = await build_principal(
                session,
                VerifiedAccessToken(
                    subject=owner_external_id,
                    account_id=provisioned.account_id,
                    scopes=frozenset({"projects:read"}),
                ),
            )

            assert owner_principal.user_id == provisioned.owner_user_id
            assert owner_principal.account_id == provisioned.account_id
            assert owner_principal.role is OWNER

            with pytest.raises(ActiveMembershipRequired):
                await build_principal(
                    session,
                    VerifiedAccessToken(
                        subject=outsider_external_id,
                        account_id=provisioned.account_id,
                        scopes=frozenset(),
                    ),
                )
            with pytest.raises(UnknownIdentityError):
                await build_principal(
                    session,
                    VerifiedAccessToken(
                        subject=unknown_external_id,
                        account_id=provisioned.account_id,
                        scopes=frozenset(),
                    ),
                )

    _run_in_schema(scenario)


async def _memberships(engine: AsyncEngine, account_id: str) -> dict[str, str]:
    async with AsyncSession(engine) as session:
        rows = await session.execute(
            select(AccountMembership.user_id, AccountMembership.role).where(
                AccountMembership.account_id == account_id
            )
        )
        return dict(rows.all())


async def _audit_entries(
    engine: AsyncEngine, account_id: str, action: str
) -> list[tuple[str | None, str | None, dict]]:
    async with AsyncSession(engine) as session:
        rows = await session.scalars(
            select(AuditLog).where(AuditLog.account_id == account_id, AuditLog.action == action)
        )
        return [(row.actor_id, row.system_actor, row.diff) for row in rows]


def test_provisioning_is_idempotent_and_never_rebinds_owner() -> None:
    account_ext, owner_ext = str(uuid4()), str(uuid4())

    async def scenario(engine: AsyncEngine) -> None:
        async with AsyncSession(engine) as session, session.begin():
            first = await provision_account_with_owner(
                session,
                identity_provider=PROVIDER,
                external_account_id=account_ext,
                external_owner_id=owner_ext,
            )
        async with AsyncSession(engine) as session, session.begin():
            second = await provision_account_with_owner(
                session,
                identity_provider=PROVIDER,
                external_account_id=account_ext,
                external_owner_id=owner_ext,
            )
        assert first.owner_membership_created is True
        assert second.owner_membership_created is False
        assert second.account_id == first.account_id
        assert second.owner_user_id == first.owner_user_id

        with pytest.raises(IdentityConflictError):
            async with AsyncSession(engine) as session, session.begin():
                await provision_account_with_owner(
                    session,
                    identity_provider=PROVIDER,
                    external_account_id=account_ext,
                    external_owner_id=str(uuid4()),
                )

        assert await _memberships(engine, first.account_id) == {first.owner_user_id: "owner"}
        provisioned = await _audit_entries(engine, first.account_id, "account.provisioned")
        assert provisioned == [
            (None, PROVISIONING_SYSTEM_ACTOR, {"owner_user_id": first.owner_user_id})
        ]

    _run_in_schema(scenario)


def test_owner_role_cannot_be_granted_changed_or_removed() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)

        async def grant_owner(session: AsyncSession, actor_id: str) -> None:
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=actor_id,
                target_user_id=seed.outsider_id,
                role=OWNER,
            )

        async def promote_operator_to_owner(session: AsyncSession, actor_id: str) -> None:
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=actor_id,
                target_user_id=seed.operator_id,
                role=OWNER,
            )

        async def demote_owner(session: AsyncSession, actor_id: str) -> None:
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=actor_id,
                target_user_id=seed.owner_id,
                role=ADMIN,
            )

        async def remove_owner(session: AsyncSession, actor_id: str) -> None:
            await remove_member(
                session,
                account_id=seed.account_id,
                actor_user_id=actor_id,
                target_user_id=seed.owner_id,
            )

        async def operator_changes_admin(session: AsyncSession, actor_id: str) -> None:
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=actor_id,
                target_user_id=seed.admin_id,
                role=OPERATOR,
            )

        for operation, actor_id in [
            (grant_owner, seed.admin_id),
            (promote_operator_to_owner, seed.admin_id),
            (demote_owner, seed.admin_id),
            (remove_owner, seed.admin_id),
            (grant_owner, seed.owner_id),
            (demote_owner, seed.owner_id),
            (remove_owner, seed.owner_id),
            (operator_changes_admin, seed.operator_id),
        ]:
            with pytest.raises(AuthorizationError):
                async with AsyncSession(engine) as session, session.begin():
                    await operation(session, actor_id)

        async with AsyncSession(engine) as session, session.begin():
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.admin_id,
                target_user_id=seed.outsider_id,
                role=OPERATOR,
            )

        members = await _memberships(engine, seed.account_id)
        assert members[seed.owner_id] == "owner"
        assert members[seed.admin_id] == "admin"
        assert members[seed.operator_id] == "operator"
        assert members[seed.outsider_id] == "operator"

    _run_in_schema(scenario)


def test_database_allows_only_one_owner_per_account() -> None:
    account_ext, owner_ext, second_ext = str(uuid4()), str(uuid4()), str(uuid4())

    async def scenario(engine: AsyncEngine) -> None:
        async with AsyncSession(engine) as session, session.begin():
            first = await provision_account_with_owner(
                session,
                identity_provider=PROVIDER,
                external_account_id=account_ext,
                external_owner_id=owner_ext,
            )
        async with AsyncSession(engine) as session, session.begin():
            second = await provision_user(
                session, identity_provider=PROVIDER, external_user_id=second_ext
            )
            second_user_id = second.id

        with pytest.raises(IntegrityError):
            async with AsyncSession(engine) as session, session.begin():
                session.add(
                    AccountMembership(
                        account_id=first.account_id,
                        user_id=second_user_id,
                        role=AccountRole.OWNER.value,
                    )
                )

        assert await _memberships(engine, first.account_id) == {first.owner_user_id: "owner"}

    _run_in_schema(scenario)


def _project(account_id: str) -> Project:
    return Project(account_id=account_id, locale="ru", timezone="UTC", settings={}, status="active")


async def _add_dialog(
    session: AsyncSession, account_id: str, *, project: Project | None = None
) -> tuple[Project, DialogSession]:
    if project is None:
        project = _project(account_id)
        session.add(project)
        await session.flush()

    channel_id = f"web-{uuid4().hex}"
    session.add(Channel(project_id=project.id, channel_id=channel_id))
    agent = await session.scalar(select(Agent).where(Agent.project_id == project.id))
    if agent is None:
        agent = Agent(id=str(uuid4()), project_id=project.id, status="draft")
        session.add(agent)
        await session.flush()
    release = (
        await session.get(AgentRelease, agent.active_release_id)
        if agent.active_release_id is not None
        else None
    )
    if release is None:
        release = AgentRelease(
            id=str(uuid4()),
            project_id=project.id,
            agent_id=agent.id,
            version=1,
            snapshot={},
        )
        session.add(release)
        await session.flush()
        agent.status = "active"
        agent.active_release_id = release.id
        await session.flush()

    dialog = DialogSession(
        id=f"dialog-{uuid4().hex}",
        project_id=project.id,
        channel_id=channel_id,
        customer_id=f"customer-{uuid4().hex}",
        external_chat_id=f"chat-{uuid4().hex}",
        release_id=release.id,
        state="active",
        version=0,
    )
    session.add(dialog)
    await session.flush()
    return project, dialog


async def _add_open_escalation(
    session: AsyncSession, *, account_id: str, project_id: str, session_id: str
) -> Escalation:
    escalation = Escalation(
        project_id=project_id,
        account_id=account_id,
        session_id=session_id,
        reason="human_requested",
        summary="Operator requested",
        sla_deadline=datetime.now(UTC),
        status="open",
    )
    session.add(escalation)
    await session.flush()
    return escalation


def test_dialog_operator_access_and_assignment_are_account_scoped() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        other_account = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            project, first_dialog = await _add_dialog(session, seed.account_id)
            _, second_dialog = await _add_dialog(session, seed.account_id, project=project)
            other_project, other_dialog = await _add_dialog(session, other_account.account_id)
            other_escalation = await _add_open_escalation(
                session,
                account_id=other_account.account_id,
                project_id=other_project.id,
                session_id=other_dialog.id,
            )
            await assign_operator_to_project(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                target_user_id=seed.operator_id,
            )
            assert await has_owner_admin_project_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                user_id=seed.owner_id,
            )
            assert await has_owner_admin_project_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                user_id=seed.admin_id,
            )
            assert not await has_owner_admin_project_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                user_id=seed.operator_id,
            )
            assert not await has_owner_admin_project_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                user_id=other_account.owner_id,
            )
            with pytest.raises(LookupError, match="Escalation not found"):
                await claim_open_escalation(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.operator_id,
                    escalation_id=other_escalation.id,
                )
            assert not await has_assigned_dialog_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
                user_id=seed.operator_id,
            )

            await assign_operator_to_dialog(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                session_id=first_dialog.id,
                target_user_id=seed.operator_id,
            )
            assert await has_assigned_dialog_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
                user_id=seed.operator_id,
            )
            assert not await has_assigned_dialog_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=second_dialog.id,
                user_id=seed.operator_id,
            )
            with pytest.raises(MembershipError, match="already assigned"):
                await assign_operator_to_dialog(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.admin_id,
                    project_id=project.id,
                    session_id=second_dialog.id,
                    target_user_id=seed.operator_id,
                )

            replacement = await provision_user(
                session, identity_provider=PROVIDER, external_user_id=str(uuid4())
            )
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=replacement.id,
                role=OPERATOR,
            )
            await assign_operator_to_project(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                target_user_id=replacement.id,
            )
            await assign_operator_to_dialog(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.admin_id,
                project_id=project.id,
                session_id=first_dialog.id,
                target_user_id=replacement.id,
            )
            assert not await has_assigned_dialog_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
                user_id=seed.operator_id,
            )
            assert await has_assigned_dialog_access(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
                user_id=replacement.id,
            )

    _run_in_schema(scenario)


def test_open_escalation_route_claim_release_and_resolution() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            second_operator = await provision_user(
                session, identity_provider=PROVIDER, external_user_id=str(uuid4())
            )
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=second_operator.id,
                role=OPERATOR,
            )
            project, first_dialog = await _add_dialog(session, seed.account_id)
            _, second_dialog = await _add_dialog(session, seed.account_id, project=project)
            _, third_dialog = await _add_dialog(session, seed.account_id, project=project)
            for operator_id in (seed.operator_id, second_operator.id):
                await assign_operator_to_project(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    project_id=project.id,
                    target_user_id=operator_id,
                )
            roster = list(
                await session.scalars(
                    select(ProjectOperator).where(ProjectOperator.project_id == project.id)
                )
            )
            memberships = {
                membership.user_id: membership
                for membership in await session.scalars(
                    select(AccountMembership).where(AccountMembership.account_id == seed.account_id)
                )
            }
            for operator in roster:
                operator.created_at = datetime(
                    2025 if operator.membership_id == memberships[second_operator.id].id else 2026,
                    1,
                    1,
                    tzinfo=UTC,
                )
            await session.flush()

            first_escalation = await _add_open_escalation(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
            )
            second_escalation = await _add_open_escalation(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=second_dialog.id,
            )
            third_escalation = await _add_open_escalation(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=third_dialog.id,
            )

            first = await assign_open_escalation_to_first_free_operator(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
            )
            assert first is not None
            assert first.id == memberships[second_operator.id].id
            second = await assign_open_escalation_to_first_free_operator(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=second_dialog.id,
            )
            assert second is not None
            assert second.id == memberships[seed.operator_id].id
            assert (
                await assign_open_escalation_to_first_free_operator(
                    session,
                    account_id=seed.account_id,
                    project_id=project.id,
                    session_id=third_dialog.id,
                )
                is None
            )

            await release_dialog_assignment(
                session,
                account_id=seed.account_id,
                actor_user_id=second_operator.id,
                project_id=project.id,
                session_id=first_dialog.id,
            )
            assert first_escalation.assignee_membership_id is None
            reclaimed = await assign_open_escalation_to_first_free_operator(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=first_dialog.id,
            )
            assert reclaimed is not None and reclaimed.id == memberships[second_operator.id].id

            await resolve_open_escalation(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.operator_id,
                escalation_id=second_escalation.id,
            )
            claimed = await claim_open_escalation(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.operator_id,
                escalation_id=third_escalation.id,
            )
            assert claimed.assignee_membership_id == memberships[seed.operator_id].id
            with pytest.raises(MembershipError, match="not open and unassigned"):
                await claim_open_escalation(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=second_operator.id,
                    escalation_id=third_escalation.id,
                )

            await resolve_open_escalation(
                session,
                account_id=seed.account_id,
                actor_user_id=second_operator.id,
                escalation_id=first_escalation.id,
            )
            await resolve_open_escalation(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.operator_id,
                escalation_id=third_escalation.id,
            )
            assert first_escalation.status == "resolved"
            assert second_escalation.status == "resolved"
            assert third_escalation.status == "resolved"
            assert (
                await session.scalar(select(func.count()).select_from(DialogOperatorAssignment))
                == 0
            )

    _run_in_schema(scenario)


def test_disabling_or_removing_operator_clears_current_dialog_assignment() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            project, dialog = await _add_dialog(session, seed.account_id)
            await assign_operator_to_project(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                target_user_id=seed.operator_id,
            )
            escalation = await _add_open_escalation(
                session,
                account_id=seed.account_id,
                project_id=project.id,
                session_id=dialog.id,
            )
            await assign_operator_to_dialog(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                session_id=dialog.id,
                target_user_id=seed.operator_id,
            )
            await set_project_operator_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                target_user_id=seed.operator_id,
                status=AccessStatus.DISABLED,
            )
            assert await session.get(DialogOperatorAssignment, dialog.id) is None
            assert escalation.assignee_membership_id is None

            await set_project_operator_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                target_user_id=seed.operator_id,
                status=AccessStatus.ACTIVE,
            )
            await assign_operator_to_dialog(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                session_id=dialog.id,
                target_user_id=seed.operator_id,
            )
            await set_member_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
                status=AccessStatus.DISABLED,
            )
            assert await session.get(DialogOperatorAssignment, dialog.id) is None
            assert escalation.assignee_membership_id is None

            await set_member_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
                status=AccessStatus.ACTIVE,
            )
            await assign_operator_to_dialog(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=project.id,
                session_id=dialog.id,
                target_user_id=seed.operator_id,
            )
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
                role=ADMIN,
            )
            assert await session.get(DialogOperatorAssignment, dialog.id) is None
            assert escalation.assignee_membership_id is None
            await remove_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
            )
            assert (
                await session.scalar(
                    select(AccountMembership.id).where(
                        AccountMembership.account_id == seed.account_id,
                        AccountMembership.user_id == seed.operator_id,
                    )
                )
                is None
            )

    _run_in_schema(scenario)


def test_suspended_admin_loses_management_rights_and_can_be_restored() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)

        async with AsyncSession(engine) as session, session.begin():
            await set_member_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.admin_id,
                status=AccessStatus.DISABLED,
            )

        async with AsyncSession(engine) as session, session.begin():
            assert (
                await get_active_membership_role(
                    session, account_id=seed.account_id, user_id=seed.admin_id
                )
                is None
            )

        with pytest.raises(AuthorizationError):
            async with AsyncSession(engine) as session, session.begin():
                await add_member(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.admin_id,
                    target_user_id=seed.outsider_id,
                    role=OPERATOR,
                )

        async with AsyncSession(engine) as session, session.begin():
            await set_member_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.admin_id,
                status=AccessStatus.ACTIVE,
            )

        async with AsyncSession(engine) as session, session.begin():
            assert (
                await get_active_membership_role(
                    session, account_id=seed.account_id, user_id=seed.admin_id
                )
                is ADMIN
            )

    _run_in_schema(scenario)


def test_owner_membership_cannot_be_disabled() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)

        with pytest.raises(AuthorizationError):
            async with AsyncSession(engine) as session, session.begin():
                await set_member_status(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    target_user_id=seed.owner_id,
                    status=AccessStatus.DISABLED,
                )

        with pytest.raises(IntegrityError):
            async with AsyncSession(engine) as session, session.begin():
                await session.execute(
                    update(AccountMembership)
                    .where(AccountMembership.user_id == seed.owner_id)
                    .values(status=AccessStatus.DISABLED.value)
                )

        async with AsyncSession(engine) as session, session.begin():
            assert (
                await get_active_membership_role(
                    session, account_id=seed.account_id, user_id=seed.owner_id
                )
                is OWNER
            )

    _run_in_schema(scenario)


def test_operator_project_access_is_suspended_per_project() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            first, second = _project(seed.account_id), _project(seed.account_id)
            session.add_all([first, second])
            await session.flush()
            first_id, second_id = first.id, second.id
            for project_id in (first_id, second_id):
                await assign_operator_to_project(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    project_id=project_id,
                    target_user_id=seed.operator_id,
                )
            await set_project_operator_status(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                project_id=second_id,
                target_user_id=seed.operator_id,
                status=AccessStatus.DISABLED,
            )

        async with AsyncSession(engine) as session, session.begin():
            assert await has_active_project_operator_access(
                session,
                account_id=seed.account_id,
                project_id=first_id,
                user_id=seed.operator_id,
            )
            assert not await has_active_project_operator_access(
                session,
                account_id=seed.account_id,
                project_id=second_id,
                user_id=seed.operator_id,
            )

    _run_in_schema(scenario)


def test_operator_assignments_stay_within_their_account() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        other = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            own, foreign = _project(seed.account_id), _project(other.account_id)
            session.add_all([own, foreign])
            await session.flush()
            own_id, foreign_id = own.id, foreign.id

        with pytest.raises(LookupError):
            async with AsyncSession(engine) as session, session.begin():
                await assign_operator_to_project(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    project_id=foreign_id,
                    target_user_id=seed.operator_id,
                )

        with pytest.raises(MembershipError):
            async with AsyncSession(engine) as session, session.begin():
                await assign_operator_to_project(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    project_id=own_id,
                    target_user_id=seed.admin_id,
                )

    _run_in_schema(scenario)


def test_role_change_and_removal_clear_project_assignments() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)
        async with AsyncSession(engine) as session, session.begin():
            project = _project(seed.account_id)
            session.add(project)
            await session.flush()
            project_id = project.id
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.outsider_id,
                role=OPERATOR,
            )
            for target_id in (seed.operator_id, seed.outsider_id):
                await assign_operator_to_project(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    project_id=project_id,
                    target_user_id=target_id,
                )

        async with AsyncSession(engine) as session, session.begin():
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
                role=ADMIN,
            )
            await remove_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.outsider_id,
            )

        async with AsyncSession(engine) as session:
            assert await session.scalar(select(func.count()).select_from(ProjectOperator)) == 0

    _run_in_schema(scenario)


def test_membership_changes_are_idempotent_and_audited() -> None:
    async def scenario(engine: AsyncEngine) -> None:
        seed = await _seed_account(engine)

        async with AsyncSession(engine) as session, session.begin():
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.outsider_id,
                role=OPERATOR,
            )
        async with AsyncSession(engine) as session, session.begin():
            await add_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.outsider_id,
                role=OPERATOR,
            )
        with pytest.raises(MembershipError):
            async with AsyncSession(engine) as session, session.begin():
                await add_member(
                    session,
                    account_id=seed.account_id,
                    actor_user_id=seed.owner_id,
                    target_user_id=seed.outsider_id,
                    role=ADMIN,
                )

        async with AsyncSession(engine) as session, session.begin():
            await change_member_role(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.operator_id,
                role=ADMIN,
            )
        async with AsyncSession(engine) as session, session.begin():
            await remove_member(
                session,
                account_id=seed.account_id,
                actor_user_id=seed.owner_id,
                target_user_id=seed.outsider_id,
            )

        added = await _audit_entries(engine, seed.account_id, "membership.added")
        assert [entry for entry in added if entry[2]["user_id"] == seed.outsider_id] == [
            (seed.owner_id, None, {"user_id": seed.outsider_id, "role": "operator"})
        ]
        assert await _audit_entries(engine, seed.account_id, "membership.role_changed") == [
            (
                seed.owner_id,
                None,
                {"user_id": seed.operator_id, "from_role": "operator", "to_role": "admin"},
            )
        ]
        assert await _audit_entries(engine, seed.account_id, "membership.removed") == [
            (
                seed.owner_id,
                None,
                {"user_id": seed.outsider_id, "role": "operator"},
            )
        ]
        assert await _memberships(engine, seed.account_id) == {
            seed.owner_id: "owner",
            seed.admin_id: "admin",
            seed.operator_id: "admin",
        }

    _run_in_schema(scenario)
