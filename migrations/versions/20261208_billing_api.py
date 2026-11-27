"""Support scheduled billing changes and pending refund requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261208_billing_api"
down_revision = "20261207_dialog_message_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("pending_tariff_plan_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("pending_billing_period", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("pending_effective_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("pending_period_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column("pending_period_end", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "pending_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_subscriptions_pending_tariff_plan",
        "subscriptions",
        "tariff_plans",
        ["pending_tariff_plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_subscriptions_pending_period_order",
        "subscriptions",
        "pending_period_start IS NULL OR pending_period_end IS NULL "
        "OR pending_period_end > pending_period_start",
    )

    op.create_table(
        "billing_refund_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_billing_refund_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_billing_refund_requests_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id", "account_id", "invoice_id"],
            ["payments.id", "payments.account_id", "payments.invoice_id"],
            name="fk_billing_refund_requests_payment",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name="fk_billing_refund_requests_user",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_billing_refund_requests"),
        sa.UniqueConstraint(
            "account_id",
            "payment_id",
            name="uq_billing_refund_requests_account_payment",
        ),
    )
    op.create_index(
        "ix_billing_refund_requests_account_created",
        "billing_refund_requests",
        ["account_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_refund_requests_account_created",
        table_name="billing_refund_requests",
    )
    op.drop_table("billing_refund_requests")
    op.drop_constraint(
        "ck_subscriptions_pending_period_order",
        "subscriptions",
        type_="check",
    )
    op.drop_constraint(
        "fk_subscriptions_pending_tariff_plan",
        "subscriptions",
        type_="foreignkey",
    )
    op.drop_column("subscriptions", "pending_snapshot")
    op.drop_column("subscriptions", "pending_period_end")
    op.drop_column("subscriptions", "pending_period_start")
    op.drop_column("subscriptions", "pending_effective_at")
    op.drop_column("subscriptions", "pending_billing_period")
    op.drop_column("subscriptions", "pending_tariff_plan_id")
