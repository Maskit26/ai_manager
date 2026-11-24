"""Move external account IDs into provider-scoped identities."""

import sqlalchemy as sa
from alembic import op

revision = "20261130_account_identities"
down_revision = "20261130_users_and_identities"
branch_labels = None
depends_on = None


_UUID_PATTERN = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM accounts WHERE external_id !~* "
            f"'{_UUID_PATTERN}') THEN "
            "RAISE EXCEPTION 'Cannot migrate accounts.external_id: "
            "values must be Bitrix external UUIDs'; "
            "END IF; END $$"
        )
    )

    op.create_table(
        "account_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("identity_provider", sa.String(length=100), nullable=False),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_account_identities_account_id_accounts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identity_provider",
            "external_account_id",
            name="uq_account_identities_provider_external_id",
        ),
    )
    op.create_index("ix_account_identities_account_id", "account_identities", ["account_id"])
    op.execute(
        sa.text(
            "INSERT INTO account_identities "
            "(id, account_id, identity_provider, external_account_id) "
            "SELECT gen_random_uuid()::text, id, 'bitrix', external_id FROM accounts"
        )
    )
    op.drop_constraint("uq_accounts_external_id", "accounts", type_="unique")
    op.drop_column("accounts", "external_id")


def downgrade() -> None:
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS ("
            "SELECT a.id FROM accounts a "
            "LEFT JOIN account_identities ai ON ai.account_id = a.id "
            "GROUP BY a.id "
            "HAVING count(ai.id) <> 1 "
            "OR count(*) FILTER (WHERE ai.identity_provider <> 'bitrix') > 0"
            ") THEN "
            "RAISE EXCEPTION 'Cannot downgrade account identities without losing mappings'; "
            "END IF; END $$"
        )
    )
    op.add_column(
        "accounts",
        sa.Column("external_id", sa.Text(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE accounts SET external_id = ("
            "SELECT external_account_id FROM account_identities "
            "WHERE account_identities.account_id = accounts.id "
            "AND identity_provider = 'bitrix'"
            ")"
        )
    )
    op.alter_column("accounts", "external_id", existing_type=sa.Text(), nullable=False)
    op.create_unique_constraint("uq_accounts_external_id", "accounts", ["external_id"])
    op.drop_index("ix_account_identities_account_id", table_name="account_identities")
    op.drop_table("account_identities")
