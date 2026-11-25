"""Enforce a single owner per account.

Revision ID: 20261201_one_owner_per_account
Revises: 20261130_account_identities
Create Date: 2026-12-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261201_one_owner_per_account"
down_revision: str | None = "20261130_account_identities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_account_memberships_one_owner",
        "account_memberships",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_account_memberships_one_owner",
        table_name="account_memberships",
    )
