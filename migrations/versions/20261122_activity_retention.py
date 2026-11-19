"""Add 30-day retention for domain events and audit logs."""

from alembic import op

revision = "20261122_activity_retention"
down_revision = "20261121_application_entities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_domain_events_occurred_at",
        "domain_events",
        ["occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_log_created_at",
        "audit_log",
        ["created_at"],
        unique=False,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_domain_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND OLD.occurred_at < CURRENT_TIMESTAMP - INTERVAL '30 days' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'Domain events are immutable';
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_domain_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Domain events are immutable';
        END;
        $$
        """
    )
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_domain_events_occurred_at", table_name="domain_events")
