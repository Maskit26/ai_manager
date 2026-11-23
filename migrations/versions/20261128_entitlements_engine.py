"""Add entitlement snapshots and source invalidation triggers.

Revision ID: 20261128_entitlements_engine
Revises: 20261127_usage_records_immutable
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261128_entitlements_engine"
down_revision = "20261127_usage_records_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlements",
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "entitlements",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        CREATE FUNCTION invalidate_entitlements_for_subscription() RETURNS trigger AS $$
        BEGIN
            UPDATE entitlements
            SET expires_at = statement_timestamp()
            WHERE account_id IN (NEW.account_id, OLD.account_id);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_subscriptions_invalidate_entitlements
        AFTER INSERT OR UPDATE OR DELETE ON subscriptions
        FOR EACH ROW EXECUTE FUNCTION invalidate_entitlements_for_subscription()
        """
    )

    op.execute(
        """
        CREATE FUNCTION invalidate_entitlements_for_balance() RETURNS trigger AS $$
        BEGIN
            UPDATE entitlements
            SET expires_at = statement_timestamp()
            WHERE account_id IN (NEW.account_id, OLD.account_id);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dancoin_balances_invalidate_entitlements
        AFTER INSERT OR UPDATE OR DELETE ON dancoin_balances
        FOR EACH ROW EXECUTE FUNCTION invalidate_entitlements_for_balance()
        """
    )

    op.execute(
        """
        CREATE FUNCTION invalidate_entitlements_for_tariff() RETURNS trigger AS $$
        BEGIN
            UPDATE entitlements
            SET expires_at = statement_timestamp()
            WHERE account_id IN (
                SELECT account_id
                FROM subscriptions
                WHERE tariff_plan_id IN (NEW.id, OLD.id)
            );
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tariff_plans_invalidate_entitlements
        AFTER INSERT OR UPDATE OR DELETE ON tariff_plans
        FOR EACH ROW EXECUTE FUNCTION invalidate_entitlements_for_tariff()
        """
    )

    op.execute(
        """
        CREATE FUNCTION invalidate_entitlements_for_agent_slot() RETURNS trigger AS $$
        BEGIN
            UPDATE entitlements
            SET expires_at = statement_timestamp()
            WHERE account_id IN (
                SELECT account_id
                FROM projects
                WHERE id IN (NEW.project_id, OLD.project_id)
            );
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_agent_slots_invalidate_entitlements
        AFTER INSERT OR UPDATE OR DELETE ON agent_slots
        FOR EACH ROW EXECUTE FUNCTION invalidate_entitlements_for_agent_slot()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_agent_slots_invalidate_entitlements ON agent_slots")
    op.execute("DROP FUNCTION IF EXISTS invalidate_entitlements_for_agent_slot()")
    op.execute("DROP TRIGGER IF EXISTS trg_tariff_plans_invalidate_entitlements ON tariff_plans")
    op.execute("DROP FUNCTION IF EXISTS invalidate_entitlements_for_tariff()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_dancoin_balances_invalidate_entitlements ON dancoin_balances"
    )
    op.execute("DROP FUNCTION IF EXISTS invalidate_entitlements_for_balance()")
    op.execute("DROP TRIGGER IF EXISTS trg_subscriptions_invalidate_entitlements ON subscriptions")
    op.execute("DROP FUNCTION IF EXISTS invalidate_entitlements_for_subscription()")
    op.drop_column("entitlements", "expires_at")
    op.drop_column("entitlements", "snapshot")
