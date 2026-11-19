import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.core.config import Settings, get_settings
from ai_seller_core.jobs.repository import claim_job
from ai_seller_core.jobs.retry import retry_delay_seconds
from ai_seller_core.persistence.base import SessionFactory
from ai_seller_core.persistence.models import BackgroundJob
from ai_seller_core.persistence.retention import purge_expired_activity

logger = logging.getLogger(__name__)
JobHandler = Callable[[dict[str, Any]], Awaitable[None]]
RETENTION_CLEANUP_INTERVAL_SECONDS = timedelta(days=1).total_seconds()
RETENTION_RETRY_INTERVAL_SECONDS = 300.0


class Worker:
    def __init__(
        self,
        *,
        queue: str,
        handlers: dict[str, JobHandler],
        worker_id: str | None = None,
        settings: Settings | None = None,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self.queue = queue
        self.handlers = dict(handlers)
        self.worker_id = worker_id or f"{socket.gethostname()}-{queue}"
        self.settings = settings or get_settings()
        self.session_factory = session_factory or SessionFactory

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "Worker started",
            extra={"queue": self.queue, "worker_id": self.worker_id},
        )
        try:
            if not self.handlers:
                logger.warning(
                    "Worker has no registered job handlers and will remain idle",
                    extra={"queue": self.queue, "worker_id": self.worker_id},
                )

            loop = asyncio.get_running_loop()
            next_retention_cleanup = loop.time()
            while not stop_event.is_set():
                if loop.time() >= next_retention_cleanup:
                    try:
                        events_deleted, audit_entries_deleted = (
                            await self._run_retention_cleanup()
                        )
                    except SQLAlchemyError:
                        logger.exception(
                            "Activity retention cleanup failed",
                            extra={"queue": self.queue, "worker_id": self.worker_id},
                        )
                        next_retention_cleanup = (
                            loop.time() + RETENTION_RETRY_INTERVAL_SECONDS
                        )
                    else:
                        logger.info(
                            "Activity retention cleanup completed",
                            extra={
                                "queue": self.queue,
                                "worker_id": self.worker_id,
                                "domain_events_deleted": events_deleted,
                                "audit_entries_deleted": audit_entries_deleted,
                            },
                        )
                        next_retention_cleanup = (
                            loop.time() + RETENTION_CLEANUP_INTERVAL_SECONDS
                        )

                if not self.handlers:
                    timeout = min(
                        self.settings.job_poll_interval_seconds,
                        max(0.0, next_retention_cleanup - loop.time()),
                    )
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
                    except TimeoutError:
                        continue
                    break

                job = await self._claim()
                if job is None:
                    timeout = min(
                        self.settings.job_poll_interval_seconds,
                        max(0.0, next_retention_cleanup - loop.time()),
                    )
                    try:
                        await asyncio.wait_for(
                            stop_event.wait(),
                            timeout=timeout,
                        )
                    except TimeoutError:
                        continue
                    break
                await self._execute(job)
        finally:
            logger.info(
                "Worker stopped",
                extra={"queue": self.queue, "worker_id": self.worker_id},
            )

    async def _run_retention_cleanup(self) -> tuple[int, int]:
        async with self.session_factory() as session, session.begin():
            return await purge_expired_activity(session)

    async def _claim(self) -> BackgroundJob | None:
        async with self.session_factory() as session, session.begin():
            return await claim_job(
                session,
                queue=self.queue,
                worker_id=self.worker_id,
                lease_seconds=self.settings.job_lease_seconds,
            )

    async def _execute(self, job: BackgroundJob) -> None:
        handler = self.handlers.get(job.kind)
        try:
            if job.payload.get("project_id") != job.project_id:
                raise ValueError("Job payload project_id does not match the job project_id")
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
                extra={
                    "job_id": job.id,
                    "queue": job.queue,
                    "kind": job.kind,
                    "correlation_id": job.correlation_id,
                },
            )
            await self._fail(job.id, str(error))
        else:
            await self._complete(job.id)

    async def _renew_lease(self, job_id: str) -> bool:
        lease_until = datetime.now(UTC) + timedelta(seconds=self.settings.job_lease_seconds)
        async with self.session_factory() as session, session.begin():
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
        async with self.session_factory() as session, session.begin():
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
        async with self.session_factory() as session, session.begin():
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
