from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import DialogSession


@dataclass(frozen=True)
class SessionLease:
    session_id: str
    owner: str
    fence: int
    lease_until: datetime


async def acquire_session_lease(
    session: AsyncSession,
    *,
    session_id: str,
    owner: str,
    lease_seconds: int,
) -> SessionLease | None:
    now = datetime.now(UTC)
    dialog = await session.scalar(
        select(DialogSession).where(DialogSession.id == session_id).with_for_update()
    )
    if dialog is None:
        raise LookupError(f"Dialog session {session_id!r} does not exist")
    if dialog.lease_until is not None and dialog.lease_until > now:
        return None

    dialog.lease_fence += 1
    dialog.lease_owner = owner
    dialog.lease_until = now + timedelta(seconds=lease_seconds)
    await session.flush()
    return SessionLease(session_id, owner, dialog.lease_fence, dialog.lease_until)


async def renew_session_lease(
    session: AsyncSession,
    *,
    lease: SessionLease,
    lease_seconds: int,
) -> SessionLease | None:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")

    dialog = await session.scalar(
        select(DialogSession)
        .where(DialogSession.id == lease.session_id)
        .with_for_update()
    )
    if (
        dialog is None
        or dialog.lease_owner != lease.owner
        or dialog.lease_fence != lease.fence
    ):
        return None

    lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
    dialog.lease_until = lease_until
    await session.flush()
    return SessionLease(lease.session_id, lease.owner, lease.fence, lease_until)


async def release_session_lease(
    session: AsyncSession,
    *,
    lease: SessionLease,
) -> bool:
    dialog = await session.scalar(
        select(DialogSession)
        .where(DialogSession.id == lease.session_id)
        .with_for_update()
    )
    if (
        dialog is None
        or dialog.lease_owner != lease.owner
        or dialog.lease_fence != lease.fence
    ):
        return False
    dialog.lease_owner = None
    dialog.lease_until = None
    return True


async def assert_current_fence(
    session: AsyncSession,
    *,
    lease: SessionLease,
) -> None:
    dialog = await session.scalar(
        select(DialogSession)
        .where(DialogSession.id == lease.session_id)
        .with_for_update()
    )
    if (
        dialog is None
        or dialog.lease_owner != lease.owner
        or dialog.lease_fence != lease.fence
    ):
        raise RuntimeError("Session lease was fenced by a newer owner")
