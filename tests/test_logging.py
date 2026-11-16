import io
import json
import logging
from contextlib import redirect_stderr

from ai_seller_core.core.config import Settings
from ai_seller_core.core.logging import configure_logging


def _settings(**overrides: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:db-secret@localhost/database",
        qdrant_api_key="qdrant-secret",
        **overrides,
    )


def _restore_logging(
    root_logger: logging.Logger,
    root_handlers: list[logging.Handler],
    root_level: int,
    named_logger_state: dict[str, tuple[list[logging.Handler], bool, int]],
) -> None:
    root_logger.handlers[:] = root_handlers
    root_logger.setLevel(root_level)
    for name, (handlers, propagate, level) in named_logger_state.items():
        logger = logging.getLogger(name)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.setLevel(level)


def test_logging_settings_are_shared_by_api_and_worker() -> None:
    root_logger = logging.getLogger()
    root_handlers = root_logger.handlers[:]
    root_level = root_logger.level
    named_loggers = ("uvicorn", "uvicorn.error", "uvicorn.access")
    named_logger_state = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in named_loggers
    }

    try:
        settings = _settings(log_level="warning", log_format="text")
        assert settings.log_level == "WARNING"
        assert settings.log_format == "text"

        for process in ("api", "worker"):
            output = io.StringIO()
            with redirect_stderr(output):
                configure_logging(process, settings)
                logging.getLogger("logging-test").info("filtered")
                logging.getLogger("logging-test").warning("visible")

            lines = output.getvalue().splitlines()
            assert len(lines) == 1
            assert "visible" in lines[0]
            assert process in lines[0]
            assert "filtered" not in lines[0]
    finally:
        _restore_logging(root_logger, root_handlers, root_level, named_logger_state)


def test_json_logging_redacts_secrets_and_omits_unapproved_extras() -> None:
    root_logger = logging.getLogger()
    root_handlers = root_logger.handlers[:]
    root_level = root_logger.level
    named_loggers = ("uvicorn", "uvicorn.error", "uvicorn.access")
    named_logger_state = {
        name: (
            logging.getLogger(name).handlers[:],
            logging.getLogger(name).propagate,
            logging.getLogger(name).level,
        )
        for name in named_loggers
    }

    try:
        output = io.StringIO()
        with redirect_stderr(output):
            configure_logging("worker", _settings())
            try:
                raise RuntimeError("provider rejected token=qdrant-secret")
            except RuntimeError:
                logging.getLogger("logging-test").exception(
                    "Background job failed",
                    extra={
                        "job_id": "job-123",
                        "queue": "inbound_dialog",
                        "kind": "process_event",
                        "payload": "private-payload",
                    },
                )

        rendered = output.getvalue()
        record = json.loads(rendered)
        assert record["process"] == "worker"
        assert record["job_id"] == "job-123"
        assert record["queue"] == "inbound_dialog"
        assert record["kind"] == "process_event"
        assert "qdrant-secret" not in rendered
        assert "private-payload" not in rendered
    finally:
        _restore_logging(root_logger, root_handlers, root_level, named_logger_state)
