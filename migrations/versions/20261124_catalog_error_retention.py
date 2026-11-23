"""Add an index for catalog import error retention cleanup."""

from alembic import op

revision = "20261124_catalog_error_retention"
down_revision = "20261123_project_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_catalog_import_errors_created_at",
        "catalog_import_errors",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_catalog_import_errors_created_at",
        table_name="catalog_import_errors",
    )
