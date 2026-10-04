"""Stage handler protocol and the per-job context handed to handlers."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from services.jobs.queue import ClaimedJob, JobQueue, Lease


class CancellationRequested(Exception):
    """Raised by :meth:`JobContext.heartbeat` once a cancellation request is observed."""


class JobContext:
    def __init__(self, queue: JobQueue, lease: Lease, lease_seconds: float):
        self.queue = queue
        self.lease = lease
        self.lease_seconds = lease_seconds

    async def heartbeat(self) -> None:
        """Extend the lease; raise if it was lost or cancellation was requested."""
        if await self.queue.heartbeat(self.lease, self.lease_seconds):
            raise CancellationRequested(f"Cancellation requested for job {self.lease.job_id}")


JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[dict[str, Any]]]


def default_handlers() -> Mapping[str, JobHandler]:
    """Pipeline stages are introduced by later tasks; the worker claims no stages yet."""
    return {}
