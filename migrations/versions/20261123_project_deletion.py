"""Add durable project deletion requests."""

import sqlalchemy as sa
from alembic import op

revision = "20261123_project_deletion"
down_revision = "20261122_activity_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_deletion_requests",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled')",
            name="ck_project_deletion_requests_status",
        ),
        sa.PrimaryKeyConstraint("project_id"),
    )
    op.create_index(
        "ix_project_deletion_requests_status_requested",
        "project_deletion_requests",
        ["status", "requested_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_deletion_requests_status_requested",
        table_name="project_deletion_requests",
    )
    op.drop_table("project_deletion_requests")
