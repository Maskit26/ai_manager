import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.persistence.access_policy import AccountRole, Principal
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.models import Account, Project


class _ScalarResult:
    def __init__(self, rows: list[Project]) -> None:
        self._rows = rows

    def all(self) -> list[Project]:
        return self._rows


class _FakeSession:
    def __init__(self, account: Account, projects: list[Project]) -> None:
        self.account = account
        self.projects = projects

    def in_transaction(self) -> bool:
        return False

    @asynccontextmanager
    async def begin(self):
        yield

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
    assert "/api/v1/projects" in schema["paths"]
    error_schema = schema["components"]["schemas"]["ErrorResponse"]
    assert error_schema["examples"][0]["error"]["code"] == "forbidden"
    project_responses = schema["paths"]["/api/v1/projects"]["get"]["responses"]
    assert {"400", "401", "402", "403", "404", "409", "413", "415", "422", "429", "500"} <= set(
        project_responses
    )
