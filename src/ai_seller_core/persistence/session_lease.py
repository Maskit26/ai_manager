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
