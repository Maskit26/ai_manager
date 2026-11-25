from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_seller_core.persistence.base import Base
from ai_seller_core.persistence.safe_json import PrivacyRedactedText


def _new_id() -> str:
    return str(uuid4())


class CatalogSource(Base):
    __tablename__ = "catalog_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_catalog_sources_project_id"),
        CheckConstraint("kind IN ('csv', 'xlsx')", name="ck_catalog_sources_kind"),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_catalog_sources_status",
        ),
        Index(
            "uq_catalog_sources_authoritative",
            "project_id",
            unique=True,
            postgresql_where=text("is_authoritative"),
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_sources_project",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="inactive")
    is_authoritative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mapping: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CatalogImport(Base):
    __tablename__ = "catalog_imports"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_catalog_imports_project_id"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_catalog_imports_status",
        ),
        CheckConstraint(
            "total_rows >= 0 AND processed_rows >= 0 AND error_count >= 0",
            name="ck_catalog_imports_counts",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_imports_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["catalog_sources.project_id", "catalog_sources.id"],
            name="fk_catalog_imports_source_project",
            ondelete="RESTRICT",
        ),
        Index("ix_catalog_imports_source", "project_id", "source_id"),
        Index("ix_catalog_imports_status", "project_id", "status"),
        Index("ix_catalog_imports_created_at", "project_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_read_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    structural_error: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deactivation_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatalogImportError(Base):
    __tablename__ = "catalog_import_errors"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "import_id",
            "row_number",
            name="uq_catalog_import_errors_import_row",
        ),
        CheckConstraint("row_number > 0", name="ck_catalog_import_errors_row_number"),
        CheckConstraint(
            "category IN ('completeness-critical', 'non-critical')",
            name="ck_catalog_import_errors_category",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_import_errors_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "import_id"],
            ["catalog_imports.project_id", "catalog_imports.id"],
            name="fk_catalog_import_errors_import_project",
            ondelete="CASCADE",
        ),
        Index("ix_catalog_import_errors_import", "project_id", "import_id"),
        Index("ix_catalog_import_errors_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    import_id: Mapped[str] = mapped_column(String(36), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(PrivacyRedactedText(), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    safe_fragment: Mapped[str | None] = mapped_column(PrivacyRedactedText())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_products_project_id"),
        UniqueConstraint(
            "project_id",
            "source_id",
            "external_id",
            name="uq_products_source_external",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_products_status",
        ),
        CheckConstraint("stock >= 0", name="ck_products_stock"),
        CheckConstraint("version > 0", name="ck_products_version"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_products_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["catalog_sources.project_id", "catalog_sources.id"],
            name="fk_products_source_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "last_seen_import_id"],
            ["catalog_imports.project_id", "catalog_imports.id"],
            name="fk_products_last_import_project",
            ondelete="RESTRICT",
        ),
        Index("ix_products_sku", "project_id", "sku"),
        Index("ix_products_category", "project_id", "category"),
        Index("ix_products_activity", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str | None] = mapped_column(String(255))
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2))
    stock: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_seen_import_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_customers_project_id"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_customers_project",
            ondelete="RESTRICT",
        ),
        Index("ix_customers_project", "project_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    profile: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    consent_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    consent_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CustomerIdentity(Base):
    __tablename__ = "customer_identities"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_customer_identities_project_id"),
        UniqueConstraint(
            "project_id",
            "channel_type",
            "external_user_id",
            name="uq_customer_identities_external",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'verified')",
            name="ck_customer_identities_verification_status",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_customer_identities_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "customer_id"],
            ["customers.project_id", "customers.id"],
            name="fk_customer_identities_customer_project",
            ondelete="CASCADE",
        ),
        Index("ix_customer_identities_customer", "project_id", "customer_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_value: Mapped[str | None] = mapped_column(String(255))
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unverified"
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_source: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KbDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_kb_documents_project_id"),
        UniqueConstraint(
            "project_id",
            "source_key",
            "version",
            name="uq_kb_documents_source_version",
        ),
        CheckConstraint("version > 0", name="ck_kb_documents_version"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'active', 'failed', 'archived')",
            name="ck_kb_documents_status",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_kb_documents_project",
            ondelete="RESTRICT",
        ),
        Index("ix_kb_documents_processing_status", "project_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    source_key: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    content: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "document_id",
            "chunk_index",
            name="uq_kb_chunks_document_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_kb_chunks_index"),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_kb_chunks_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["kb_documents.project_id", "kb_documents.id"],
            name="fk_kb_chunks_document_project",
            ondelete="CASCADE",
        ),
        Index("ix_kb_chunks_document", "project_id", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    vector_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
