"""Support operator/system messages and link outbox delivery status."""

import sqlalchemy as sa
from alembic import op

revision = "20261207_dialog_message_roles"
down_revision = "20261206_project_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_chat_messages_role", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_role",
        "chat_messages",
        "role IN ('user', 'assistant', 'operator', 'system')",
    )
    op.drop_constraint("ck_chat_messages_delivery_status", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_delivery_status",
        "chat_messages",
        "delivery_status IN "
        "('received', 'pending', 'sending', 'sent', 'retrying', 'delivery_unknown', "
        "'failed', 'not_required')",
    )
    op.add_column("outbox", sa.Column("chat_message_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_outbox_project_session_chat_message",
        "outbox",
        "chat_messages",
        ["project_id", "session_id", "chat_message_id"],
        ["project_id", "session_id", "id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_outbox_project_session_chat_message",
        "outbox",
        type_="foreignkey",
    )
    op.drop_column("outbox", "chat_message_id")
    op.drop_constraint("ck_chat_messages_role", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_role",
        "chat_messages",
        "role IN ('user', 'assistant')",
    )
    op.drop_constraint("ck_chat_messages_delivery_status", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_delivery_status",
        "chat_messages",
        "delivery_status IN "
        "('received', 'pending', 'sending', 'sent', 'retrying', 'delivery_unknown', 'failed')",
    )
