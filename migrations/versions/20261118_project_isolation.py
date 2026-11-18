"""Scope existing durable records to projects."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261118_project_isolation"
down_revision = "20261118_accounts_projects"
branch_labels = None
depends_on = None


def _assert_no_rows(statement: str, message: str) -> None:
    if context.is_offline_mode():
        escaped_message = message.replace("'", "''")
        op.execute(
            sa.text(
                "DO $$ BEGIN "
                f"IF EXISTS ({statement}) THEN RAISE EXCEPTION '{escaped_message}'; "
                "END IF; END $$"
            )
        )
    elif op.get_bind().scalar(sa.text(f"SELECT EXISTS ({statement})")):
        raise RuntimeError(message)


def upgrade() -> None:
    _assert_no_rows(
        "SELECT 1 FROM dialog_sessions s "
        "LEFT JOIN projects p ON p.id = s.project_id WHERE p.id IS NULL",
        "Cannot add project isolation: dialog_sessions contains project_id values "
        "without matching projects; map existing sessions to accounts/projects first.",
    )

    op.add_column(
        "background_jobs",
        sa.Column("project_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "inbox",
        sa.Column("project_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "outbox",
        sa.Column("project_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("project_id", sa.String(length=100), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE outbox o SET project_id = s.project_id "
            "FROM dialog_sessions s WHERE o.session_id = s.id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE chat_messages m SET project_id = s.project_id "
            "FROM dialog_sessions s WHERE m.session_id = s.id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE background_jobs j SET project_id = o.project_id "
            "FROM outbox o WHERE j.payload->>'outbox_id' = o.id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE background_jobs SET payload = "
            "payload || jsonb_build_object('project_id', project_id) "
            "WHERE project_id IS NOT NULL"
        )
    )

    for table in ("background_jobs", "inbox", "outbox", "chat_messages"):
        _assert_no_rows(
            f"SELECT 1 FROM {table} WHERE project_id IS NULL",
            "Cannot add project isolation: existing durable records cannot be assigned "
            "to a project without an explicit mapping. Resolve legacy rows before retrying.",
        )

    for table in ("background_jobs", "inbox", "outbox", "chat_messages"):
        op.alter_column(table, "project_id", existing_type=sa.String(length=100), nullable=False)

    op.create_index("ix_background_jobs_project_id", "background_jobs", ["project_id"])
    op.create_index("ix_inbox_project_id", "inbox", ["project_id"])
    op.create_index("ix_outbox_project_id", "outbox", ["project_id"])
    op.create_index(
        "ix_chat_messages_project_created",
        "chat_messages",
        ["project_id", "created_at"],
    )

    op.create_unique_constraint(
        "uq_dialog_sessions_project_id_id", "dialog_sessions", ["project_id", "id"]
    )
    op.create_foreign_key(
        "fk_dialog_sessions_project_id_projects",
        "dialog_sessions",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_background_jobs_project_id_projects",
        "background_jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_inbox_project_id_projects",
        "inbox",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_chat_messages_project_session",
        "chat_messages",
        "dialog_sessions",
        ["project_id", "session_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_outbox_project_session",
        "outbox",
        "dialog_sessions",
        ["project_id", "session_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_outbox_project_session", "outbox", type_="foreignkey")
    op.drop_constraint("fk_chat_messages_project_session", "chat_messages", type_="foreignkey")
    op.drop_constraint("fk_inbox_project_id_projects", "inbox", type_="foreignkey")
    op.drop_constraint(
        "fk_background_jobs_project_id_projects", "background_jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_dialog_sessions_project_id_projects", "dialog_sessions", type_="foreignkey"
    )
    op.drop_constraint("uq_dialog_sessions_project_id_id", "dialog_sessions", type_="unique")
    op.drop_index("ix_chat_messages_project_created", table_name="chat_messages")
    op.drop_index("ix_outbox_project_id", table_name="outbox")
    op.drop_index("ix_inbox_project_id", table_name="inbox")
    op.drop_index("ix_background_jobs_project_id", table_name="background_jobs")
    op.drop_column("chat_messages", "project_id")
    op.drop_column("outbox", "project_id")
    op.drop_column("inbox", "project_id")
    op.drop_column("background_jobs", "project_id")
