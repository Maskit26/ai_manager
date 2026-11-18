"""Add project-scoped channels and link existing channel references."""

import sqlalchemy as sa
from alembic import op

revision = "20261118_channels"
down_revision = "20261118_project_isolation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_channels_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("project_id", "channel_id", name="pk_channels"),
    )
    op.execute(
        sa.text(
            "INSERT INTO channels (project_id, channel_id) "
            "SELECT project_id, channel_id FROM dialog_sessions "
            "UNION "
            "SELECT project_id, channel_id FROM inbox "
            "UNION "
            "SELECT project_id, channel_id FROM outbox"
        )
    )

    op.drop_constraint("uq_inbox_provider_event", "inbox", type_="unique")
    op.create_unique_constraint(
        "uq_inbox_project_channel_event",
        "inbox",
        ["project_id", "channel_id", "provider_event_id"],
    )
    op.create_foreign_key(
        "fk_dialog_sessions_project_channel",
        "dialog_sessions",
        "channels",
        ["project_id", "channel_id"],
        ["project_id", "channel_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_inbox_project_channel",
        "inbox",
        "channels",
        ["project_id", "channel_id"],
        ["project_id", "channel_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_outbox_project_channel",
        "outbox",
        "channels",
        ["project_id", "channel_id"],
        ["project_id", "channel_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_outbox_project_channel", "outbox", type_="foreignkey")
    op.drop_constraint("fk_inbox_project_channel", "inbox", type_="foreignkey")
    op.drop_constraint(
        "fk_dialog_sessions_project_channel",
        "dialog_sessions",
        type_="foreignkey",
    )
    op.drop_constraint("uq_inbox_project_channel_event", "inbox", type_="unique")
    op.create_unique_constraint(
        "uq_inbox_provider_event",
        "inbox",
        ["channel_id", "provider_event_id"],
    )
    op.drop_table("channels")
