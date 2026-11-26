from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, SecurityScopes

from ai_seller_core.api.auth import (
    ActiveMembershipRequired,
    InvalidAccessToken,
    Principal,
    UnknownIdentityError,
    VerifiedAccessToken,
    build_principal,
    get_current_principal,
    validate_public_keyring,
    verify_access_token,
)
from ai_seller_core.core.config import Settings
from ai_seller_core.persistence.access_policy import AccountRole
from ai_seller_core.persistence.identity_service import resolve_user_id

DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5432/test"
ACCOUNT_ID = str(uuid4())
SUBJECT = str(uuid4())


@pytest.fixture(scope="module")
def key_pair() -> tuple[rsa.RSAPrivateKey, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_key, public_key.decode("utf-8")


def make_settings(public_key: str, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        jwt_public_keys={"test-key": public_key},
        **overrides,
    )


def make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    claims: dict[str, Any] | None = None,
    time_offsets: dict[str, int] | None = None,
    remove_claims: tuple[str, ...] = (),
    key_id: str = "test-key",
    algorithm: str = "RS256",
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": "ai-manager-web",
        "aud": "ai-manager-api",
        "sub": SUBJECT,
        "account_id": ACCOUNT_ID,
        "scopes": ["dialogs:read"],
        "iat": now,
        "nbf": now,
        "exp": now + 60,
    }
    if time_offsets:
        payload.update({claim: now + offset for claim, offset in time_offsets.items()})
    if claims:
        payload.update(claims)
    for claim in remove_claims:
        payload.pop(claim, None)
    return jwt.encode(
        payload,
        private_key,
        algorithm=algorithm,
        headers={"kid": key_id},
    )


def test_verify_access_token_uses_kid_and_returns_claims(
    key_pair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    private_key, public_key = key_pair
    settings = make_settings(public_key)
    token = make_token(private_key)

    validate_public_keyring(settings)
    verified = verify_access_token(token, settings)

    assert verified.subject == SUBJECT
    assert verified.account_id == ACCOUNT_ID
    assert verified.scopes == frozenset({"dialogs:read"})


def test_verify_access_token_accepts_old_and_new_keys_during_rotation(
    key_pair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    old_private_key, old_public_key = key_pair
    new_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new_public_key = new_private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    settings = Settings(
        _env_file=None,
        database_url=DATABASE_URL,
        jwt_public_keys={
            "old-key": old_public_key,
            "new-key": new_public_key.decode("utf-8"),
        },
    )

    validate_public_keyring(settings)
    for key_id, private_key in (
        ("old-key", old_private_key),
        ("new-key", new_private_key),
    ):
        verified = verify_access_token(
            make_token(private_key, key_id=key_id),
            settings,
        )
        assert verified.subject == SUBJECT


@pytest.mark.parametrize(
    ("token_options", "settings_overrides"),
    [
        ({"remove_claims": ("iat",)}, {}),
        ({"remove_claims": ("nbf",)}, {}),
        ({"claims": {"aud": "other-api"}}, {}),
        ({"claims": {"iss": "other-issuer"}}, {}),
        ({"time_offsets": {"exp": 16 * 60}}, {}),
        (
            {"time_offsets": {"iat": 60, "nbf": 0, "exp": 120}},
            {},
        ),
        ({"claims": {"scopes": "dialogs:read"}}, {}),
        ({"claims": {"account_id": "not-a-uuid"}}, {}),
        ({"time_offsets": {"nbf": 300, "exp": 600}}, {}),
        ({"claims": {"nbf": True}}, {}),
        (
            {"time_offsets": {"iat": -120, "nbf": -120, "exp": -1}},
            {},
        ),
    ],
)
def test_verify_access_token_rejects_invalid_claims_and_ttl(
    key_pair: tuple[rsa.RSAPrivateKey, str],
    token_options: dict[str, Any],
    settings_overrides: dict[str, object],
) -> None:
    private_key, public_key = key_pair
    token = make_token(private_key, **token_options)

    with pytest.raises(InvalidAccessToken):
        verify_access_token(token, make_settings(public_key, **settings_overrides))


def test_verify_access_token_rejects_wrong_algorithm_unknown_kid_and_signature(
    key_pair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    private_key, public_key = key_pair
    settings = make_settings(public_key)
    wrong_algorithm = jwt.encode(
        {
            "iss": "ai-manager-web",
            "aud": "ai-manager-api",
            "sub": SUBJECT,
            "account_id": ACCOUNT_ID,
            "scopes": [],
            "iat": int(time.time()),
            "nbf": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "x" * 32,
        algorithm="HS256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(InvalidAccessToken):
        verify_access_token(wrong_algorithm, settings)

    with pytest.raises(InvalidAccessToken):
        verify_access_token(make_token(private_key, key_id="unknown"), settings)

    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidAccessToken):
        verify_access_token(make_token(other_private_key), settings)


def test_validate_public_keyring_rejects_invalid_pem() -> None:
    settings = make_settings("not a PEM key")

    with pytest.raises(ValueError, match="valid PEM"):
        validate_public_keyring(settings)


class _Session:
    def in_transaction(self) -> bool:
        return False

    @asynccontextmanager
    async def begin(self):
        yield


@pytest.mark.anyio
async def test_build_principal_resolves_bitrix_mapping_and_current_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = VerifiedAccessToken(
        subject=SUBJECT,
        account_id=ACCOUNT_ID,
        scopes=frozenset({"dialogs:read"}),
    )
    session = _Session()
    resolve = AsyncMock(return_value="backend-user-id")
    membership = AsyncMock(return_value=AccountRole.ADMIN)
    monkeypatch.setattr("ai_seller_core.api.auth.resolve_user_id", resolve)
    monkeypatch.setattr("ai_seller_core.api.auth.get_active_membership_role", membership)

    principal = await build_principal(session, token)

    assert principal == Principal(
        user_id="backend-user-id",
        account_id=ACCOUNT_ID,
        role=AccountRole.ADMIN,
        scopes=frozenset({"dialogs:read"}),
    )
    resolve.assert_awaited_once_with(
        session,
        identity_provider="bitrix",
        external_user_id=SUBJECT,
    )
    membership.assert_awaited_once()
    assert membership.await_args.kwargs == {
        "account_id": ACCOUNT_ID,
        "user_id": "backend-user-id",
    }


@pytest.mark.anyio
async def test_build_principal_rejects_unknown_sub_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve = AsyncMock(side_effect=LookupError("not mapped"))
    membership = AsyncMock()
    monkeypatch.setattr("ai_seller_core.api.auth.resolve_user_id", resolve)
    monkeypatch.setattr("ai_seller_core.api.auth.get_active_membership_role", membership)
    token = VerifiedAccessToken(
        subject=SUBJECT,
        account_id=ACCOUNT_ID,
        scopes=frozenset(),
    )

    with pytest.raises(UnknownIdentityError):
        await build_principal(_Session(), token)

    assert resolve.await_args.kwargs["external_user_id"] == SUBJECT
    assert "email" not in resolve.await_args.kwargs
    assert "name" not in resolve.await_args.kwargs
    membership.assert_not_awaited()


@pytest.mark.anyio
async def test_build_principal_rejects_missing_active_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ai_seller_core.api.auth.resolve_user_id",
        AsyncMock(return_value="backend-user-id"),
    )
    monkeypatch.setattr(
        "ai_seller_core.api.auth.get_active_membership_role",
        AsyncMock(return_value=None),
    )
    token = VerifiedAccessToken(
        subject=SUBJECT,
        account_id=ACCOUNT_ID,
        scopes=frozenset(),
    )

    with pytest.raises(ActiveMembershipRequired):
        await build_principal(_Session(), token)


@pytest.mark.anyio
async def test_current_principal_enforces_route_scopes(
    key_pair: tuple[rsa.RSAPrivateKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key, public_key = key_pair
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=make_token(private_key))
    build = AsyncMock(
        return_value=Principal(
            user_id="backend-user-id",
            account_id=ACCOUNT_ID,
            role=AccountRole.ADMIN,
            scopes=frozenset({"dialogs:read"}),
        )
    )
    monkeypatch.setattr("ai_seller_core.api.auth.build_principal", build)

    with pytest.raises(HTTPException) as error:
        await get_current_principal(
            SecurityScopes(scopes=["dialogs:write"]),
            credentials,
            _Session(),
            make_settings(public_key),
        )

    assert error.value.status_code == status.HTTP_403_FORBIDDEN
    build.assert_awaited_once()


@pytest.mark.anyio
async def test_current_principal_requires_bearer_credentials(
    key_pair: tuple[rsa.RSAPrivateKey, str],
) -> None:
    _, public_key = key_pair

    with pytest.raises(HTTPException) as error:
        await get_current_principal(
            SecurityScopes(scopes=[]),
            None,
            _Session(),
            make_settings(public_key),
        )

    assert error.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.anyio
async def test_resolve_user_id_rejects_ambiguous_mapping() -> None:
    result = Mock()
    result.all.return_value = ["backend-user-a", "backend-user-b"]
    session = Mock()
    session.in_transaction.return_value = True
    session.scalars = AsyncMock(return_value=result)

    with pytest.raises(LookupError, match="ambiguous"):
        await resolve_user_id(
            session,
            identity_provider="bitrix",
            external_user_id=SUBJECT,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("principal_error", "expected_status"),
    [
        (UnknownIdentityError("unknown subject"), status.HTTP_401_UNAUTHORIZED),
        (
            ActiveMembershipRequired("inactive membership"),
            status.HTTP_403_FORBIDDEN,
        ),
    ],
)
async def test_current_principal_maps_identity_and_membership_errors(
    key_pair: tuple[rsa.RSAPrivateKey, str],
    monkeypatch: pytest.MonkeyPatch,
    principal_error: Exception,
    expected_status: int,
) -> None:
    private_key, public_key = key_pair
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=make_token(private_key))
    build = AsyncMock(side_effect=principal_error)
    monkeypatch.setattr("ai_seller_core.api.auth.build_principal", build)

    with pytest.raises(HTTPException) as error:
        await get_current_principal(
            SecurityScopes(scopes=[]),
            credentials,
            _Session(),
            make_settings(public_key),
        )

    assert error.value.status_code == expected_status
