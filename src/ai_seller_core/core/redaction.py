import re
from uuid import UUID

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\w)\+?\d(?:[\s().-]*\d){7,14}(?!\w)")


def redact_personal_data(value: str) -> str:
    try:
        UUID(value)
    except ValueError:
        pass
    else:
        return value

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

    return _PHONE.sub(redact_phone, value)
