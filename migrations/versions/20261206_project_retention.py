"""Track activity across all project data before physical purge."""

import sqlalchemy as sa
from alembic import op

revision: str = "20261206_project_retention"
down_revision: str | None = "20261205_api_idempotency"
branch_labels: str | None = None
depends_on: str | None = None


_ACTIVITY_TIMESTAMP_COLUMNS = (
    ("projects", "created_at"),
    ("projects", "updated_at"),
    ("agents", "created_at"),
    ("agents", "updated_at"),
    ("channels", "created_at"),
    ("channels", "updated_at"),
    ("lead_items", "created_at"),
    ("lead_items", "updated_at"),
    ("escalations", "updated_at"),
    ("inbox", "updated_at"),
    ("outbox", "updated_at"),
    ("catalog_imports", "updated_at"),
    ("customer_identities", "updated_at"),
)


def upgrade() -> None:
    for table_name, column_name in _ACTIVITY_TIMESTAMP_COLUMNS:
        op.add_column(
            table_name,
            sa.Column(
                column_name,
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table_name, column_name in reversed(_ACTIVITY_TIMESTAMP_COLUMNS):
        op.drop_column(table_name, column_name)
