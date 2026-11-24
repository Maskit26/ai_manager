"""Add canonical users and external user identities."""

import sqlalchemy as sa
from alembic import op

revision = "20261130_users_and_identities"
down_revision = "20261129_idempotency_not_null"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        sa.text(
            "INSERT INTO users (id, status) "
            "SELECT DISTINCT user_id, 'active' "
            "FROM account_memberships "
            "WHERE user_id IS NOT NULL "
            "ON CONFLICT (id) DO NOTHING"
        )
    )

    op.execute(
        sa.text(
            "INSERT INTO users (id, status) "
            "SELECT DISTINCT actor_id, 'disabled' "
            "FROM audit_log "
            "WHERE actor_id IS NOT NULL "
            "ON CONFLICT (id) DO NOTHING"
        )
    )

    op.create_table(
        "user_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("identity_provider", sa.String(length=100), nullable=False),
        sa.Column("external_user_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_identities_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identity_provider",
            "external_user_id",
            name="uq_user_identities_provider_external_id",
        ),
    )
    op.create_index("ix_user_identities_user_id", "user_identities", ["user_id"])

    op.create_foreign_key(
        "fk_account_memberships_user_id_users",
        "account_memberships",
        "users",
        ["user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_account_memberships_user_id", "account_memberships", ["user_id"])

    op.alter_column(
        "audit_log",
        "actor_id",
        existing_type=sa.String(length=100),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.add_column("audit_log", sa.Column("system_actor", sa.String(length=100), nullable=True))
    op.create_foreign_key(
        "fk_audit_log_actor_id_users",
        "audit_log",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_audit_log_actor_source_exclusive",
        "audit_log",
        "actor_id IS NULL OR system_actor IS NULL",
    )
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_constraint("ck_audit_log_actor_source_exclusive", "audit_log", type_="check")
    op.drop_constraint("fk_audit_log_actor_id_users", "audit_log", type_="foreignkey")
    op.drop_column("audit_log", "system_actor")
    op.alter_column(
        "audit_log",
        "actor_id",
        existing_type=sa.Text(),
        type_=sa.String(length=100),
        existing_nullable=True,
    )

    op.drop_index("ix_account_memberships_user_id", table_name="account_memberships")
    op.drop_constraint(
        "fk_account_memberships_user_id_users",
        "account_memberships",
        type_="foreignkey",
    )
    op.drop_index("ix_user_identities_user_id", table_name="user_identities")
    op.drop_table("user_identities")
    op.drop_table("users")
