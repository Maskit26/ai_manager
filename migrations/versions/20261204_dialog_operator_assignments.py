"""Add tenant-scoped operator assignments for dialogs and escalations.

Revision ID: 20261204_dialog_assignments
Revises: 20261203_entitlement_invalidate
Create Date: 2026-12-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261204_dialog_assignments"
down_revision: str | None = "20261203_entitlement_invalidate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _fail_if_rows(bind: sa.Connection, query: str, message: str) -> None:
    if bind.execute(sa.text(query)).first() is not None:
        raise RuntimeError(message)


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(
        "escalations",
        sa.Column("account_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "escalations",
        sa.Column("assignee_membership_id", sa.String(length=36), nullable=True),
    )
    bind.execute(
        sa.text(
            """
            UPDATE escalations AS e
            SET account_id = p.account_id
            FROM projects AS p
            WHERE p.id = e.project_id
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE escalations AS e
            SET assignee_membership_id = m.id
            FROM projects AS p
            JOIN account_memberships AS m ON m.account_id = p.account_id
            WHERE p.id = e.project_id
              AND e.assignee IS NOT NULL
              AND m.user_id = e.assignee
            """
        )
    )

    _fail_if_rows(
        bind,
        "SELECT 1 FROM escalations WHERE account_id IS NULL LIMIT 1",
        "Cannot backfill account_id for existing escalations.",
    )
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM escalations
        WHERE assignee IS NOT NULL AND assignee_membership_id IS NULL
        LIMIT 1
        """,
        "A legacy escalation assignee cannot be mapped to an account membership.",
    )
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM escalations
        WHERE status = 'open'
        GROUP BY project_id, session_id
        HAVING count(*) > 1
        LIMIT 1
        """,
        "Cannot migrate duplicate open escalations for a dialog.",
    )
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM escalations
        WHERE status = 'open' AND assignee_membership_id IS NOT NULL
        GROUP BY assignee_membership_id
        HAVING count(DISTINCT session_id) > 1
        LIMIT 1
        """,
        "Cannot migrate an operator assigned to multiple open dialogs.",
    )
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM escalations AS e
        LEFT JOIN account_memberships AS m
          ON m.id = e.assignee_membership_id
         AND m.account_id = e.account_id
         AND m.role = 'operator'
         AND m.status = 'active'
        LEFT JOIN project_operators AS po
          ON po.account_id = e.account_id
         AND po.project_id = e.project_id
         AND po.membership_id = m.id
         AND po.status = 'active'
        WHERE e.status = 'open'
          AND e.assignee_membership_id IS NOT NULL
          AND (m.id IS NULL OR po.id IS NULL)
        LIMIT 1
        """,
        "Cannot migrate an open escalation without an active project operator.",
    )

    op.create_table(
        "dialog_operator_assignments",
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_dialog_operator_assignments_project_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_dialog_operator_assignments_project_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["membership_id", "account_id"],
            ["account_memberships.id", "account_memberships.account_id"],
            name="fk_dialog_operator_assignments_membership_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "membership_id",
            name="uq_dialog_operator_assignments_membership_id",
        ),
    )
    op.create_index(
        "ix_dialog_operator_assignments_account_project",
        "dialog_operator_assignments",
        ["account_id", "project_id"],
        unique=False,
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO dialog_operator_assignments (
                session_id, account_id, project_id, membership_id, created_at
            )
            SELECT e.session_id, e.account_id, e.project_id,
                   e.assignee_membership_id, e.created_at
            FROM escalations AS e
            WHERE e.status = 'open'
              AND e.assignee_membership_id IS NOT NULL
            """
        )
    )

    op.alter_column(
        "escalations",
        "account_id",
        existing_type=sa.String(length=36),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_escalations_project_account",
        "escalations",
        "projects",
        ["project_id", "account_id"],
        ["id", "account_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_escalations_assignee_membership_account",
        "escalations",
        "account_memberships",
        ["assignee_membership_id", "account_id"],
        ["id", "account_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_escalations_open_project_session",
        "escalations",
        ["project_id", "session_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_column("escalations", "assignee")


def downgrade() -> None:
    bind = op.get_bind()
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM dialog_operator_assignments AS a
        LEFT JOIN escalations AS e
          ON e.project_id = a.project_id
         AND e.session_id = a.session_id
         AND e.status = 'open'
         AND e.assignee_membership_id = a.membership_id
        WHERE e.id IS NULL
        LIMIT 1
        """,
        "Cannot downgrade dialog assignments that have no matching open escalation.",
    )

    op.add_column(
        "escalations",
        sa.Column("assignee", sa.String(length=100), nullable=True),
    )
    bind.execute(
        sa.text(
            """
            UPDATE escalations AS e
            SET assignee = m.user_id
            FROM account_memberships AS m
            WHERE m.id = e.assignee_membership_id
              AND m.account_id = e.account_id
            """
        )
    )
    _fail_if_rows(
        bind,
        """
        SELECT 1
        FROM escalations
        WHERE assignee_membership_id IS NOT NULL AND assignee IS NULL
        LIMIT 1
        """,
        "Cannot restore a legacy escalation assignee from its membership.",
    )

    op.drop_index("uq_escalations_open_project_session", table_name="escalations")
    op.drop_constraint(
        "fk_escalations_assignee_membership_account",
        "escalations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_escalations_project_account",
        "escalations",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_dialog_operator_assignments_account_project",
        table_name="dialog_operator_assignments",
    )
    op.drop_table("dialog_operator_assignments")
    op.drop_column("escalations", "assignee_membership_id")
    op.drop_column("escalations", "account_id")
