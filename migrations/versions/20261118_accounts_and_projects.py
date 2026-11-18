"""Add accounts, account memberships, and projects."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261118_accounts_projects"
down_revision = "7612d753d48f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.UniqueConstraint("external_id", name="uq_accounts_external_id"),
    )
    op.create_table(
        "account_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'operator')",
            name="ck_account_memberships_role",
        ),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("locale", sa.String(32), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("settings", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("id", "account_id", name="uq_projects_id_account_id"),
    )
    op.create_index("ix_projects_account_id", "projects", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_account_id", table_name="projects")
    op.drop_table("projects")
    op.drop_table("account_memberships")
    op.drop_table("accounts")
