from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_seller_core.persistence.base import Base
from ai_seller_core.persistence.safe_json import PrivacyRedactedJSONB, RedactedJSONB


def _new_id() -> str:
    return str(uuid4())


class ToolExecution(Base):
    __tablename__ = "tool_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_tool_executions_project_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "session_id", "message_id"],
            ["chat_messages.project_id", "chat_messages.session_id", "chat_messages.id"],
            name="fk_tool_executions_project_session_message",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_tool_executions_project_session_message",
            "project_id",
            "session_id",
            "message_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(36))
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(RedactedJSONB(), nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(RedactedJSONB())
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_leads_project_id"),
        UniqueConstraint("project_id", "idempotency_key", name="uq_leads_project_idempotency"),
        ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_leads_project_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "customer_id"],
            ["customers.project_id", "customers.id"],
            name="fk_leads_project_customer",
            ondelete="RESTRICT",
        ),
        Index("ix_leads_project_status_created", "project_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    customer_confirmed: Mapped[bool] = mapped_column(nullable=False, default=False)
    consent_given: Mapped[bool] = mapped_column(nullable=False, default=False)
    request_data: Mapped[dict[str, Any]] = mapped_column(
        RedactedJSONB(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class LeadItem(Base):
    __tablename__ = "lead_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "lead_id"],
            ["leads.project_id", "leads.id"],
            name="fk_lead_items_project_lead",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "product_id"],
            ["products.project_id", "products.id"],
            name="fk_lead_items_project_product",
            ondelete="RESTRICT",
        ),
        Index("ix_lead_items_project_lead", "project_id", "lead_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    lead_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_id: Mapped[str | None] = mapped_column(String(36))
    quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    product_snapshot: Mapped[dict[str, Any]] = mapped_column(RedactedJSONB(), nullable=False)


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_escalations_project_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_escalations_project_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["assignee_membership_id", "account_id"],
            ["account_memberships.id", "account_memberships.account_id"],
            name="fk_escalations_assignee_membership_account",
            ondelete="RESTRICT",
        ),
        Index("ix_escalations_project_status", "project_id", "status"),
        Index("ix_escalations_project_sla", "project_id", "sla_deadline"),
        Index(
            "uq_escalations_open_project_session",
            "project_id",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_membership_id: Mapped[str | None] = mapped_column(String(36))
    sla_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_audit_log_project_account",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "actor_id IS NULL OR system_actor IS NULL",
            name="ck_audit_log_actor_source_exclusive",
        ),
        Index("ix_audit_log_project_time_type", "project_id", "created_at", "action"),
        Index("ix_audit_log_account_time_type", "account_id", "created_at", "action"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_actor_id", "actor_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str | None] = mapped_column(String(100))
    actor_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("users.id", name="fk_audit_log_actor_id_users", ondelete="RESTRICT"),
    )
    system_actor: Mapped[str | None] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)
    diff: Mapped[dict[str, Any]] = mapped_column(
        PrivacyRedactedJSONB(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectDeletionRequest(Base):
    __tablename__ = "project_deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'cancelled')",
            name="ck_project_deletion_requests_status",
        ),
        Index(
            "ix_project_deletion_requests_status_requested",
            "status",
            "requested_at",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    previous_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LlmCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_llm_calls_project_account",
            ondelete="RESTRICT",
        ),
        Index("ix_llm_calls_account_time", "account_id", "created_at"),
        Index("ix_llm_calls_project_time", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    purpose: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "period_start",
            "metric",
            name="uq_usage_counters_project_period_metric",
        ),
        ForeignKeyConstraint(
            ["project_id", "account_id"],
            ["projects.id", "projects.account_id"],
            name="fk_usage_counters_project_account",
            ondelete="RESTRICT",
        ),
        Index("ix_usage_counters_account_time", "account_id", "period_start"),
        Index("ix_usage_counters_project_time", "project_id", "period_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
