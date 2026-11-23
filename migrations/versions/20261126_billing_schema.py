"""Create billing catalogs, accounting tables, and usage records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261126_billing_schema"
down_revision = "20261125_account_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tariff_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("included_agents", sa.Integer(), nullable=True),
        sa.Column("max_agents", sa.Integer(), nullable=True),
        sa.Column("extra_agent_daily_dancoin", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "features",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "limits",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "dancoin_grant_conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "billing_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_tariff_plans_version_positive"),
        sa.CheckConstraint(
            "included_agents IS NULL OR included_agents >= 0",
            name="ck_tariff_plans_included_agents_nonnegative",
        ),
        sa.CheckConstraint(
            "max_agents IS NULL OR max_agents >= 0",
            name="ck_tariff_plans_max_agents_nonnegative",
        ),
        sa.CheckConstraint(
            "max_agents IS NULL OR included_agents IS NULL OR max_agents >= included_agents",
            name="ck_tariff_plans_agent_limits_order",
        ),
        sa.CheckConstraint(
            "extra_agent_daily_dancoin IS NULL OR extra_agent_daily_dancoin >= 0",
            name="ck_tariff_plans_extra_agent_cost_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tariff_plans"),
        sa.UniqueConstraint("code", "version", name="uq_tariff_plans_code_version"),
    )
    op.create_index("ix_tariff_plans_active", "tariff_plans", ["is_active"])

    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("input_price_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_price_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("long_context_threshold_tokens", sa.BigInteger(), nullable=True),
        sa.Column("long_context_input_price_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("long_context_output_price_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_llm_models_version_positive"),
        sa.CheckConstraint(
            "input_price_per_1k IS NULL OR input_price_per_1k >= 0",
            name="ck_llm_models_input_price_nonnegative",
        ),
        sa.CheckConstraint(
            "output_price_per_1k IS NULL OR output_price_per_1k >= 0",
            name="ck_llm_models_output_price_nonnegative",
        ),
        sa.CheckConstraint(
            "long_context_threshold_tokens IS NULL OR long_context_threshold_tokens >= 0",
            name="ck_llm_models_context_threshold_nonnegative",
        ),
        sa.CheckConstraint(
            "long_context_input_price_per_1k IS NULL OR long_context_input_price_per_1k >= 0",
            name="ck_llm_models_long_input_price_nonnegative",
        ),
        sa.CheckConstraint(
            "long_context_output_price_per_1k IS NULL OR long_context_output_price_per_1k >= 0",
            name="ck_llm_models_long_output_price_nonnegative",
        ),
        sa.CheckConstraint(
            "NOT is_active OR (input_price_per_1k IS NOT NULL AND output_price_per_1k IS NOT NULL)",
            name="ck_llm_models_active_has_base_prices",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_models"),
        sa.UniqueConstraint("model_id", "version", name="uq_llm_models_model_version"),
    )
    op.create_index("ix_llm_models_active", "llm_models", ["is_active"])

    op.create_table(
        "dancoin_packages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("dancoin_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("version > 0", name="ck_dancoin_packages_version_positive"),
        sa.CheckConstraint("dancoin_amount > 0", name="ck_dancoin_packages_amount_positive"),
        sa.CheckConstraint("price_amount >= 0", name="ck_dancoin_packages_price_nonnegative"),
        sa.PrimaryKeyConstraint("id", name="pk_dancoin_packages"),
        sa.UniqueConstraint("code", "version", name="uq_dancoin_packages_code_version"),
    )
    op.create_index("ix_dancoin_packages_active", "dancoin_packages", ["is_active"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("tariff_plan_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("trial_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "status IN ('trial', 'active', 'grace_period', 'canceled', 'parking')",
            name="ck_subscriptions_status",
        ),
        sa.CheckConstraint(
            "current_period_end IS NULL OR current_period_end >= current_period_start",
            name="ck_subscriptions_period_order",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_subscriptions_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tariff_plan_id"],
            ["tariff_plans.id"],
            name="fk_subscriptions_tariff_plan",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
    )
    op.create_index(
        "uq_subscriptions_current_account",
        "subscriptions",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('trial', 'active', 'grace_period', 'parking')"),
    )
    op.create_index(
        "uq_subscriptions_trial_account",
        "subscriptions",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("trial_used"),
    )
    op.create_index(
        "ix_subscriptions_status_period_end",
        "subscriptions",
        ["status", "current_period_end"],
    )

    op.create_table(
        "dancoin_balances",
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column(
            "grant_balance",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "purchased_balance",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "reserved_grant",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "reserved_purchased",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "debt_balance",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("grant_balance >= 0", name="ck_dancoin_balances_grant_nonnegative"),
        sa.CheckConstraint(
            "purchased_balance >= 0",
            name="ck_dancoin_balances_purchased_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_grant >= 0 AND reserved_purchased >= 0",
            name="ck_dancoin_balances_reserved_nonnegative",
        ),
        sa.CheckConstraint("debt_balance >= 0", name="ck_dancoin_balances_debt_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_balances_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("account_id", name="pk_dancoin_balances"),
    )

    op.create_table(
        "dancoin_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("price_version", sa.Integer(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('grant', 'purchase', 'reserve', 'settle', 'release', "
            "'spend_agent', 'refund', 'timeout_charge', 'expire', 'adjustment')",
            name="ck_dancoin_transactions_type",
        ),
        sa.CheckConstraint(
            "source IS NULL OR source IN ('grant', 'purchase', 'system')",
            name="ck_dancoin_transactions_source",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_dancoin_transactions_amount_nonzero"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_transactions_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dancoin_transactions"),
        sa.UniqueConstraint("reference", name="uq_dancoin_transactions_reference"),
    )
    op.create_index(
        "ix_dancoin_transactions_account_time",
        "dancoin_transactions",
        ["account_id", "occurred_at"],
    )

    op.create_table(
        "dancoin_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "grant_amount",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "purchased_amount",
            sa.Numeric(12, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_dancoin_reservations_amount_positive"),
        sa.CheckConstraint(
            "grant_amount >= 0 AND purchased_amount >= 0 "
            "AND amount = grant_amount + purchased_amount",
            name="ck_dancoin_reservations_source_amounts",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'timeout_charged')",
            name="ck_dancoin_reservations_status",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_reservations_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dancoin_reservations"),
        sa.UniqueConstraint("operation_id", name="uq_dancoin_reservations_operation"),
    )
    op.create_index(
        "ix_dancoin_reservations_account_status",
        "dancoin_reservations",
        ["account_id", "status"],
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=False),
        sa.Column("purchase_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("total >= 0", name="ck_invoices_total_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_invoices_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.UniqueConstraint("id", "account_id", name="uq_invoices_id_account"),
        sa.UniqueConstraint("number", name="uq_invoices_number"),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name="uq_invoices_account_idempotency"
        ),
    )
    op.create_index("ix_invoices_account_created", "invoices", ["account_id", "created_at"])

    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("external_payment_id", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount >= 0", name="ck_payments_amount_nonnegative"),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payments_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id", "account_id"],
            ["invoices.id", "invoices.account_id"],
            name="fk_payments_invoice_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.UniqueConstraint(
            "id", "account_id", "invoice_id", name="uq_payments_id_account_invoice"
        ),
        sa.UniqueConstraint(
            "provider", "external_payment_id", name="uq_payments_provider_external_id"
        ),
        sa.UniqueConstraint(
            "account_id", "idempotency_key", name="uq_payments_account_idempotency"
        ),
    )
    op.create_index("ix_payments_account_created", "payments", ["account_id", "created_at"])
    op.create_index(
        "uq_payments_succeeded_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("payment_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("provider_status", sa.String(length=32), nullable=True),
        sa.Column(
            "processing_status",
            sa.String(length=32),
            server_default="received",
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("provider_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payment_events_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payment_id", "account_id", "invoice_id"],
            ["payments.id", "payments.account_id", "payments.invoice_id"],
            name="fk_payment_events_payment_account_invoice",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_events"),
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_payment_events_provider_event"
        ),
    )
    op.create_index(
        "ix_payment_events_account_received",
        "payment_events",
        ["account_id", "received_at"],
    )

    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=True),
        sa.Column("metric", sa.String(length=100), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("model_id", sa.String(length=100), nullable=True),
        sa.Column("price_version", sa.Integer(), nullable=True),
        sa.Column("dancoin_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("corrects_record_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(model_id IS NULL) = (price_version IS NULL)",
            name="ck_usage_records_model_price_version_pair",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_usage_records_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_usage_records_project_account",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "price_version"],
            ["llm_models.model_id", "llm_models.version"],
            name="fk_usage_records_model_price_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["corrects_record_id"],
            ["usage_records.id"],
            name="fk_usage_records_corrected_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_usage_records"),
        sa.UniqueConstraint("idempotency_key", name="uq_usage_records_idempotency_key"),
    )
    op.create_index(
        "ix_usage_records_account_period_metric",
        "usage_records",
        ["account_id", "period_start", "metric"],
    )
    op.create_index(
        "ix_usage_records_project_period_metric",
        "usage_records",
        ["project_id", "period_start", "metric"],
    )

    op.create_table(
        "agent_slots",
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("slot_limit", sa.Integer(), nullable=False),
        sa.Column("used_slots", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("slot_limit >= 0", name="ck_agent_slots_limit_nonnegative"),
        sa.CheckConstraint("used_slots >= 0", name="ck_agent_slots_used_nonnegative"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_slots_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("project_id", name="pk_agent_slots"),
    )

    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_dancoin_transaction_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'Dancoin transactions are immutable'; "
            "END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_dancoin_transactions_immutable "
            "BEFORE UPDATE OR DELETE ON dancoin_transactions "
            "FOR EACH ROW EXECUTE FUNCTION prevent_dancoin_transaction_mutation()"
        )
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_invoice_snapshot_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF TG_OP = 'DELETE' OR NEW.snapshot IS DISTINCT FROM OLD.snapshot THEN "
            "RAISE EXCEPTION 'Invoice snapshots are immutable'; "
            "END IF; RETURN NEW; END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_invoices_snapshot_immutable "
            "BEFORE UPDATE OR DELETE ON invoices "
            "FOR EACH ROW EXECUTE FUNCTION prevent_invoice_snapshot_mutation()"
        )
    )
    op.execute(
        sa.text(
            "CREATE FUNCTION prevent_subscription_snapshot_mutation() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF TG_OP = 'DELETE' OR (NEW.snapshot IS DISTINCT FROM OLD.snapshot "
            "AND NEW.current_period_start = OLD.current_period_start) THEN "
            "RAISE EXCEPTION 'Subscription snapshots are immutable within a period'; "
            "END IF; RETURN NEW; END; $$"
        )
    )
    op.execute(
        sa.text(
            "CREATE TRIGGER trg_subscriptions_snapshot_immutable "
            "BEFORE UPDATE OR DELETE ON subscriptions "
            "FOR EACH ROW EXECUTE FUNCTION prevent_subscription_snapshot_mutation()"
        )
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_subscriptions_snapshot_immutable ON subscriptions")
    op.execute("DROP FUNCTION IF EXISTS prevent_subscription_snapshot_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_invoices_snapshot_immutable ON invoices")
    op.execute("DROP FUNCTION IF EXISTS prevent_invoice_snapshot_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_dancoin_transactions_immutable ON dancoin_transactions")
    op.execute("DROP FUNCTION IF EXISTS prevent_dancoin_transaction_mutation()")

    op.drop_table("agent_slots")
    op.drop_index("ix_usage_records_project_period_metric", table_name="usage_records")
    op.drop_index("ix_usage_records_account_period_metric", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_index("ix_payment_events_account_received", table_name="payment_events")
    op.drop_table("payment_events")
    op.drop_index("uq_payments_succeeded_invoice", table_name="payments")
    op.drop_index("ix_payments_account_created", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_invoices_account_created", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_dancoin_reservations_account_status", table_name="dancoin_reservations")
    op.drop_table("dancoin_reservations")
    op.drop_index("ix_dancoin_transactions_account_time", table_name="dancoin_transactions")
    op.drop_table("dancoin_transactions")
    op.drop_table("dancoin_balances")
    op.drop_index("ix_subscriptions_status_period_end", table_name="subscriptions")
    op.drop_index("uq_subscriptions_trial_account", table_name="subscriptions")
    op.drop_index("uq_subscriptions_current_account", table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_index("ix_dancoin_packages_active", table_name="dancoin_packages")
    op.drop_table("dancoin_packages")
    op.drop_index("ix_llm_models_active", table_name="llm_models")
    op.drop_table("llm_models")
    op.drop_index("ix_tariff_plans_active", table_name="tariff_plans")
    op.drop_table("tariff_plans")
