import asyncio
import logging
import os
import socket
from collections.abc import Mapping
from contextlib import suppress
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.jobs.faults import Checkpoint, FaultInjector, NoFaults
from services.jobs.handlers import CancellationRequested, JobContext, JobHandler
from services.jobs.queue import ClaimedJob, JobQueue, Lease, LeaseLost, PublishRejected

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        database: Database,
        shutdown_seconds: float,
        *,
        handlers: Mapping[str, JobHandler] | None = None,
        faults: FaultInjector | None = None,
        lease_seconds: float = 30,
        poll_seconds: float = 1,
        worker_id: str | None = None,
    ):
        self.database = database
        self.shutdown_seconds = shutdown_seconds
        self.handlers: Mapping[str, JobHandler] = dict(handlers or {})
        self.faults: FaultInjector = faults if faults is not None else NoFaults()
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self.queue = JobQueue(database)
        self._owned: dict[UUID, Lease] = {}
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._started.is_set() and self._task is not None and not self._task.done()

    @property
    def owned_leases(self) -> frozenset[UUID]:
        """Leases this worker currently holds in memory; empty once drained."""
        return frozenset(self._owned)

    async def run(self) -> None:
        try:
            await self.database.ping()
        except (SQLAlchemyError, OSError, TimeoutError):
            raise RuntimeError("Worker startup failed: database unavailable") from None
        self._started.set()
        stages = list(self.handlers)
        logger.info("Worker ready (%s, %d stage handlers)", self.worker_id, len(stages))
        while not self._stop.is_set():
            job = await self.queue.claim(self.worker_id, stages, self.lease_seconds)
            if job is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), self.poll_seconds)
                continue
            await self._execute(job)

    async def _execute(self, job: ClaimedJob) -> None:
        lease = job.lease
        self._owned[job.id] = lease
        try:
            await self.faults.checkpoint(Checkpoint.CLAIMED, job)
            if not await self.queue.start(lease):
                # Either cancellation was requested while leased or the lease is already lost.
                if await self.queue.cancel(lease):
                    logger.info("Job %s cancelled before it started", job.id)
                else:
                    logger.warning("Job %s lease lost before it started", job.id)
                return
            context = JobContext(self.queue, lease, self.lease_seconds)
            try:
                result = await self.handlers[job.key.stage](job, context)
            except CancellationRequested:
                await self.queue.cancel(lease)
                logger.info("Job %s cancelled during stage %s", job.id, job.key.stage)
                return
            except LeaseLost:
                logger.warning("Job %s lease lost during stage %s", job.id, job.key.stage)
                return
            except Exception as error:
                # Only the exception type is recorded; messages may contain private details.
                reason = type(error).__name__
                logger.error("Job %s failed in stage %s (%s)", job.id, job.key.stage, reason)
                await self.queue.fail(lease, reason)
                return
            await self.faults.checkpoint(Checkpoint.BEFORE_PUBLISH, job)
            try:
                await self.queue.publish(lease, result)
            except PublishRejected as rejected:
                logger.warning("Job %s not published: %s", job.id, rejected.reason)
            await self.faults.checkpoint(Checkpoint.AFTER_PUBLISH, job)
        except asyncio.CancelledError:
            # Forced shutdown: hand the lease back so the job is neither lost nor duplicated.
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._release(lease)), self.shutdown_seconds)
            raise
        finally:
            self._owned.pop(job.id, None)

    async def _release(self, lease: Lease) -> None:
        try:
            if await self.queue.release(lease):
                logger.info("Job %s lease released during shutdown", lease.job_id)
        except (SQLAlchemyError, OSError, TimeoutError):
            logger.error("Job %s lease could not be released; it will expire", lease.job_id)

    def _finished(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("Worker failed; readiness is unavailable")
        elif not self._stop.is_set():
            logger.error("Worker stopped unexpectedly; readiness is unavailable")

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Worker instances can only be started once")
        self._task = asyncio.create_task(self.run(), name="ovrly-worker")
        self._task.add_done_callback(self._finished)
        ready = asyncio.create_task(self._started.wait())
        try:
            await asyncio.wait({ready, self._task}, return_when=asyncio.FIRST_COMPLETED)
            if self._task.done():
                await self._task
                raise RuntimeError("Worker stopped before startup completed")
        finally:
            ready.cancel()
            with suppress(asyncio.CancelledError):
                await ready

    def request_stop(self) -> None:
        self._stop.set()

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("Worker has not started")
        await self._task

    async def stop(self) -> None:
        """Stop claiming, finish or release the in-flight job, then return."""
        if self._task is None:
            return
        self.request_stop()
        if self._task.cancelled():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=self.shutdown_seconds)
        except TimeoutError:
            logger.error("Worker shutdown timed out; cancelling the owned task")
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            raise RuntimeError("Worker shutdown timed out") from None
