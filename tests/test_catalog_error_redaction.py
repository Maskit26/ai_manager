from sqlalchemy.engine.default import DefaultDialect

from ai_seller_core.persistence.catalog_models import CatalogImportError


def test_catalog_import_error_fields_redact_personal_data_on_bind():
    raw_value = "Contact jane.doe@example.com at 202-555-0143; api_key=secret-value"
    dialect = DefaultDialect()

    for column_name in ("error_message", "safe_fragment"):
        column_type = CatalogImportError.__table__.c[column_name].type
        bind_processor = column_type.bind_processor(dialect)
        redacted_value = bind_processor(raw_value)

        assert "jane.doe@example.com" not in redacted_value
        assert "202-555-0143" not in redacted_value
        assert "secret-value" not in redacted_value
