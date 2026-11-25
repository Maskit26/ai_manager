"""Invalidate entitlement snapshots with a fixed past timestamp.

Revision ID: 20261203_entitlement_invalidate
Revises: 20261202_membership_status
Create Date: 2026-12-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261203_entitlement_invalidate"
down_revision: str | None = "20261202_membership_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The service compares expires_at with the application clock, so the database clock
# must not be used here: a database clock ahead of the application would hide invalidation.
_INVALIDATORS: dict[str, str] = {
    "invalidate_entitlements_for_subscription": "account_id IN (NEW.account_id, OLD.account_id)",
    "invalidate_entitlements_for_balance": "account_id IN (NEW.account_id, OLD.account_id)",
    "invalidate_entitlements_for_tariff": (
        "account_id IN (SELECT account_id FROM subscriptions "
        "WHERE tariff_plan_id IN (NEW.id, OLD.id))"
    ),
    "invalidate_entitlements_for_agent_slot": (
        "account_id IN (SELECT account_id FROM projects "
        "WHERE id IN (NEW.project_id, OLD.project_id))"
    ),
}


def _replace_invalidators(expires_at_expr: str) -> None:
    for name, where_clause in _INVALIDATORS.items():
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {name}() RETURNS trigger AS $$
            BEGIN
                UPDATE entitlements
                SET expires_at = {expires_at_expr}
                WHERE {where_clause};
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql
            """
        )


def upgrade() -> None:
    _replace_invalidators("to_timestamp(0)")


def downgrade() -> None:
    _replace_invalidators("statement_timestamp()")
