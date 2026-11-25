"""Add dialog versioning, channel-scoped message deduplication, and domain events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261119_dialog_transactions"
down_revision = "20261119_agent_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dialog_sessions",
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_unique_constraint(
        "uq_dialog_sessions_project_id_channel_id",
        "dialog_sessions",
        ["project_id", "id", "channel_id"],
    )
    op.create_check_constraint(
        "ck_dialog_sessions_state",
        "dialog_sessions",
        "state IN ('active', 'closed')",
    )
    op.create_check_constraint(
        "ck_dialog_sessions_close_reason",
        "dialog_sessions",
        "close_reason IS NULL OR close_reason IN ('max_chat_closed', 'idle_ttl', 'manual')",
    )
    op.create_check_constraint(
        "ck_dialog_sessions_reopen_count",
        "dialog_sessions",
        "reopen_count >= 0",
    )
    op.create_check_constraint(
        "ck_dialog_sessions_lease_fence",
        "dialog_sessions",
        "lease_fence >= 0",
    )
    op.create_check_constraint(
        "ck_dialog_sessions_version",
        "dialog_sessions",
        "version >= 0",
    )

    op.add_column("chat_messages", sa.Column("channel_id", sa.String(length=100), nullable=True))
    op.execute(
        sa.text(
            "UPDATE chat_messages AS message "
            "SET channel_id = session.channel_id "
            "FROM dialog_sessions AS session "
            "WHERE session.project_id = message.project_id "
            "AND session.id = message.session_id"
        )
    )
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM chat_messages WHERE channel_id IS NULL) THEN "
            "RAISE EXCEPTION 'Cannot assign channel_id to every chat message from its session'; "
            "END IF; "
            "IF EXISTS ("
            "SELECT 1 FROM chat_messages "
            "WHERE external_message_id IS NOT NULL "
            "GROUP BY project_id, channel_id, external_message_id HAVING count(*) > 1"
            ") THEN "
            "RAISE EXCEPTION 'Duplicate external message IDs exist within a project channel'; "
            "END IF; END $$"
        )
    )
    op.alter_column("chat_messages", "channel_id", nullable=False)
    op.drop_constraint("uq_chat_messages_external", "chat_messages", type_="unique")
    op.drop_constraint("fk_chat_messages_project_session", "chat_messages", type_="foreignkey")
    op.create_check_constraint(
        "ck_chat_messages_role",
        "chat_messages",
        "role IN ('user', 'assistant')",
    )
    op.create_check_constraint(
        "ck_chat_messages_delivery_status",
        "chat_messages",
        "delivery_status IN "
        "('received', 'pending', 'sending', 'sent', 'retrying', 'delivery_unknown', 'failed')",
    )
    op.create_unique_constraint(
        "uq_chat_messages_external",
        "chat_messages",
        ["project_id", "channel_id", "external_message_id"],
    )
    op.create_foreign_key(
        "fk_chat_messages_project_session_channel",
        "chat_messages",
        "dialog_sessions",
        ["project_id", "session_id", "channel_id"],
        ["project_id", "id", "channel_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "domain_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=100), nullable=False),
        sa.Column("tenant_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("correlation_id", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("event_type <> ''", name="ck_domain_events_event_type"),
        sa.CheckConstraint("schema_version > 0", name="ck_domain_events_schema_version"),
        sa.CheckConstraint(
            "jsonb_typeof(tenant_ids) = 'object'",
            name="ck_domain_events_tenant_ids_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_domain_events_payload_object",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_domain_events_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_domain_events_project_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_domain_events_project_session_occurred",
        "domain_events",
        ["project_id", "session_id", "occurred_at"],
    )
    op.create_index(
        "ix_domain_events_aggregate",
        "domain_events",
        ["project_id", "aggregate_type", "aggregate_id", "occurred_at"],
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_domain_event_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'Domain events are immutable'; "
            "END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_domain_events_immutable "
            "BEFORE UPDATE OR DELETE ON domain_events "
            "FOR EACH ROW EXECUTE FUNCTION prevent_domain_event_mutation()"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER trg_domain_events_immutable ON domain_events"))
    op.execute(sa.text("DROP FUNCTION prevent_domain_event_mutation()"))
    op.drop_table("domain_events")

    op.drop_constraint(
        "fk_chat_messages_project_session_channel",
        "chat_messages",
        type_="foreignkey",
    )
    op.drop_constraint("uq_chat_messages_external", "chat_messages", type_="unique")
    op.drop_constraint("ck_chat_messages_role", "chat_messages", type_="check")
    op.drop_constraint("ck_chat_messages_delivery_status", "chat_messages", type_="check")
    op.create_unique_constraint(
        "uq_chat_messages_external",
        "chat_messages",
        ["project_id", "session_id", "external_message_id"],
    )
    op.create_foreign_key(
        "fk_chat_messages_project_session",
        "chat_messages",
        "dialog_sessions",
        ["project_id", "session_id"],
        ["project_id", "id"],
    )
    op.drop_column("chat_messages", "channel_id")

    op.drop_constraint("ck_dialog_sessions_version", "dialog_sessions", type_="check")
    op.drop_constraint("ck_dialog_sessions_lease_fence", "dialog_sessions", type_="check")
    op.drop_constraint("ck_dialog_sessions_reopen_count", "dialog_sessions", type_="check")
    op.drop_constraint("ck_dialog_sessions_close_reason", "dialog_sessions", type_="check")
    op.drop_constraint("ck_dialog_sessions_state", "dialog_sessions", type_="check")
    op.drop_constraint(
        "uq_dialog_sessions_project_id_channel_id",
        "dialog_sessions",
        type_="unique",
    )
    op.drop_column("dialog_sessions", "version")
