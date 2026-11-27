import asyncio
from types import SimpleNamespace

import pytest

from ai_seller_core.persistence.membership_service import (
    AccessStatus,
    MembershipError,
    set_member_status,
)


class _TransactionSession:
    def in_transaction(self) -> bool:
        return True


def test_account_owner_cannot_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    membership = SimpleNamespace(role="owner", status="active")

    async def verify() -> None:
        async def no_op(*_args, **_kwargs) -> None:
            return None

        async def owner_membership(*_args, **_kwargs):
            return membership

        monkeypatch.setattr(
            "ai_seller_core.persistence.membership_service._lock_account",
            no_op,
        )
        monkeypatch.setattr(
            "ai_seller_core.persistence.membership_service._require_membership",
            owner_membership,
        )
        monkeypatch.setattr(
            "ai_seller_core.persistence.membership_service._authorize",
            no_op,
        )

        with pytest.raises(MembershipError, match="owner cannot be disabled"):
            await set_member_status(
                _TransactionSession(),
                account_id="account-id",
                actor_user_id="owner-id",
                target_user_id="owner-id",
                status=AccessStatus.DISABLED,
            )

    asyncio.run(verify())
