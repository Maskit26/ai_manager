"""Add account entitlements."""

from alembic import op
import sqlalchemy as sa

revision = "20261125_account_entitlements"
down_revision = "20261124_catalog_error_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entitlements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column(
            "catalog_import_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "multi_channel_enabled",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("max_projects", sa.Integer(), nullable=True),
        sa.Column(
            "max_catalog_sources_per_project",
            sa.Integer(),
            server_default="1",
            nullable=True,
        ),
        sa.Column("max_products_per_project", sa.Integer(), nullable=True),
        sa.Column("max_kb_documents_per_project", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "max_projects IS NULL OR max_projects >= 0",
            name="ck_entitlements_max_projects_nonnegative",
        ),
        sa.CheckConstraint(
            "max_catalog_sources_per_project IS NULL OR max_catalog_sources_per_project >= 0",
            name="ck_entitlements_max_catalog_sources_nonnegative",
        ),
        sa.CheckConstraint(
            "max_products_per_project IS NULL OR max_products_per_project >= 0",
            name="ck_entitlements_max_products_nonnegative",
        ),
        sa.CheckConstraint(
            "max_kb_documents_per_project IS NULL OR max_kb_documents_per_project >= 0",
            name="ck_entitlements_max_kb_documents_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_entitlements_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_entitlements"),
        sa.UniqueConstraint("account_id", name="uq_entitlements_account_id"),
    )


def downgrade() -> None:
    op.drop_table("entitlements")
