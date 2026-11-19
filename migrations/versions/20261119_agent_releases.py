"""Add project agents and immutable release snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261119_agent_releases"
down_revision = "20261118_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM dialog_sessions) THEN "
            "RAISE EXCEPTION 'Cannot add release foreign keys while dialog_sessions contains "
            "legacy release_id values; map those sessions to agent releases first.'; "
            "END IF; END $$"
        )
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("active_release_id", sa.String(length=36), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'disabled')",
            name="ck_agents_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agents_project_id_projects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_agents_project_id"),
        sa.UniqueConstraint("project_id", "id", name="uq_agents_project_id_id"),
    )
    op.create_table(
        "agent_drafts",
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_agent_drafts_config_object",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["agents.project_id", "agents.id"],
            name="fk_agent_drafts_agent",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "agent_id", name="pk_agent_drafts"),
    )
    op.create_table(
        "agent_releases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_agent_releases_snapshot_object",
        ),
        sa.CheckConstraint("version > 0", name="ck_agent_releases_version_positive"),
        sa.ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["agents.project_id", "agents.id"],
            name="fk_agent_releases_agent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_releases_agent_version"),
        sa.UniqueConstraint("project_id", "id", name="uq_agent_releases_project_id_id"),
        sa.UniqueConstraint(
            "project_id",
            "agent_id",
            "id",
            name="uq_agent_releases_project_agent_id",
        ),
    )
    op.create_foreign_key(
        "fk_agents_active_release",
        "agents",
        "agent_releases",
        ["project_id", "id", "active_release_id"],
        ["project_id", "agent_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_dialog_sessions_project_release",
        "dialog_sessions",
        "agent_releases",
        ["project_id", "release_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_agent_release_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'Published agent releases are immutable'; "
            "END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_agent_releases_immutable "
            "BEFORE UPDATE OR DELETE ON agent_releases "
            "FOR EACH ROW EXECUTE FUNCTION prevent_agent_release_mutation()"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_dialog_sessions_project_release",
        "dialog_sessions",
        type_="foreignkey",
    )
    op.execute(sa.text("DROP TRIGGER trg_agent_releases_immutable ON agent_releases"))
    op.execute(sa.text("DROP FUNCTION prevent_agent_release_mutation()"))
    op.drop_constraint("fk_agents_active_release", "agents", type_="foreignkey")
    op.drop_table("agent_releases")
    op.drop_table("agent_drafts")
    op.drop_table("agents")
