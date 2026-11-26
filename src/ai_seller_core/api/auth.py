"""JWT authentication and request principal construction."""

from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Annotated, Any
from uuid import UUID

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    SecurityScopes,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.persistence.access_policy import Principal, missing_scopes
from ai_seller_core.persistence.base import get_session
from ai_seller_core.persistence.identity_service import resolve_user_id
from ai_seller_core.persistence.membership_service import (
    get_active_membership_role,
)

BITRIX_IDENTITY_PROVIDER = "bitrix"
_bearer_scheme = HTTPBearer(auto_error=False)


class InvalidAccessToken(ValueError):
    """Raised when a bearer token does not meet the access-token contract."""


class UnknownIdentityError(LookupError):
    """Raised when a verified token subject has no unique backend mapping."""


class ActiveMembershipRequired(PermissionError):
    """Raised when the mapped user has no active membership in the token account."""


@dataclass(frozen=True)
class VerifiedAccessToken:
    subject: str
    account_id: str
    scopes: frozenset[str]


def validate_public_keyring(settings: Settings) -> None:
    for key_id, pem in settings.jwt_public_keys.items():
        try:
            public_key = serialization.load_pem_public_key(pem.strip().encode("utf-8"))
        except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
            raise ValueError(f"JWT public key {key_id!r} must be a valid PEM public key") from exc

        if not isinstance(public_key, rsa.RSAPublicKey):
            raise ValueError(f"JWT public key {key_id!r} must be an RSA key")
        if public_key.key_size < 2048:
            raise ValueError(f"JWT public key {key_id!r} must be at least 2048 bits")


def verify_access_token(token: str, settings: Settings | None = None) -> VerifiedAccessToken:
    settings = settings or get_settings()
    if not token:
        raise InvalidAccessToken("Bearer token is empty")

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken("Bearer token header is invalid") from exc

    key_id = header.get("kid")
    if header.get("alg") != "RS256":
        raise InvalidAccessToken("Bearer token must use RS256")
    if not isinstance(key_id, str) or not key_id:
        raise InvalidAccessToken("Bearer token has no key ID")

    public_key = settings.jwt_public_keys.get(key_id)
    if public_key is None:
        raise InvalidAccessToken("Bearer token key ID is unknown")

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": [
                    "sub",
                    "account_id",
                    "scopes",
                    "iat",
                    "nbf",
                    "exp",
                    "iss",
                    "aud",
                ]
            },
        )
    except jwt.PyJWTError as exc:
        raise InvalidAccessToken("Bearer token claims or signature are invalid") from exc

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise InvalidAccessToken("Bearer token subject is invalid")
    try:
        UUID(subject)
    except ValueError as exc:
        raise InvalidAccessToken("Bearer token subject must be a Bitrix UUID") from exc

    raw_account_id = claims.get("account_id")
    if not isinstance(raw_account_id, str):
        raise InvalidAccessToken("Bearer token account ID is invalid")
    try:
        canonical_account_id = str(UUID(raw_account_id))
    except ValueError as exc:
        raise InvalidAccessToken("Bearer token account ID is invalid") from exc
    if raw_account_id != canonical_account_id:
        raise InvalidAccessToken("Bearer token account ID is not canonical")

    raw_scopes = claims.get("scopes")
    if not isinstance(raw_scopes, list) or any(
        not isinstance(scope, str) or not scope or scope != scope.strip() for scope in raw_scopes
    ):
        raise InvalidAccessToken("Bearer token scopes must be an array of strings")

    issued_at = claims.get("iat")
    not_before = claims.get("nbf")
    expires_at = claims.get("exp")
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, (int, float))
        or isinstance(not_before, bool)
        or not isinstance(not_before, (int, float))
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, (int, float))
    ):
        raise InvalidAccessToken("Bearer token timestamps are invalid")
    if issued_at > time():
        raise InvalidAccessToken("Bearer token was issued in the future")
    ttl_seconds = expires_at - issued_at
    max_ttl_seconds = settings.jwt_access_token_ttl_minutes * 60
    if ttl_seconds <= 0 or ttl_seconds > max_ttl_seconds:
        raise InvalidAccessToken("Bearer token exceeds the maximum TTL")

    return VerifiedAccessToken(
        subject=subject,
        account_id=canonical_account_id,
        scopes=frozenset(raw_scopes),
    )


async def _build_principal_in_transaction(
    session: AsyncSession, token: VerifiedAccessToken
) -> Principal:
    try:
        user_id = await resolve_user_id(
            session,
            identity_provider=BITRIX_IDENTITY_PROVIDER,
            external_user_id=token.subject,
        )
    except LookupError as exc:
        raise UnknownIdentityError("Token subject has no unique backend mapping") from exc

    role = await get_active_membership_role(session, account_id=token.account_id, user_id=user_id)
    if role is None:
        raise ActiveMembershipRequired("Token subject has no active membership in the account")

    return Principal(
        user_id=user_id,
        account_id=token.account_id,
        role=role,
        scopes=token.scopes,
    )


async def build_principal(session: AsyncSession, token: VerifiedAccessToken) -> Principal:
    if session.in_transaction():
        return await _build_principal_in_transaction(session, token)

    async with session.begin():
        return await _build_principal_in_transaction(session, token)


async def get_current_principal(
    security_scopes: SecurityScopes,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token = verify_access_token(credentials.credentials, settings)
    except InvalidAccessToken:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    try:
        principal = await build_principal(session, token)
    except UnknownIdentityError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except ActiveMembershipRequired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active account membership required",
        ) from None

    missing = missing_scopes(principal, security_scopes.scopes)
    if missing:
        required_scope = " ".join(sorted(missing))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient scope",
            headers={
                "WWW-Authenticate": (f'Bearer error="insufficient_scope", scope="{required_scope}"')
            },
        )

    return principal
