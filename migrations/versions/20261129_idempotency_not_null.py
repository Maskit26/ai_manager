"""Require idempotency keys for invoices and payments."""

import sqlalchemy as sa
from alembic import op

revision = "20261129_idempotency_not_null"
down_revision = "20261128_entitlements_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "invoices",
        "idempotency_key",
        existing_type=sa.String(length=200),
        existing_nullable=True,
        nullable=False,
    )
    op.alter_column(
        "payments",
        "idempotency_key",
        existing_type=sa.String(length=200),
        existing_nullable=True,
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "payments",
        "idempotency_key",
        existing_type=sa.String(length=200),
        existing_nullable=False,
        nullable=True,
    )
    op.alter_column(
        "invoices",
        "idempotency_key",
        existing_type=sa.String(length=200),
        existing_nullable=False,
        nullable=True,
    )
