"""Add application persistence entities and durable processing metadata."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261121_application_entities"
down_revision = "20261120_catalog_customers_kb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "background_jobs",
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        "ix_background_jobs_status_available_priority",
        "background_jobs",
        ["status", "available_at", "priority"],
    )

    op.add_column("inbox", sa.Column("processed_at", sa.DateTime(timezone=True)))
    op.add_column("inbox", sa.Column("last_error", sa.Text()))

    op.add_column(
        "outbox",
        sa.Column(
            "kind",
            sa.String(length=80),
            server_default="outbound_message",
            nullable=False,
        ),
    )
    op.add_column(
        "outbox", sa.Column("destination", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "outbox", sa.Column("aggregate_id", sa.String(length=100), nullable=True)
    )
    op.add_column("outbox", sa.Column("delivery_reason", sa.String(length=100)))
    op.execute(
        sa.text(
            "UPDATE outbox SET destination = channel_id, aggregate_id = session_id"
        )
    )
    op.alter_column(
        "outbox", "destination", existing_type=sa.String(length=200), nullable=False
    )
    op.alter_column(
        "outbox", "aggregate_id", existing_type=sa.String(length=100), nullable=False
    )

    op.create_unique_constraint(
        "uq_chat_messages_project_session_id",
        "chat_messages",
        ["project_id", "session_id", "id"],
    )
    op.create_index(
        "ix_domain_events_project_time_type",
        "domain_events",
        ["project_id", "occurred_at", "event_type"],
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_tool_executions_project_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id", "message_id"],
            ["chat_messages.project_id", "chat_messages.session_id", "chat_messages.id"],
            name="fk_tool_executions_project_session_message",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_executions_project_session_message",
        "tool_executions",
        ["project_id", "session_id", "message_id"],
    )

    op.create_table(
        "leads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "customer_confirmed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("consent_given", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "request_data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_leads_project_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "customer_id"],
            ["customers.project_id", "customers.id"],
            name="fk_leads_project_customer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "id", name="uq_leads_project_id"),
        sa.UniqueConstraint(
            "project_id", "idempotency_key", name="uq_leads_project_idempotency"
        ),
    )
    op.create_index(
        "ix_leads_project_status_created",
        "leads",
        ["project_id", "status", "created_at"],
    )

    op.create_table(
        "lead_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("lead_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column(
            "product_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "lead_id"],
            ["leads.project_id", "leads.id"],
            name="fk_lead_items_project_lead",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "product_id"],
            ["products.project_id", "products.id"],
            name="fk_lead_items_project_product",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_lead_items_project_lead", "lead_items", ["project_id", "lead_id"]
    )

    op.create_table(
        "escalations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("assignee", sa.String(length=100), nullable=True),
        sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_escalations_project_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_escalations_project_status", "escalations", ["project_id", "status"]
    )
    op.create_index(
        "ix_escalations_project_sla", "escalations", ["project_id", "sla_deadline"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=True),
        sa.Column("actor_id", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=False),
        sa.Column(
            "diff",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name="fk_audit_log_account", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_audit_log_project_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_project_time_type",
        "audit_log",
        ["project_id", "created_at", "action"],
    )
    op.create_index(
        "ix_audit_log_account_time_type",
        "audit_log",
        ["account_id", "created_at", "action"],
    )

    op.create_table(
        "llm_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_llm_calls_project_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_account_time", "llm_calls", ["account_id", "created_at"])
    op.create_index("ix_llm_calls_project_time", "llm_calls", ["project_id", "created_at"])

    op.create_table(
        "usage_counters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Numeric(precision=20, scale=6), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_usage_counters_project_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "period_start",
            "metric",
            name="uq_usage_counters_project_period_metric",
        ),
    )
    op.create_index(
        "ix_usage_counters_account_time", "usage_counters", ["account_id", "period_start"]
    )
    op.create_index(
        "ix_usage_counters_project_time", "usage_counters", ["project_id", "period_start"]
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
    op.drop_table("llm_calls")
    op.drop_table("audit_log")
    op.drop_table("escalations")
    op.drop_table("lead_items")
    op.drop_table("leads")
    op.drop_table("tool_executions")
    op.drop_index("ix_domain_events_project_time_type", table_name="domain_events")
    op.drop_constraint(
        "uq_chat_messages_project_session_id", "chat_messages", type_="unique"
    )

    op.drop_column("outbox", "delivery_reason")
    op.drop_column("outbox", "aggregate_id")
    op.drop_column("outbox", "destination")
    op.drop_column("outbox", "kind")
    op.drop_column("inbox", "last_error")
    op.drop_column("inbox", "processed_at")
    op.drop_index(
        "ix_background_jobs_status_available_priority", table_name="background_jobs"
    )
    op.drop_column("background_jobs", "priority")
