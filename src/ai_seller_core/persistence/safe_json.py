import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

_SECRET_KEY_NAMES = {
    "access-token",
    "api-key",
    "authorization",
    "credential",
    "password",
    "passwd",
    "private-key",
    "refresh-token",
    "secret",
    "token",
}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|"
    r"passwd|secret|credential|private[_-]?key)\b"
    r"(\s*[:=]\s*)(['\"]?)([^'\"\s,;&]+)"
)
_AUTHORIZATION_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization)\b(\s*[:=]\s*)(['\"]?)(?:Bearer\s+)?([^'\";,]+)"
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|"
    r"password|secret|authorization)=)[^&\s]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_URL_PASSWORD = re.compile(r"(://[^:/\s]+:)[^@/\s]+@")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){7,14}(?!\w)")
_PERSONAL_KEYS = {
    "address",
    "e-mail",
    "email",
    "email-address",
    "mobile",
    "phone",
    "phone-number",
    "telephone",
}


def _is_sensitive_key(key: str) -> bool:
    normalized_key = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", key).casefold()
    normalized_key = re.sub(r"[_\s]+", "-", normalized_key)
    if normalized_key in _SECRET_KEY_NAMES:
        return True
    return any(
        normalized_key.endswith(f"-{secret_name}")
        for secret_name in _SECRET_KEY_NAMES
        if secret_name not in {"authorization", "credential"}
    )


def redact_sensitive_data(value: Any, *, mask_personal_data: bool = False) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _is_sensitive_key(str(key))
                or (mask_personal_data and str(key).casefold().replace("_", "-") in _PERSONAL_KEYS)
                else redact_sensitive_data(item, mask_personal_data=mask_personal_data)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            redact_sensitive_data(item, mask_personal_data=mask_personal_data) for item in value
        ]
    if isinstance(value, str):
        value = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        value = _AUTHORIZATION_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        value = _SECRET_QUERY.sub(r"\1[REDACTED]", value)
        value = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
        value = _URL_PASSWORD.sub(r"\1[REDACTED]@", value)
        if mask_personal_data:
            value = _EMAIL.sub("[REDACTED_EMAIL]", value)

            def redact_phone(match: re.Match[str]) -> str:
                phone = match.group(0)
                digits = re.sub(r"\D", "", phone)
                if (
                    8 <= len(digits) <= 15
                    and (phone.startswith("+") or re.search(r"[\s().-]", phone))
                    and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", phone)
                ):
                    return "[REDACTED_PHONE]"
                return phone

            value = _PHONE.sub(redact_phone, value)
        return value
    return value


class RedactedJSONB(TypeDecorator[Any]):
    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return redact_sensitive_data(value)


class RedactedText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return redact_sensitive_data(value) if value is not None else None


class PrivacyRedactedText(RedactedText):
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        return redact_sensitive_data(value, mask_personal_data=True) if value is not None else None


class PrivacyRedactedJSONB(RedactedJSONB):
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return redact_sensitive_data(value, mask_personal_data=True)
