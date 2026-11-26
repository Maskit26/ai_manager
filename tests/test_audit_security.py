import pytest

from ai_seller_core.core.redaction import redact_personal_data
from ai_seller_core.persistence.application_models import validate_audit_diff
from ai_seller_core.persistence.safe_json import PrivacyRedactedJSONB


def test_audit_diff_accepts_allowlisted_fields_for_known_actions() -> None:
    diff = {"user_id": "backend-user-1", "role": "operator"}

    assert validate_audit_diff("membership.added", diff) == diff


def test_audit_diff_rejects_unallowlisted_action_fields_and_values() -> None:
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_audit_diff(
            "membership.added",
            {"user_id": "backend-user-1", "email": "alice@example.com"},
        )

    with pytest.raises(ValueError, match="action is not allowlisted"):
        validate_audit_diff("unknown.action", {})

    with pytest.raises(TypeError, match="values must be strings"):
        validate_audit_diff("membership.added", {"user_id": 123})


def test_audit_diff_redacts_secrets_and_personal_data_before_persistence() -> None:
    redacted = PrivacyRedactedJSONB().process_bind_param(
        {
            "user_id": "alice@example.com",
            "role": "operator +1 (415) 555-2671 token=private-token",
            "password": "must-not-be-persisted",
        },
        None,
    )

    assert redacted == {
        "user_id": "[REDACTED_EMAIL]",
        "role": "operator [REDACTED_PHONE] token=[REDACTED]",
        "password": "[REDACTED]",
    }


def test_redact_personal_data_preserves_uuid_identifiers() -> None:
    value = "550e8400-e29b-41d4-a716-446655440000"

    assert redact_personal_data(value) == value
