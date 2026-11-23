from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_seller_core.persistence.base import Base
from ai_seller_core.persistence.safe_json import PrivacyRedactedJSONB


def _new_id() -> str:
    return str(uuid4())


class TariffPlan(Base):
    __tablename__ = "tariff_plans"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_tariff_plans_code_version"),
        CheckConstraint("version > 0", name="ck_tariff_plans_version_positive"),
        CheckConstraint(
            "included_agents IS NULL OR included_agents >= 0",
            name="ck_tariff_plans_included_agents_nonnegative",
        ),
        CheckConstraint(
            "max_agents IS NULL OR max_agents >= 0",
            name="ck_tariff_plans_max_agents_nonnegative",
        ),
        CheckConstraint(
            "max_agents IS NULL OR included_agents IS NULL OR max_agents >= included_agents",
            name="ck_tariff_plans_agent_limits_order",
        ),
        CheckConstraint(
            "extra_agent_daily_dancoin IS NULL OR extra_agent_daily_dancoin >= 0",
            name="ck_tariff_plans_extra_agent_cost_nonnegative",
        ),
        Index("ix_tariff_plans_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    included_agents: Mapped[int | None] = mapped_column(Integer)
    max_agents: Mapped[int | None] = mapped_column(Integer)
    extra_agent_daily_dancoin: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    features: Mapped[dict] = mapped_column(
        PrivacyRedactedJSONB(),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    limits: Mapped[dict] = mapped_column(
        PrivacyRedactedJSONB(),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    dancoin_grant_conditions: Mapped[dict] = mapped_column(
        PrivacyRedactedJSONB(),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    billing_options: Mapped[dict] = mapped_column(
        PrivacyRedactedJSONB(),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_llm_models_model_version"),
        CheckConstraint("version > 0", name="ck_llm_models_version_positive"),
        CheckConstraint(
            "input_price_per_1k IS NULL OR input_price_per_1k >= 0",
            name="ck_llm_models_input_price_nonnegative",
        ),
        CheckConstraint(
            "output_price_per_1k IS NULL OR output_price_per_1k >= 0",
            name="ck_llm_models_output_price_nonnegative",
        ),
        CheckConstraint(
            "long_context_threshold_tokens IS NULL OR long_context_threshold_tokens >= 0",
            name="ck_llm_models_context_threshold_nonnegative",
        ),
        CheckConstraint(
            "long_context_input_price_per_1k IS NULL OR long_context_input_price_per_1k >= 0",
            name="ck_llm_models_long_input_price_nonnegative",
        ),
        CheckConstraint(
            "long_context_output_price_per_1k IS NULL OR long_context_output_price_per_1k >= 0",
            name="ck_llm_models_long_output_price_nonnegative",
        ),
        CheckConstraint(
            "NOT is_active OR (input_price_per_1k IS NOT NULL AND output_price_per_1k IS NOT NULL)",
            name="ck_llm_models_active_has_base_prices",
        ),
        Index("ix_llm_models_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    input_price_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    output_price_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    long_context_threshold_tokens: Mapped[int | None] = mapped_column(BigInteger)
    long_context_input_price_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    long_context_output_price_per_1k: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    capabilities: Mapped[dict] = mapped_column(
        PrivacyRedactedJSONB(),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DancoinPackage(Base):
    __tablename__ = "dancoin_packages"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_dancoin_packages_code_version"),
        CheckConstraint("version > 0", name="ck_dancoin_packages_version_positive"),
        CheckConstraint("dancoin_amount > 0", name="ck_dancoin_packages_amount_positive"),
        CheckConstraint("price_amount >= 0", name="ck_dancoin_packages_price_nonnegative"),
        Index("ix_dancoin_packages_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dancoin_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('trial', 'active', 'grace_period', 'canceled', 'parking')",
            name="ck_subscriptions_status",
        ),
        CheckConstraint(
            "current_period_end IS NULL OR current_period_end >= current_period_start",
            name="ck_subscriptions_period_order",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_subscriptions_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tariff_plan_id"],
            ["tariff_plans.id"],
            name="fk_subscriptions_tariff_plan",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_subscriptions_current_account",
            "account_id",
            unique=True,
            postgresql_where=text("status IN ('trial', 'active', 'grace_period', 'parking')"),
        ),
        Index(
            "uq_subscriptions_trial_account",
            "account_id",
            unique=True,
            postgresql_where=text("trial_used"),
        ),
        Index("ix_subscriptions_status_period_end", "status", "current_period_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tariff_plan_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    trial_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict] = mapped_column(PrivacyRedactedJSONB(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DancoinBalance(Base):
    __tablename__ = "dancoin_balances"
    __table_args__ = (
        CheckConstraint("grant_balance >= 0", name="ck_dancoin_balances_grant_nonnegative"),
        CheckConstraint(
            "purchased_balance >= 0",
            name="ck_dancoin_balances_purchased_nonnegative",
        ),
        CheckConstraint(
            "reserved_grant >= 0 AND reserved_purchased >= 0",
            name="ck_dancoin_balances_reserved_nonnegative",
        ),
        CheckConstraint("debt_balance >= 0", name="ck_dancoin_balances_debt_nonnegative"),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_balances_account",
            ondelete="RESTRICT",
        ),
    )

    account_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    grant_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    purchased_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    reserved_grant: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    reserved_purchased: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    debt_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class DancoinTransaction(Base):
    __tablename__ = "dancoin_transactions"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_dancoin_transactions_reference"),
        CheckConstraint(
            "type IN ('grant', 'purchase', 'reserve', 'settle', 'release', "
            "'spend_agent', 'refund', 'timeout_charge', 'expire', 'adjustment')",
            name="ck_dancoin_transactions_type",
        ),
        CheckConstraint(
            "source IS NULL OR source IN ('grant', 'purchase', 'system')",
            name="ck_dancoin_transactions_source",
        ),
        CheckConstraint("amount <> 0", name="ck_dancoin_transactions_amount_nonzero"),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_transactions_account",
            ondelete="RESTRICT",
        ),
        Index("ix_dancoin_transactions_account_time", "account_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reference: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_version: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict | None] = mapped_column(PrivacyRedactedJSONB())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DancoinReservation(Base):
    __tablename__ = "dancoin_reservations"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_dancoin_reservations_operation"),
        CheckConstraint("amount > 0", name="ck_dancoin_reservations_amount_positive"),
        CheckConstraint(
            "grant_amount >= 0 AND purchased_amount >= 0 "
            "AND amount = grant_amount + purchased_amount",
            name="ck_dancoin_reservations_source_amounts",
        ),
        CheckConstraint(
            "status IN ('reserved', 'settled', 'released', 'timeout_charged')",
            name="ck_dancoin_reservations_status",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_dancoin_reservations_account",
            ondelete="RESTRICT",
        ),
        Index("ix_dancoin_reservations_account_status", "account_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    grant_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    purchased_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("number", name="uq_invoices_number"),
        UniqueConstraint("id", "account_id", name="uq_invoices_id_account"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_invoices_account_idempotency"),
        CheckConstraint("total >= 0", name="ck_invoices_total_nonnegative"),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_invoices_account",
            ondelete="RESTRICT",
        ),
        Index("ix_invoices_account_created", "account_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    number: Mapped[str] = mapped_column(String(100), nullable=False)
    purchase_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict] = mapped_column(PrivacyRedactedJSONB(), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_payment_id",
            name="uq_payments_provider_external_id",
        ),
        UniqueConstraint("id", "account_id", "invoice_id", name="uq_payments_id_account_invoice"),
        UniqueConstraint("account_id", "idempotency_key", name="uq_payments_account_idempotency"),
        CheckConstraint("amount >= 0", name="ck_payments_amount_nonnegative"),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payments_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["invoice_id", "account_id"],
            ["invoices.id", "invoices.account_id"],
            name="fk_payments_invoice_account",
            ondelete="RESTRICT",
        ),
        Index("ix_payments_account_created", "account_id", "created_at"),
        Index(
            "uq_payments_succeeded_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    external_payment_id: Mapped[str | None] = mapped_column(String(200))
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_payment_events_provider_event",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_payment_events_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["payment_id", "account_id", "invoice_id"],
            ["payments.id", "payments.account_id", "payments.invoice_id"],
            name="fk_payment_events_payment_account_invoice",
            ondelete="RESTRICT",
        ),
        Index("ix_payment_events_account_received", "account_id", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received", server_default="received"
    )
    payload: Mapped[dict | None] = mapped_column(PrivacyRedactedJSONB())
    provider_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_usage_records_idempotency_key"),
        CheckConstraint(
            "(model_id IS NULL) = (price_version IS NULL)",
            name="ck_usage_records_model_price_version_pair",
        ),
        ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_usage_records_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_usage_records_project_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["model_id", "price_version"],
            ["llm_models.model_id", "llm_models.version"],
            name="fk_usage_records_model_price_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["corrects_record_id"],
            ["usage_records.id"],
            name="fk_usage_records_corrected_record",
            ondelete="RESTRICT",
        ),
        Index("ix_usage_records_account_period_metric", "account_id", "period_start", "metric"),
        Index("ix_usage_records_project_period_metric", "project_id", "period_start", "metric"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(100))
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    model_id: Mapped[str | None] = mapped_column(String(100))
    price_version: Mapped[int | None] = mapped_column(Integer)
    dancoin_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    corrects_record_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentSlot(Base):
    __tablename__ = "agent_slots"
    __table_args__ = (
        CheckConstraint("slot_limit >= 0", name="ck_agent_slots_limit_nonnegative"),
        CheckConstraint("used_slots >= 0", name="ck_agent_slots_used_nonnegative"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_agent_slots_project",
            ondelete="RESTRICT",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    slot_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    used_slots: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
