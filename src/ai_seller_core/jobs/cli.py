import argparse
import asyncio
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType
from uuid import uuid4

from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.core.logging import configure_logging
from ai_seller_core.jobs.worker import JobHandler, Worker
from ai_seller_core.persistence.base import database_lifespan, get_session_factory

logger = logging.getLogger(__name__)


def build_handlers(*, settings: Settings, worker_id: str) -> dict[str, JobHandler]:
    if settings.app_env not in {"local", "development", "test"}:
        return {}
    from ai_seller_core.jobs.development_handlers import build_development_handlers

    logger.warning("Using development dialog runtime and fake outbound delivery")
    return build_development_handlers(
        worker_id=worker_id,
        lease_seconds=settings.job_lease_seconds,
        session_factory=get_session_factory(),
    )


@contextmanager
def _shutdown_signals(stop_event: asyncio.Event) -> Iterator[None]:
    loop = asyncio.get_running_loop()
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}
    shutdown_signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        shutdown_signals.append(signal.SIGBREAK)

    def request_shutdown(_signum: int, _frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for signum in shutdown_signals:
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)

    try:
        yield
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


async def _run_worker(
    *,
    queue: str,
    worker_id: str | None,
    settings: Settings,
) -> None:
    stop_event = asyncio.Event()

    with _shutdown_signals(stop_event):
        async with database_lifespan(settings):
            effective_worker_id = worker_id or f"{queue}-{uuid4().hex}"
            worker = Worker(
                queue=queue,
                handlers=build_handlers(settings=settings, worker_id=effective_worker_id),
                worker_id=effective_worker_id,
                settings=settings,
                session_factory=get_session_factory(),
            )
            await worker.run(stop_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Seller background worker.")
    parser.add_argument("--queue", default="inbound_dialog")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    try:
        settings = get_settings()
    except Exception:
        configure_logging("worker")
        logger.exception("Worker configuration failed", extra={"queue": args.queue})
        raise

    configure_logging("worker", settings)
    try:
        asyncio.run(
            _run_worker(
                queue=args.queue,
                worker_id=args.worker_id,
                settings=settings,
            )
        )
    except Exception:
        logger.exception("Worker startup or runtime failed", extra={"queue": args.queue})
        raise
