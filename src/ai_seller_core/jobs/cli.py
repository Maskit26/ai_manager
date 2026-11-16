import argparse
import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

from ai_seller_core.core.config import get_settings
from ai_seller_core.jobs.worker import JobHandler, Worker
from ai_seller_core.persistence.base import database_lifespan, get_session_factory


def build_handlers() -> dict[str, JobHandler]:
    # No concrete inbox/outbox adapters are available to register yet.
    return {}


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


async def _run_worker(*, queue: str, worker_id: str | None) -> None:
    settings = get_settings()
    stop_event = asyncio.Event()

    with _shutdown_signals(stop_event):
        async with database_lifespan(settings):
            worker = Worker(
                queue=queue,
                handlers=build_handlers(),
                worker_id=worker_id,
                settings=settings,
                session_factory=get_session_factory(),
            )
            await worker.run(stop_event)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Seller background worker.")
    parser.add_argument("--queue", default="inbound_dialog")
    parser.add_argument("--worker-id")
    args = parser.parse_args()
    asyncio.run(_run_worker(queue=args.queue, worker_id=args.worker_id))
