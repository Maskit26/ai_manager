"""Make usage records immutable.

Revision ID: 20261127_usage_records_immutable
Revises: 20261126_billing_schema
Create Date: 2026-11-23
"""

from alembic import op

revision = "20261127_usage_records_immutable"
down_revision = "20261126_billing_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_usage_record_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Usage records are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_usage_records_immutable
        BEFORE UPDATE OR DELETE ON usage_records
        FOR EACH ROW EXECUTE FUNCTION prevent_usage_record_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_usage_records_immutable ON usage_records")
    op.execute("DROP FUNCTION IF EXISTS prevent_usage_record_mutation()")
