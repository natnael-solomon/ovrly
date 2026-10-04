"""Stage handler protocol and the per-job context handed to handlers."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from services.jobs.faults import Checkpoint, FaultInjector, NoFaults
from services.jobs.queue import ClaimedJob, JobQueue, Lease


class CancellationRequested(Exception):
    """Raised by :meth:`JobContext.heartbeat` once a cancellation request is observed."""


class JobContext:
    def __init__(
        self,
        queue: JobQueue,
        lease: Lease,
        lease_seconds: float,
        faults: FaultInjector | None = None,
    ):
        self.queue = queue
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.faults: FaultInjector = faults if faults is not None else NoFaults()

    async def heartbeat(self) -> None:
        """Extend the lease; raise if it was lost or cancellation was requested."""
        if await self.queue.heartbeat(self.lease, self.lease_seconds):
            raise CancellationRequested(f"Cancellation requested for job {self.lease.job_id}")

    async def record_request_id(self, request_id: str) -> None:
        """Record the provider request id *before* calling the provider.

        An :class:`~services.jobs.retries.UnknownOutcome` is only retried when this was
        called; the next attempt sees the id on ``ClaimedJob.provider_request_id`` and
        reconciles instead of calling the provider again.
        """
        await self.queue.record_request_id(self.lease, request_id)

    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None:
        """Let the fault injector act at a stage-level checkpoint (tests only)."""
        await self.faults.checkpoint(name, job)


JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[dict[str, Any]]]


def default_handlers() -> Mapping[str, JobHandler]:
    """Pipeline stages are introduced by later tasks; the worker claims no stages yet."""
    return {}
