"""Create the initial durable-processing schema."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_durable_processing"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("queue", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency"),
    )
    op.create_index(
        "ix_background_jobs_claim",
        "background_jobs",
        ["queue", "status", "available_at"],
    )

    op.create_table(
        "inbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("normalized_event", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("correlation_id", sa.String(length=100), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel_id",
            "provider_event_id",
            name="uq_inbox_provider_event",
        ),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_message_id", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
    )
    op.create_index("ix_outbox_delivery", "outbox", ["status", "available_at"])

    op.create_table(
        "dialog_sessions",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("release_id", sa.String(length=100), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_fence", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dialog_sessions_project_id", "dialog_sessions", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_dialog_sessions_project_id", table_name="dialog_sessions")
    op.drop_table("dialog_sessions")
    op.drop_index("ix_outbox_delivery", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("inbox")
    op.drop_index("ix_background_jobs_claim", table_name="background_jobs")
    op.drop_table("background_jobs")
