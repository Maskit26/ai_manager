import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any, Literal

from ai_seller_core.core.config import Settings

_CONTEXT_FIELDS = ("queue", "job_id", "kind", "correlation_id")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization)\b"
    r"(\s*[:=]\s*)([^\s,;&]+)"
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:access_token|token|api[_-]?key|password|secret|authorization)=)"
    r"[^&\s]+"
)
_URL_PASSWORD = re.compile(r"(?i)(://[^:/@\s]+:)[^@\s]+(@)")


class _Redactor:
    def __init__(self, secrets: tuple[str, ...]) -> None:
        self.secrets = tuple(secret for secret in secrets if secret)

    def __call__(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        value = _SECRET_QUERY.sub(r"\1[REDACTED]", value)
        return _URL_PASSWORD.sub(r"\1[REDACTED]\2", value)


class _ProcessFilter(logging.Filter):
    def __init__(self, process_type: Literal["api", "worker"]) -> None:
        super().__init__()
        self.process_type = process_type

    def filter(self, record: logging.LogRecord) -> bool:
        record.process_type = self.process_type
        return True


class _BaseFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self.redact = _Redactor(secrets)

    def _fields(self, record: logging.LogRecord) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "process": getattr(record, "process_type", "unknown"),
            "logger": record.name,
            "message": self.redact(record.getMessage()),
        }
        for name in _CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                fields[name] = self.redact(str(value))
        if record.exc_info:
            fields["exception"] = self.redact(self.formatException(record.exc_info))
        return fields


class _JsonFormatter(_BaseFormatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self._fields(record), ensure_ascii=False)


class _TextFormatter(_BaseFormatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = self._fields(record)
        context = " ".join(
            f"{name}={fields[name]}" for name in _CONTEXT_FIELDS if name in fields
        )
        message = (
            f"{fields['timestamp']} {fields['level']} "
            f"process={fields['process']} logger={fields['logger']} "
            f"{fields['message']}"
        )
        if context:
            message = f"{message} {context}"
        if "exception" in fields:
            message = f"{message}\n{fields['exception']}"
        return message


def configure_logging(
    process_type: Literal["api", "worker"],
    settings: Settings | None = None,
) -> None:
    if settings is None:
        level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            level_name = "INFO"
        log_format = os.getenv("LOG_FORMAT", "json").lower()
        if log_format not in {"json", "text"}:
            log_format = "json"
        secrets: tuple[str, ...] = ()
    else:
        level_name = settings.log_level
        log_format = settings.log_format
        secrets = (
            settings.database_url.get_secret_value(),
            settings.secret_encryption_key.get_secret_value()
            if settings.secret_encryption_key is not None
            else "",
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else "",
        )

    formatter: logging.Formatter
    if log_format == "json":
        formatter = _JsonFormatter(secrets)
    else:
        formatter = _TextFormatter(secrets)

    handler = logging.StreamHandler()
    handler.addFilter(_ProcessFilter(process_type))
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level_name)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        server_logger = logging.getLogger(logger_name)
        server_logger.handlers.clear()
        server_logger.propagate = True
