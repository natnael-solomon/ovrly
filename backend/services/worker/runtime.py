import asyncio
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.jobs.faults import Checkpoint, FaultInjector, NoFaults
from services.jobs.handlers import CancellationRequested, JobContext, JobHandler
from services.jobs.queue import ClaimedJob, JobQueue, Lease, LeaseLost, PublishRejected
from services.jobs.retries import RetryableError, RetryPolicy, UnknownOutcome
from services.logging import configure_logging
from services.pipeline.publish import publish_stage

logger = logging.getLogger(__name__)

# Infrastructure failures leave the stage outcome unknown; the job is never marked failed.
INFRASTRUCTURE_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


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
        retry_policy: RetryPolicy | None = None,
        worker_id: str | None = None,
        maintenance: Callable[[], Awaitable[None]] | None = None,
        idle_poll_max_seconds: float | None = None,
    ):
        configure_logging()
        self.database = database
        self.shutdown_seconds = shutdown_seconds
        self.handlers: Mapping[str, JobHandler] = dict(handlers or {})
        self.faults: FaultInjector = faults if faults is not None else NoFaults()
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        # Idle waits double from poll_seconds up to this cap and reset after a claim.
        self.idle_poll_max_seconds = max(poll_seconds, idle_poll_max_seconds or poll_seconds)
        self.retry_policy = retry_policy if retry_policy is not None else RetryPolicy()
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self.queue = JobQueue(database)
        self.maintenance = maintenance
        self._owned: dict[UUID, Lease] = {}
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._beat: float | None = None

    @property
    def running(self) -> bool:
        return self._started.is_set() and self._task is not None and not self._task.done()

    @property
    def heartbeat_age(self) -> float | None:
        """Seconds since the loop last polled or an in-flight job extended its lease."""
        return None if self._beat is None else time.monotonic() - self._beat

    def _touch(self) -> None:
        self._beat = time.monotonic()

    @property
    def owned_leases(self) -> frozenset[UUID]:
        """Leases this worker currently holds in memory; empty once drained."""
        return frozenset(self._owned)

    async def run(self) -> None:
        try:
            await self.database.ping()
        except (SQLAlchemyError, OSError, TimeoutError):
            raise RuntimeError("Worker startup failed: database unavailable") from None
        self._touch()
        self._started.set()
        stages = list(self.handlers)
        logger.info("Worker ready (%s, %d stage handlers)", self.worker_id, len(stages))
        delay = self.poll_seconds
        while not self._stop.is_set():
            self._touch()
            if "reconciliation" in self.handlers:
                from services.pipeline.reconciliation import schedule_reconciliation

                await schedule_reconciliation(self.queue)
            if self.maintenance is not None:
                await self.maintenance()
            job = await self.queue.claim(self.worker_id, stages, self.lease_seconds)
            if job is None:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), delay)
                delay = min(delay * 2, self.idle_poll_max_seconds)
                continue
            delay = self.poll_seconds
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
            context = JobContext(
                self.queue, lease, self.lease_seconds, self.faults, on_heartbeat=self._touch
            )
            try:
                result = await self.handlers[job.key.stage](job, context)
            except CancellationRequested:
                await self.queue.cancel(lease)
                logger.info("Job %s cancelled during stage %s", job.id, job.key.stage)
                return
            except LeaseLost:
                logger.warning("Job %s lease lost during stage %s", job.id, job.key.stage)
                return
            except RetryableError as outcome:
                await self._retry(job, outcome)
                return
            except INFRASTRUCTURE_ERRORS:
                # The stage outcome is unknown; hand the lease back or let it expire so the
                # job is re-leased. The worker itself keeps running.
                logger.warning("Job %s infrastructure error in stage %s", job.id, job.key.stage)
                await self._release(lease)
                return
            except Exception as error:
                # Only the exception type is recorded; messages may contain private details.
                reason = type(error).__name__
                logger.error("Job %s failed in stage %s (%s)", job.id, job.key.stage, reason)
                await self.queue.fail(lease, reason)
                return
            await self.faults.checkpoint(Checkpoint.BEFORE_PUBLISH, job)
            try:
                published = await publish_stage(
                    self.queue, job, result, successors=context.successors
                )
                if published is None:
                    await self.queue.cancel(lease)
                    logger.info("Job %s cancelled during stage %s", job.id, job.key.stage)
            except PublishRejected as rejected:
                logger.warning("Job %s not published: %s", job.id, rejected.reason)
            except INFRASTRUCTURE_ERRORS:
                logger.warning("Job %s infrastructure error in stage %s", job.id, job.key.stage)
                await self._release(lease)
            await self.faults.checkpoint(Checkpoint.AFTER_PUBLISH, job)
        except asyncio.CancelledError:
            # Forced shutdown: hand the lease back so the job is neither lost nor duplicated.
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(self._release(lease, reason="during shutdown")),
                    self.shutdown_seconds,
                )
            raise
        finally:
            self._owned.pop(job.id, None)

    async def _retry(self, job: ClaimedJob, outcome: RetryableError) -> None:
        """Schedule the next attempt per the retry class, or fail with the class recorded."""
        recorded = job.provider_request_id is not None
        if not recorded and isinstance(outcome, UnknownOutcome):
            # The handler records the id during this attempt; the claim predates it.
            record = await self.queue.get(job.id)
            recorded = record is not None and record.provider_request_id is not None
        decision = self.retry_policy.decide(outcome, job.retry_counts, request_id_recorded=recorded)
        retry_class = decision.retry_class
        if decision.delay_seconds is None:
            logger.error(
                "Job %s failed in stage %s (%s, retries exhausted after %d)",
                job.id,
                job.key.stage,
                retry_class.value,
                decision.count - 1,
            )
            await self.queue.fail(job.lease, type(outcome).__name__, retry_class)
            return
        if await self.queue.retry(job.lease, retry_class, decision.delay_seconds):
            logger.info(
                "Job %s retry %d (%s) scheduled in %.2fs",
                job.id,
                decision.count,
                retry_class.value,
                decision.delay_seconds,
            )
        else:
            logger.warning("Job %s lease lost before retry %s", job.id, retry_class.value)

    async def _release(self, lease: Lease, *, reason: str = "after an error") -> None:
        try:
            if await self.queue.release(lease):
                logger.info("Job %s lease released %s", lease.job_id, reason)
        except INFRASTRUCTURE_ERRORS:
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
