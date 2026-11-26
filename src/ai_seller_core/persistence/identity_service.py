"""Provisioning and resolution for external account and user identities."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import (
    Account,
    AccountIdentity,
    User,
    UserIdentity,
    new_id,
)


class IdentityConflictError(ValueError):
    """Raised when an external identity is already linked to another backend entity."""


def _require_transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise RuntimeError("Identity services require an active database transaction")


def _validate_identity(identity_provider: str, external_id: str) -> None:
    if not identity_provider or not identity_provider.strip():
        raise ValueError("identity_provider must not be empty")
    if not external_id or not external_id.strip():
        raise ValueError("external identity must not be empty")


async def _find_account_identity(
    session: AsyncSession, identity_provider: str, external_account_id: str
) -> AccountIdentity | None:
    return await session.scalar(
        select(AccountIdentity).where(
            AccountIdentity.identity_provider == identity_provider,
            AccountIdentity.external_account_id == external_account_id,
        )
    )


async def _find_user_identity(
    session: AsyncSession, identity_provider: str, external_user_id: str
) -> UserIdentity | None:
    return await session.scalar(
        select(UserIdentity).where(
            UserIdentity.identity_provider == identity_provider,
            UserIdentity.external_user_id == external_user_id,
        )
    )


async def link_account_identity(
    session: AsyncSession,
    *,
    account_id: str,
    identity_provider: str,
    external_account_id: str,
) -> AccountIdentity:
    _require_transaction(session)
    _validate_identity(identity_provider, external_account_id)
    if await session.get(Account, account_id) is None:
        raise LookupError("Backend account not found")

    statement = (
        pg_insert(AccountIdentity)
        .values(
            id=new_id(),
            account_id=account_id,
            identity_provider=identity_provider,
            external_account_id=external_account_id,
        )
        .on_conflict_do_nothing(constraint="uq_account_identities_provider_external_id")
        .returning(AccountIdentity.id)
    )
    identity_id = await session.scalar(statement)
    if identity_id is not None:
        identity = await session.get(AccountIdentity, identity_id)
        if identity is None:
            raise RuntimeError("Inserted account identity could not be reloaded")
        return identity

    existing = await _find_account_identity(session, identity_provider, external_account_id)
    if existing is None:
        raise RuntimeError("Conflicting account identity is not visible")
    if existing.account_id != account_id:
        raise IdentityConflictError(
            "External account identity is already linked to another backend account"
        )
    return existing


async def link_user_identity(
    session: AsyncSession,
    *,
    user_id: str,
    identity_provider: str,
    external_user_id: str,
) -> UserIdentity:
    _require_transaction(session)
    _validate_identity(identity_provider, external_user_id)
    if await session.get(User, user_id) is None:
        raise LookupError("Backend user not found")

    statement = (
        pg_insert(UserIdentity)
        .values(
            id=new_id(),
            user_id=user_id,
            identity_provider=identity_provider,
            external_user_id=external_user_id,
        )
        .on_conflict_do_nothing(constraint="uq_user_identities_provider_external_id")
        .returning(UserIdentity.id)
    )
    identity_id = await session.scalar(statement)
    if identity_id is not None:
        identity = await session.get(UserIdentity, identity_id)
        if identity is None:
            raise RuntimeError("Inserted user identity could not be reloaded")
        return identity

    existing = await _find_user_identity(session, identity_provider, external_user_id)
    if existing is None:
        raise RuntimeError("Conflicting user identity is not visible")
    if existing.user_id != user_id:
        raise IdentityConflictError(
            "External user identity is already linked to another backend user"
        )
    return existing


async def provision_account(
    session: AsyncSession,
    *,
    identity_provider: str,
    external_account_id: str,
    status: str = "active",
) -> Account:
    _require_transaction(session)
    _validate_identity(identity_provider, external_account_id)
    existing = await _find_account_identity(session, identity_provider, external_account_id)
    if existing is not None:
        account = await session.get(Account, existing.account_id)
        if account is None:
            raise RuntimeError("Account identity references a missing backend account")
        return account

    account = Account(status=status)
    session.add(account)
    await session.flush()
    try:
        identity = await link_account_identity(
            session,
            account_id=account.id,
            identity_provider=identity_provider,
            external_account_id=external_account_id,
        )
    except IdentityConflictError as conflict:
        await session.delete(account)
        await session.flush()
        existing = await _find_account_identity(session, identity_provider, external_account_id)
        if existing is None:
            raise RuntimeError("Conflicting account identity is not visible") from conflict
        account = await session.get(Account, existing.account_id)
        if account is None:
            raise RuntimeError(
                "Account identity references a missing backend account"
            ) from conflict
        return account
    if identity.account_id != account.id:
        await session.delete(account)
        await session.flush()
        existing_account = await session.get(Account, identity.account_id)
        if existing_account is None:
            raise RuntimeError("Account identity references a missing backend account")
        return existing_account
    return account


async def provision_user(
    session: AsyncSession,
    *,
    identity_provider: str,
    external_user_id: str,
) -> User:
    _require_transaction(session)
    _validate_identity(identity_provider, external_user_id)
    existing = await _find_user_identity(session, identity_provider, external_user_id)
    if existing is not None:
        user = await session.get(User, existing.user_id)
        if user is None:
            raise RuntimeError("User identity references a missing backend user")
        return user

    user = User()
    session.add(user)
    await session.flush()
    try:
        identity = await link_user_identity(
            session,
            user_id=user.id,
            identity_provider=identity_provider,
            external_user_id=external_user_id,
        )
    except IdentityConflictError as conflict:
        await session.delete(user)
        await session.flush()
        existing = await _find_user_identity(session, identity_provider, external_user_id)
        if existing is None:
            raise RuntimeError("Conflicting user identity is not visible") from conflict
        user = await session.get(User, existing.user_id)
        if user is None:
            raise RuntimeError("User identity references a missing backend user") from conflict
        return user
    if identity.user_id != user.id:
        await session.delete(user)
        await session.flush()
        existing_user = await session.get(User, identity.user_id)
        if existing_user is None:
            raise RuntimeError("User identity references a missing backend user")
        return existing_user
    return user


async def resolve_account_id(
    session: AsyncSession, *, identity_provider: str, external_account_id: str
) -> str:
    _require_transaction(session)
    _validate_identity(identity_provider, external_account_id)
    identity = await _find_account_identity(session, identity_provider, external_account_id)
    if identity is None:
        raise LookupError("External account identity not found")
    return identity.account_id


async def resolve_user_id(
    session: AsyncSession, *, identity_provider: str, external_user_id: str
) -> str:
    _require_transaction(session)
    _validate_identity(identity_provider, external_user_id)
    user_ids = (
        await session.scalars(
            select(UserIdentity.user_id)
            .where(
                UserIdentity.identity_provider == identity_provider,
                UserIdentity.external_user_id == external_user_id,
            )
            .limit(2)
        )
    ).all()
    if not user_ids:
        raise LookupError("External user identity not found")
    if len(user_ids) > 1:
        raise LookupError("External user identity is ambiguous")
    return user_ids[0]
