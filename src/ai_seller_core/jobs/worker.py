import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from ai_seller_core.core.config import get_settings
from ai_seller_core.jobs.repository import claim_job
from ai_seller_core.jobs.retry import retry_delay_seconds
from ai_seller_core.persistence.base import SessionFactory
from ai_seller_core.persistence.models import BackgroundJob

logger = logging.getLogger(__name__)
JobHandler = Callable[[dict[str, Any]], Awaitable[None]]


class Worker:
    def __init__(
        self,
        *,
        queue: str,
        handlers: dict[str, JobHandler],
        worker_id: str | None = None,
    ) -> None:
        self.queue = queue
        self.handlers = handlers
        self.worker_id = worker_id or f"{socket.gethostname()}-{queue}"
        self.settings = get_settings()

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            job = await self._claim()
            if job is None:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.settings.job_poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
                break
            await self._execute(job)

    async def _claim(self) -> BackgroundJob | None:
        async with SessionFactory() as session, session.begin():
            return await claim_job(
                session,
                queue=self.queue,
                worker_id=self.worker_id,
                lease_seconds=self.settings.job_lease_seconds,
            )

    async def _execute(self, job: BackgroundJob) -> None:
        handler = self.handlers.get(job.kind)
        try:
            if handler is None:
                raise LookupError(f"No handler registered for job kind {job.kind!r}")
            task = asyncio.create_task(handler(job.payload))
            heartbeat_interval = max(1.0, self.settings.job_lease_seconds / 3)
            while not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat_interval)
                except TimeoutError:
                    if not await self._renew_lease(job.id):
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        raise RuntimeError(f"Worker lost lease for job {job.id}") from None
            await task
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception(
                "Background job failed",
                extra={"job_id": job.id, "queue": job.queue, "kind": job.kind},
            )
            await self._fail(job.id, str(error))
        else:
            await self._complete(job.id)

    async def _renew_lease(self, job_id: str) -> bool:
        lease_until = datetime.now(UTC) + timedelta(seconds=self.settings.job_lease_seconds)
        async with SessionFactory() as session, session.begin():
            result = await session.execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.lease_owner == self.worker_id,
                    BackgroundJob.status == "processing",
                )
                .values(lease_until=lease_until)
            )
            return result.rowcount == 1

    async def _complete(self, job_id: str) -> None:
        async with SessionFactory() as session, session.begin():
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.lease_owner == self.worker_id,
                    BackgroundJob.status == "processing",
                )
                .with_for_update()
            )
            if job is not None:
                job.status = "completed"
                job.lease_owner = None
                job.lease_until = None

    async def _fail(self, job_id: str, error_message: str) -> None:
        now = datetime.now(UTC)
        async with SessionFactory() as session, session.begin():
            job = await session.scalar(
                select(BackgroundJob)
                .where(
                    BackgroundJob.id == job_id,
                    BackgroundJob.lease_owner == self.worker_id,
                    BackgroundJob.status == "processing",
                )
                .with_for_update()
            )
            if job is None:
                logger.warning(
                    "Job lease lost before failure could be recorded",
                    extra={"job_id": job_id},
                )
                return

            job.last_error = error_message[:2000]
            job.lease_owner = None
            job.lease_until = None
            if job.attempts >= job.max_attempts:
                job.status = "dead_letter"
            else:
                job.status = "retrying"
                job.available_at = now + timedelta(seconds=retry_delay_seconds(job.attempts))
