import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from ai_seller_core.api.v1 import agents as agents_api
from ai_seller_core.api.v1 import members as members_api
from ai_seller_core.api.v1 import onboarding as onboarding_api
from ai_seller_core.api.v1 import projects as projects_api
from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.persistence.access_policy import AccountRole, Principal
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.models import Account, Agent, AgentDraft, Project


class _ScalarResult:
    def __init__(self, rows: list[Project]) -> None:
        self._rows = rows

    def all(self) -> list[Project]:
        return self._rows


class _FakeSession:
    def __init__(self, account: Account, projects: list[Project]) -> None:
        self.account = account
        self.projects = projects
        self._in_transaction = False

    def in_transaction(self) -> bool:
        return self._in_transaction

    @asynccontextmanager
    async def begin(self):
        self._in_transaction = True
        try:
            yield
        finally:
            self._in_transaction = False

    async def get(self, model: type[Any], object_id: str) -> Account | None:
        if model is Account and str(self.account.id) == str(object_id):
            return self.account
        return None

    async def scalars(self, statement: Any) -> _ScalarResult:
        values = set(statement.compile().params.values())
        account_id = next(
            (project.account_id for project in self.projects if project.account_id in values),
            None,
        )
        matching_statuses = {
            project.status for project in self.projects if project.status in values
        }
        rows = [
            project
            for project in self.projects
            if project.account_id == account_id
            and (not matching_statuses or project.status in matching_statuses)
        ]
        return _ScalarResult(rows)

    async def scalar(self, statement: Any) -> Project | None:
        values = set(statement.compile().params.values())
        for project in self.projects:
            if project.id in values and project.account_id in values:
                return project
        return None


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test:test@localhost/test",
        jwt_public_keys={"test-key": public_key},
        onboarding_server_credential=SecretStr("test-onboarding-server-secret-32-chars"),
    )
    account = Account(id=str(uuid4()), status="active")
    other_account = Account(id=str(uuid4()), status="active")
    project = Project(
        id=str(uuid4()),
        account_id=account.id,
        locale="ru-RU",
        timezone="Europe/Moscow",
        settings={"private_configuration": "must-not-be-returned"},
        status="active",
    )
    inactive_project = Project(
        id=str(uuid4()),
        account_id=account.id,
        locale="ru-RU",
        timezone="Europe/Moscow",
        settings={},
        status="archived",
    )
    foreign_project = Project(
        id=str(uuid4()),
        account_id=other_account.id,
        locale="en-US",
        timezone="UTC",
        settings={},
        status="active",
    )
    session = _FakeSession(account, [project, inactive_project, foreign_project])
    principal = Principal(
        user_id=str(uuid4()),
        account_id=str(account.id),
        role=AccountRole.ADMIN,
        scopes=frozenset({"account:read", "projects:read"}),
    )
    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(principal),
    )

    async def override_session():
        yield session

    from ai_seller_core.main import create_app

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    yield app, private_key, principal, project, foreign_project
    app.dependency_overrides.clear()


async def _principal_result(principal: Principal) -> Principal:
    return principal


def _token(private_key: Any, *, scopes: list[str] | None = None) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "ai-manager-web",
            "aud": "ai-manager-api",
            "sub": str(uuid4()),
            "account_id": str(uuid4()),
            "scopes": scopes or ["account:read", "projects:read"],
            "iat": now,
            "nbf": now,
            "exp": now + 60,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _run(coro):
    return asyncio.run(coro)


def test_api_requires_authentication(api_client):
    app, _private_key, _principal, _project, _foreign_project = api_client

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            unauthenticated = await client.get("/api/v1/account")
            invalid_token = await client.get(
                "/api/v1/account",
                headers={"Authorization": "Bearer not-a-valid-token"},
            )
            return unauthenticated, invalid_token

    response, invalid_token = _run(request())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.json()["error"]["request_id"]
    assert invalid_token.status_code == 401
    assert invalid_token.json()["error"]["code"] == "unauthorized"


def test_api_enforces_scope_and_role(api_client, monkeypatch):
    app, private_key, principal, _project, _foreign_project = api_client
    missing_scope_principal = Principal(
        user_id=principal.user_id,
        account_id=principal.account_id,
        role=AccountRole.ADMIN,
        scopes=frozenset({"account:read"}),
    )
    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(missing_scope_principal),
    )

    operator = Principal(
        user_id=str(uuid4()),
        account_id=principal.account_id,
        role=AccountRole.OPERATOR,
        scopes=frozenset({"projects:read"}),
    )

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response_without_scope = await client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {_token(private_key)}"},
            )
            monkeypatch.setattr(
                "ai_seller_core.api.auth.build_principal",
                lambda *_args, **_kwargs: _principal_result(operator),
            )
            response_without_role = await client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {_token(private_key)}"},
            )
            return response_without_scope, response_without_role

    response_without_scope, response_without_role = _run(requests())
    assert response_without_scope.status_code == 403
    assert response_without_role.status_code == 403
    assert response_without_role.json()["error"]["code"] == "forbidden"


def test_api_hides_cross_tenant_projects_and_settings(api_client):
    app, private_key, _principal, project, foreign_project = api_client
    headers = {"Authorization": f"Bearer {_token(private_key)}"}

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            foreign_response = await client.get(
                f"/api/v1/projects/{foreign_project.id}",
                headers=headers,
            )
            own_response = await client.get(
                f"/api/v1/projects/{project.id}",
                headers=headers,
            )
            invalid_id_response = await client.get(
                "/api/v1/projects/not-a-uuid",
                headers=headers,
            )
            account_response = await client.get("/api/v1/account", headers=headers)
            list_response = await client.get("/api/v1/projects", headers=headers)
            active_list_response = await client.get(
                "/api/v1/projects?status=active",
                headers=headers,
            )
            return (
                foreign_response,
                own_response,
                invalid_id_response,
                account_response,
                list_response,
                active_list_response,
            )

    (
        foreign_response,
        own_response,
        invalid_id_response,
        account_response,
        list_response,
        active_list_response,
    ) = _run(requests())
    assert foreign_response.status_code == 404
    assert foreign_response.json()["error"]["code"] == "not_found"
    assert own_response.status_code == 200
    assert "private_configuration" not in own_response.text
    assert invalid_id_response.status_code == 422
    assert invalid_id_response.json()["error"]["code"] == "validation_error"
    assert "not-a-uuid" not in invalid_id_response.text
    assert account_response.status_code == 200
    assert list_response.status_code == 200
    assert len(list_response.json()["items"]) == 2
    assert active_list_response.status_code == 200
    assert len(active_list_response.json()["items"]) == 1


def test_api_openapi_lists_versioned_routes_and_error_examples(api_client):
    app, _private_key, _principal, _project, _foreign_project = api_client

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/openapi.json")

    response = _run(request())

    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/account" in schema["paths"]
    assert "/api/v1/account/members" in schema["paths"]
    assert "/api/v1/account/members/{user_id}" in schema["paths"]
    assert "/api/v1/internal/onboarding/accounts" in schema["paths"]
    assert "/api/v1/projects" in schema["paths"]
    assert "/api/v1/projects/{project_id}/agent" in schema["paths"]
    assert "/api/v1/projects/{project_id}/agent/draft" in schema["paths"]
    assert "/api/v1/projects/{project_id}/agent/publish" in schema["paths"]
    error_schema = schema["components"]["schemas"]["ErrorResponse"]
    assert error_schema["examples"][0]["error"]["code"] == "forbidden"
    project_responses = schema["paths"]["/api/v1/projects"]["get"]["responses"]
    assert {"400", "401", "402", "403", "404", "409", "413", "415", "422", "429", "500"} <= set(
        project_responses
    )


def test_onboarding_uses_server_shared_secret_and_provisioning_service(
    api_client,
    monkeypatch,
) -> None:
    app, _private_key, _principal, _project, _foreign_project = api_client
    account_id = str(uuid4())
    owner_user_id = str(uuid4())
    called: dict[str, Any] = {}

    async def provision(_session, **kwargs):
        called.update(kwargs)
        return SimpleNamespace(
            account_id=account_id,
            owner_user_id=owner_user_id,
            owner_membership_created=True,
        )

    async def execute_once(session, *, action, **_kwargs):
        assert session.in_transaction()
        return await action(), False

    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(
            Principal(
                user_id=str(uuid4()),
                account_id=account_id,
                role=AccountRole.ADMIN,
                scopes=frozenset(),
            )
        ),
    )
    monkeypatch.setattr(onboarding_api, "provision_account_with_owner", provision)
    monkeypatch.setattr(onboarding_api, "execute_idempotently", execute_once)

    async def requests():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            missing_credential = await client.post(
                "/api/v1/internal/onboarding/accounts",
                headers={"Idempotency-Key": "missing-credential"},
                json={
                    "external_account_id": "company-42",
                    "external_owner_id": "user-108",
                },
            )
            accepted = await client.post(
                "/api/v1/internal/onboarding/accounts",
                headers={
                    "Authorization": "Bearer test-onboarding-server-secret-32-chars",
                    "Idempotency-Key": "onboarding-1",
                },
                json={
                    "external_account_id": "company-42",
                    "external_owner_id": "user-108",
                },
            )
            return missing_credential, accepted

    missing_credential, accepted = _run(requests())

    assert missing_credential.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json() == {
        "account_id": account_id,
        "owner_user_id": owner_user_id,
        "owner_membership_created": True,
    }
    assert called == {
        "identity_provider": "bitrix",
        "external_account_id": "company-42",
        "external_owner_id": "user-108",
    }


def test_project_create_route_delegates_to_service_and_is_idempotent(
    api_client,
    monkeypatch,
) -> None:
    app, private_key, principal, _project, _foreign_project = api_client
    manager = Principal(
        user_id=principal.user_id,
        account_id=principal.account_id,
        role=AccountRole.ADMIN,
        scopes=frozenset({"projects:write"}),
    )
    created = Project(
        id=str(uuid4()),
        account_id=principal.account_id,
        locale="ru-RU",
        timezone="Europe/Moscow",
        settings={},
        status="active",
    )
    called: dict[str, Any] = {}

    async def create(_session, **kwargs):
        called.update(kwargs)
        return created

    async def execute_once(session, *, action, **_kwargs):
        assert session.in_transaction()
        return await action(), False

    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(manager),
    )
    monkeypatch.setattr(projects_api, "create_project_record", create)
    monkeypatch.setattr(projects_api, "execute_idempotently", execute_once)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post(
                "/api/v1/projects",
                headers={
                    "Authorization": f"Bearer {_token(private_key)}",
                    "Idempotency-Key": "project-create-1",
                },
                json={"locale": "ru-RU", "timezone": "Europe/Moscow"},
            )

    response = _run(request())

    assert response.status_code == 201
    assert response.json() == {
        "id": created.id,
        "locale": "ru-RU",
        "timezone": "Europe/Moscow",
        "status": "active",
    }
    assert called == {
        "principal": manager,
        "locale": "ru-RU",
        "timezone": "Europe/Moscow",
    }


def test_account_members_route_uses_membership_service(
    api_client,
    monkeypatch,
) -> None:
    app, private_key, principal, _project, _foreign_project = api_client
    reader = Principal(
        user_id=principal.user_id,
        account_id=principal.account_id,
        role=AccountRole.ADMIN,
        scopes=frozenset({"members:read"}),
    )
    members = [
        SimpleNamespace(
            user_id=str(uuid4()),
            role="operator",
            status="active",
        )
    ]
    called: dict[str, Any] = {}

    async def list_members(_session, **kwargs):
        called.update(kwargs)
        return members

    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(reader),
    )
    monkeypatch.setattr(members_api, "list_account_memberships", list_members)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get(
                "/api/v1/account/members",
                headers={"Authorization": f"Bearer {_token(private_key)}"},
            )

    response = _run(request())

    assert response.status_code == 200
    assert response.json()["items"][0]["role"] == "operator"
    assert called == {
        "account_id": principal.account_id,
        "actor_user_id": principal.user_id,
    }


def test_agent_create_route_calls_service_and_returns_draft(
    api_client,
    monkeypatch,
) -> None:
    app, private_key, principal, project, _foreign_project = api_client
    writer = Principal(
        user_id=principal.user_id,
        account_id=principal.account_id,
        role=AccountRole.ADMIN,
        scopes=frozenset({"agents:write"}),
    )
    agent = Agent(id=str(uuid4()), project_id=project.id, status="draft")
    draft = AgentDraft(project_id=project.id, agent_id=agent.id, config={})
    called: dict[str, Any] = {}

    async def create(_session, **kwargs):
        called.update(kwargs)
        return agent

    async def get_state(_session, **_kwargs):
        return agent, draft

    async def execute_once(session, *, action, **_kwargs):
        assert session.in_transaction()
        return await action(), False

    monkeypatch.setattr(
        "ai_seller_core.api.auth.build_principal",
        lambda *_args, **_kwargs: _principal_result(writer),
    )
    monkeypatch.setattr(agents_api, "create_agent", create)
    monkeypatch.setattr(agents_api, "get_agent_state", get_state)
    monkeypatch.setattr(agents_api, "execute_idempotently", execute_once)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post(
                f"/api/v1/projects/{project.id}/agent",
                headers={
                    "Authorization": f"Bearer {_token(private_key)}",
                    "Idempotency-Key": "agent-create-1",
                },
            )

    response = _run(request())

    assert response.status_code == 201
    assert response.json() == {
        "id": agent.id,
        "project_id": project.id,
        "status": "draft",
        "active_release_id": None,
        "draft_config": {},
    }
    assert called == {
        "principal": writer,
        "project_id": project.id,
    }
