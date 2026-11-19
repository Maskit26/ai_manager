"""Add catalog, customer, and knowledge-base persistence."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261120_catalog_customers_kb"
down_revision = "20261119_dialog_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "is_authoritative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("kind IN ('csv', 'xlsx')", name="ck_catalog_sources_kind"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_catalog_sources_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_sources_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_sources"),
        sa.UniqueConstraint("project_id", "id", name="uq_catalog_sources_project_id"),
    )
    op.create_index(
        "uq_catalog_sources_authoritative",
        "catalog_sources",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_authoritative"),
    )

    op.create_table(
        "catalog_imports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("processed_rows", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column(
            "source_read_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "structural_error",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "deactivation_safe",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', "
            "'completed_with_errors', 'failed')",
            name="ck_catalog_imports_status",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND processed_rows >= 0 AND error_count >= 0",
            name="ck_catalog_imports_counts",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_imports_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["catalog_sources.project_id", "catalog_sources.id"],
            name="fk_catalog_imports_source_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_imports"),
        sa.UniqueConstraint("project_id", "id", name="uq_catalog_imports_project_id"),
    )
    op.create_index(
        "ix_catalog_imports_source", "catalog_imports", ["project_id", "source_id"]
    )
    op.create_index(
        "ix_catalog_imports_status", "catalog_imports", ["project_id", "status"]
    )
    op.create_index(
        "ix_catalog_imports_created_at", "catalog_imports", ["project_id", "created_at"]
    )

    op.create_table(
        "catalog_import_errors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("import_id", sa.String(length=36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("safe_fragment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("row_number > 0", name="ck_catalog_import_errors_row_number"),
        sa.CheckConstraint(
            "category IN ('completeness-critical', 'non-critical')",
            name="ck_catalog_import_errors_category",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_catalog_import_errors_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "import_id"],
            ["catalog_imports.project_id", "catalog_imports.id"],
            name="fk_catalog_import_errors_import_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_catalog_import_errors"),
        sa.UniqueConstraint(
            "project_id",
            "import_id",
            "row_number",
            name="uq_catalog_import_errors_import_row",
        ),
    )
    op.create_index(
        "ix_catalog_import_errors_import",
        "catalog_import_errors",
        ["project_id", "import_id"],
    )

    op.create_table(
        "products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("sku", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("stock", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_seen_import_id", sa.String(length=36), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("stock IS NULL OR stock >= 0", name="ck_products_stock"),
        sa.CheckConstraint("version > 0", name="ck_products_version"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_products_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_products_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_id"],
            ["catalog_sources.project_id", "catalog_sources.id"],
            name="fk_products_source_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "last_seen_import_id"],
            ["catalog_imports.project_id", "catalog_imports.id"],
            name="fk_products_last_import_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("project_id", "id", name="uq_products_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "source_id",
            "external_id",
            name="uq_products_source_external",
        ),
    )
    op.create_index("ix_products_sku", "products", ["project_id", "sku"])
    op.create_index("ix_products_category", "products", ["project_id", "category"])
    op.create_index("ix_products_activity", "products", ["project_id", "status"])

    op.create_table(
        "customers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column(
            "profile",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("consent_status", sa.String(length=30), nullable=False),
        sa.Column("consent_version", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_customers_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
        sa.UniqueConstraint("project_id", "id", name="uq_customers_project_id"),
    )
    op.create_index("ix_customers_project", "customers", ["project_id"])

    op.create_table(
        "customer_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("customer_id", sa.String(length=36), nullable=False),
        sa.Column("channel_type", sa.String(length=50), nullable=False),
        sa.Column("external_user_id", sa.String(length=255), nullable=False),
        sa.Column("contact_value", sa.String(length=255), nullable=True),
        sa.Column("verification_status", sa.String(length=20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_source", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verified')",
            name="ck_customer_identities_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_customer_identities_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "customer_id"],
            ["customers.project_id", "customers.id"],
            name="fk_customer_identities_customer_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_identities"),
        sa.UniqueConstraint("project_id", "id", name="uq_customer_identities_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "channel_type",
            "external_user_id",
            name="uq_customer_identities_external",
        ),
    )
    op.create_index(
        "ix_customer_identities_customer",
        "customer_identities",
        ["project_id", "customer_id"],
    )

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("source_key", sa.String(length=500), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("version > 0", name="ck_kb_documents_version"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'active', 'failed', 'archived')",
            name="ck_kb_documents_status",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_kb_documents_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kb_documents"),
        sa.UniqueConstraint("project_id", "id", name="uq_kb_documents_project_id"),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "version",
            name="uq_kb_documents_source_version",
        ),
    )
    op.create_index(
        "ix_kb_documents_processing_status",
        "kb_documents",
        ["project_id", "status"],
    )

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("vector_id", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_kb_chunks_index"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_kb_chunks_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["kb_documents.project_id", "kb_documents.id"],
            name="fk_kb_chunks_document_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kb_chunks"),
        sa.UniqueConstraint(
            "project_id",
            "document_id",
            "chunk_index",
            name="uq_kb_chunks_document_index",
        ),
    )
    op.create_index(
        "ix_kb_chunks_document", "kb_chunks", ["project_id", "document_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_document", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_kb_documents_processing_status", table_name="kb_documents")
    op.drop_table("kb_documents")
    op.drop_index("ix_customer_identities_customer", table_name="customer_identities")
    op.drop_table("customer_identities")
    op.drop_index("ix_customers_project", table_name="customers")
    op.drop_table("customers")
    op.drop_index("ix_products_activity", table_name="products")
    op.drop_index("ix_products_category", table_name="products")
    op.drop_index("ix_products_sku", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_catalog_import_errors_import", table_name="catalog_import_errors")
    op.drop_table("catalog_import_errors")
    op.drop_index("ix_catalog_imports_created_at", table_name="catalog_imports")
    op.drop_index("ix_catalog_imports_status", table_name="catalog_imports")
    op.drop_index("ix_catalog_imports_source", table_name="catalog_imports")
    op.drop_table("catalog_imports")
    op.drop_index("uq_catalog_sources_authoritative", table_name="catalog_sources")
    op.drop_table("catalog_sources")
