from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_seller_core.persistence.base import Base


def new_id() -> str:
    return str(uuid4())


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_background_jobs_idempotency"),
        Index("ix_background_jobs_claim", "queue", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    queue: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class InboxEvent(Base):
    __tablename__ = "inbox"
    __table_args__ = (
        UniqueConstraint("channel_id", "provider_event_id", name="uq_inbox_provider_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_event: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxMessage(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_delivery", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_message_id: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DialogSession(Base):
    __tablename__ = "dialog_sessions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    release_id: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="bot")
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_fence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
