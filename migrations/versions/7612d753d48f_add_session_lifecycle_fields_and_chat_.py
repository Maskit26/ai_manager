"""Add session lifecycle fields and chat messages."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7612d753d48f"
down_revision = "0001_durable_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("external_message_id", sa.String(length=200), nullable=True),
        sa.Column("delivery_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "external_message_id",
            name="uq_chat_messages_external",
        ),
    )
    op.create_index(
        "ix_chat_messages_session_created",
        "chat_messages",
        ["session_id", "created_at"],
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])

    op.add_column(
        "dialog_sessions",
        sa.Column("channel_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column("customer_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column("external_chat_id", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column("close_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column("closed_by", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column(
            "reopen_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "dialog_sessions",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE dialog_sessions
        SET channel_id = 'legacy',
            customer_id = id,
            external_chat_id = id
        WHERE channel_id IS NULL
        """
    )
    op.alter_column("dialog_sessions", "channel_id", nullable=False)
    op.alter_column("dialog_sessions", "customer_id", nullable=False)
    op.alter_column("dialog_sessions", "external_chat_id", nullable=False)

    op.create_index(
        "ix_dialog_sessions_active_lookup",
        "dialog_sessions",
        ["project_id", "channel_id", "external_chat_id"],
        unique=True,
    )
    op.create_index(
        "ix_dialog_sessions_closed_resume",
        "dialog_sessions",
        ["project_id", "channel_id", "customer_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_dialog_sessions_closed_resume", table_name="dialog_sessions")
    op.drop_index("ix_dialog_sessions_active_lookup", table_name="dialog_sessions")
    op.drop_column("dialog_sessions", "updated_at")
    op.drop_column("dialog_sessions", "created_at")
    op.drop_column("dialog_sessions", "reopen_count")
    op.drop_column("dialog_sessions", "closed_by")
    op.drop_column("dialog_sessions", "close_reason")
    op.drop_column("dialog_sessions", "closed_at")
    op.drop_column("dialog_sessions", "last_activity_at")
    op.drop_column("dialog_sessions", "variables")
    op.drop_column("dialog_sessions", "external_chat_id")
    op.drop_column("dialog_sessions", "customer_id")
    op.drop_column("dialog_sessions", "channel_id")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_created", table_name="chat_messages")
    op.drop_table("chat_messages")
