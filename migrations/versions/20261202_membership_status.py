"""Move user status to account memberships and add project operator assignments.

Revision ID: 20261202_membership_status
Revises: 20261201_one_owner_per_account
Create Date: 2026-12-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261202_membership_status"
down_revision: str | None = "20261201_one_owner_per_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_column("users", "status")

    op.add_column(
        "account_memberships",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_account_memberships_status",
        "account_memberships",
        "status IN ('active', 'disabled')",
    )
    op.create_check_constraint(
        "ck_account_memberships_owner_active",
        "account_memberships",
        "role <> 'owner' OR status = 'active'",
    )
    op.create_unique_constraint(
        "uq_account_memberships_id_account_id",
        "account_memberships",
        ["id", "account_id"],
    )

    op.create_table(
        "project_operators",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.Column("membership_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_project_operators_project_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "account_id"],
            ["account_memberships.id", "account_memberships.account_id"],
            name="fk_project_operators_membership_account",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id",
            "membership_id",
            name="uq_project_operators_project_membership",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_project_operators_status",
        ),
    )
    op.create_index(
        "ix_project_operators_membership_id",
        "project_operators",
        ["membership_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_project_operators_membership_id", table_name="project_operators")
    op.drop_table("project_operators")

    op.drop_constraint(
        "uq_account_memberships_id_account_id",
        "account_memberships",
        type_="unique",
    )
    op.drop_constraint(
        "ck_account_memberships_owner_active",
        "account_memberships",
        type_="check",
    )
    op.drop_constraint(
        "ck_account_memberships_status",
        "account_memberships",
        type_="check",
    )
    op.drop_column("account_memberships", "status")

    op.add_column(
        "users",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_users_status",
        "users",
        "status IN ('active', 'disabled')",
    )
