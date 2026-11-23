from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_seller_core.persistence.application_models import (
    AuditLog as AuditLog,
)
from ai_seller_core.persistence.application_models import (
    Escalation as Escalation,
)
from ai_seller_core.persistence.application_models import (
    Lead as Lead,
)
from ai_seller_core.persistence.application_models import (
    LeadItem as LeadItem,
)
from ai_seller_core.persistence.application_models import (
    LlmCall as LlmCall,
)
from ai_seller_core.persistence.application_models import (
    ProjectDeletionRequest as ProjectDeletionRequest,
)
from ai_seller_core.persistence.application_models import (
    ToolExecution as ToolExecution,
)
from ai_seller_core.persistence.application_models import (
    UsageCounter as UsageCounter,
)
from ai_seller_core.persistence.base import Base
from ai_seller_core.persistence.billing_models import (
    AgentSlot as AgentSlot,
)
from ai_seller_core.persistence.billing_models import (
    DancoinBalance as DancoinBalance,
)
from ai_seller_core.persistence.billing_models import (
    DancoinPackage as DancoinPackage,
)
from ai_seller_core.persistence.billing_models import (
    DancoinReservation as DancoinReservation,
)
from ai_seller_core.persistence.billing_models import (
    DancoinTransaction as DancoinTransaction,
)
from ai_seller_core.persistence.billing_models import (
    Invoice as Invoice,
)
from ai_seller_core.persistence.billing_models import (
    LlmModel as LlmModel,
)
from ai_seller_core.persistence.billing_models import (
    Payment as Payment,
)
from ai_seller_core.persistence.billing_models import (
    PaymentEvent as PaymentEvent,
)
from ai_seller_core.persistence.billing_models import (
    Subscription as Subscription,
)
from ai_seller_core.persistence.billing_models import (
    TariffPlan as TariffPlan,
)
from ai_seller_core.persistence.billing_models import (
    UsageRecord as UsageRecord,
)
from ai_seller_core.persistence.catalog_models import (
    CatalogImport as CatalogImport,
)
from ai_seller_core.persistence.catalog_models import (
    CatalogImportError as CatalogImportError,
)
from ai_seller_core.persistence.catalog_models import (
    CatalogSource as CatalogSource,
)
from ai_seller_core.persistence.catalog_models import (
    Customer as Customer,
)
from ai_seller_core.persistence.catalog_models import (
    CustomerIdentity as CustomerIdentity,
)
from ai_seller_core.persistence.catalog_models import (
    KbChunk as KbChunk,
)
from ai_seller_core.persistence.catalog_models import (
    KbDocument as KbDocument,
)
from ai_seller_core.persistence.catalog_models import (
    Product as Product,
)
from ai_seller_core.persistence.safe_json import (
    PrivacyRedactedJSONB,
    RedactedJSONB,
    RedactedText,
)


def new_id() -> str:
    return str(uuid4())


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("external_id", name="uq_accounts_external_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class AccountMembership(Base):
    __tablename__ = "account_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "user_id", name="uq_account_memberships_account_user"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'operator')",
            name="ck_account_memberships_role",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)


class AccountEntitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("account_id", name="uq_entitlements_account_id"),
        CheckConstraint(
            "max_projects IS NULL OR max_projects >= 0",
            name="ck_entitlements_max_projects_nonnegative",
        ),
        CheckConstraint(
            "max_catalog_sources_per_project IS NULL OR max_catalog_sources_per_project >= 0",
            name="ck_entitlements_max_catalog_sources_nonnegative",
        ),
        CheckConstraint(
            "max_products_per_project IS NULL OR max_products_per_project >= 0",
            name="ck_entitlements_max_products_nonnegative",
        ),
        CheckConstraint(
            "max_kb_documents_per_project IS NULL OR max_kb_documents_per_project >= 0",
            name="ck_entitlements_max_kb_documents_nonnegative",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    catalog_import_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    knowledge_base_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    multi_channel_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    max_projects: Mapped[int | None] = mapped_column(Integer)
    max_catalog_sources_per_project: Mapped[int | None] = mapped_column(
        Integer, default=1, server_default="1"
    )
    max_products_per_project: Mapped[int | None] = mapped_column(Integer)
    max_kb_documents_per_project: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="'{}'::jsonb"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("id", "account_id", name="uq_projects_id_account_id"),
        Index("ix_projects_account_id", "account_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    locale: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_channels_project_id_projects",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint("project_id", "channel_id", name="pk_channels"),
    )

    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_agents_project_id"),
        UniqueConstraint("project_id", "id", name="uq_agents_project_id_id"),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'disabled')",
            name="ck_agents_status",
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "active_release_id"],
            ["agent_releases.project_id", "agent_releases.agent_id", "agent_releases.id"],
            name="fk_agents_active_release",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft", server_default="draft"
    )
    active_release_id: Mapped[str | None] = mapped_column(String(36))


class AgentDraft(Base):
    __tablename__ = "agent_drafts"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(config) = 'object'", name="ck_agent_drafts_config_object"),
        ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["agents.project_id", "agents.id"],
            name="fk_agent_drafts_agent",
            ondelete="CASCADE",
        ),
    )

    project_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AgentRelease(Base):
    __tablename__ = "agent_releases"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_agent_releases_snapshot_object",
        ),
        CheckConstraint("version > 0", name="ck_agent_releases_version_positive"),
        ForeignKeyConstraint(
            ["project_id", "agent_id"],
            ["agents.project_id", "agents.id"],
            name="fk_agent_releases_agent",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("agent_id", "version", name="uq_agent_releases_agent_version"),
        UniqueConstraint("project_id", "id", name="uq_agent_releases_project_id_id"),
        UniqueConstraint(
            "project_id",
            "agent_id",
            "id",
            name="uq_agent_releases_project_agent_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency"),
        Index("ix_background_jobs_claim", "queue", "status", "available_at"),
        Index(
            "ix_background_jobs_status_available_priority",
            "status",
            "available_at",
            "priority",
        ),
        Index("ix_background_jobs_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    queue: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(RedactedJSONB(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(RedactedText())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class InboxEvent(Base):
    __tablename__ = "inbox"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "channel_id",
            "provider_event_id",
            name="uq_inbox_project_channel_event",
        ),
        Index("ix_inbox_project", "project_id"),
        ForeignKeyConstraint(
            ["project_id", "channel_id"],
            ["channels.project_id", "channels.channel_id"],
            name="fk_inbox_project_channel",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_event: Mapped[dict[str, Any]] = mapped_column(RedactedJSONB(), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(RedactedText())


class OutboxMessage(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_delivery", "status", "available_at"),
        Index("ix_outbox_project", "project_id"),
        ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_outbox_project_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "channel_id"],
            ["channels.project_id", "channels.channel_id"],
            name="fk_outbox_project_channel",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(80), nullable=False, default="outbound_message", server_default="outbound_message"
    )
    destination: Mapped[str] = mapped_column(String(200), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(RedactedJSONB(), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_message_id: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(RedactedText())
    delivery_reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DialogSession(Base):
    __tablename__ = "dialog_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_dialog_sessions_project_id_id"),
        UniqueConstraint(
            "project_id",
            "id",
            "channel_id",
            name="uq_dialog_sessions_project_id_channel_id",
        ),
        CheckConstraint("state IN ('active', 'closed')", name="ck_dialog_sessions_state"),
        CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('max_chat_closed', 'idle_ttl', 'manual')",
            name="ck_dialog_sessions_close_reason",
        ),
        CheckConstraint("reopen_count >= 0", name="ck_dialog_sessions_reopen_count"),
        CheckConstraint("lease_fence >= 0", name="ck_dialog_sessions_lease_fence"),
        CheckConstraint("version >= 0", name="ck_dialog_sessions_version"),
        ForeignKeyConstraint(
            ["project_id", "release_id"],
            ["agent_releases.project_id", "agent_releases.id"],
            name="fk_dialog_sessions_project_release",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "channel_id"],
            ["channels.project_id", "channels.channel_id"],
            name="fk_dialog_sessions_project_channel",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_dialog_sessions_active_lookup",
            "project_id",
            "channel_id",
            "external_chat_id",
            unique=True,
        ),
        Index(
            "ix_dialog_sessions_closed_resume",
            "project_id",
            "channel_id",
            "customer_id",
            "state",
        ),
    )
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    external_chat_id: Mapped[str] = mapped_column(String(200), nullable=False)
    release_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    variables: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(64))
    closed_by: Mapped[str | None] = mapped_column(String(100))
    reopen_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DomainEvent(Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        CheckConstraint("event_type <> ''", name="ck_domain_events_event_type"),
        CheckConstraint("schema_version > 0", name="ck_domain_events_schema_version"),
        CheckConstraint(
            "jsonb_typeof(tenant_ids) = 'object'",
            name="ck_domain_events_tenant_ids_object",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_domain_events_payload_object"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_domain_events_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "session_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id"],
            name="fk_domain_events_project_session",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_domain_events_project_session_occurred",
            "project_id",
            "session_id",
            "occurred_at",
        ),
        Index(
            "ix_domain_events_aggregate",
            "project_id",
            "aggregate_type",
            "aggregate_id",
            "occurred_at",
        ),
        Index(
            "ix_domain_events_project_time_type",
            "project_id",
            "occurred_at",
            "event_type",
        ),
        Index("ix_domain_events_occurred_at", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_ids: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        PrivacyRedactedJSONB(), nullable=False, default=dict
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
        Index("ix_chat_messages_project_created", "project_id", "created_at"),
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_messages_role",
        ),
        CheckConstraint(
            "delivery_status IN "
            "('received', 'pending', 'sending', 'sent', 'retrying', 'delivery_unknown', 'failed')",
            name="ck_chat_messages_delivery_status",
        ),
        UniqueConstraint(
            "project_id",
            "channel_id",
            "external_message_id",
            name="uq_chat_messages_external",
        ),
        UniqueConstraint(
            "project_id",
            "session_id",
            "id",
            name="uq_chat_messages_project_session_id",
        ),
        ForeignKeyConstraint(
            ["project_id", "session_id", "channel_id"],
            ["dialog_sessions.project_id", "dialog_sessions.id", "dialog_sessions.channel_id"],
            name="fk_chat_messages_project_session_channel",
            ondelete="RESTRICT",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(200))
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
