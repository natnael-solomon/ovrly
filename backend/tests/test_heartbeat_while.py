"""``_heartbeat_while`` without a database: renewals, stop conditions and #130's stall."""

import asyncio

import pytest

from services.jobs.handlers import CancellationRequested
from services.pipeline.media_validation import _heartbeat_while


class PooledContext:
    """Heartbeats check a connection out like SQLAlchemy's pool once it is at size.

    ``asyncio.wait_for`` on Python 3.11 returns the result instead of raising when a
    cancellation arrives as the inner get completes, so a cancelled heartbeat can go on.
    """

    def __init__(self, lease_seconds=0.3, cancel_after=None):
        self.lease_seconds = lease_seconds
        self.cancel_after = cancel_after
        self.pool: asyncio.Queue[object] = asyncio.Queue()
        self.pool.put_nowait(object())
        self.beats = 0

    async def heartbeat(self):
        connection = await asyncio.wait_for(self.pool.get(), 5)
        await asyncio.sleep(0)
        self.pool.put_nowait(connection)
        self.beats += 1
        if self.cancel_after is not None and self.beats > self.cancel_after:
            raise CancellationRequested("cancel requested")


@pytest.mark.parametrize("ticks", range(6))
async def test_work_failing_during_a_renewal_ends_the_wait_and_its_renewals(ticks):
    # The failing 408 speech case: the provider answered while the first renewal was
    # checking out a connection. The old side task kept renewing the lease forever.
    context = PooledContext()

    async def provider_error():
        for _ in range(ticks):
            await asyncio.sleep(0)
        raise ValueError("provider error")

    async with asyncio.timeout(2):
        with pytest.raises(ValueError, match="provider error"):
            await _heartbeat_while(context, provider_error())
    beats = context.beats
    await asyncio.sleep(context.lease_seconds)
    assert context.beats == beats <= 2, "No renewal may outlive the work"


async def test_long_work_keeps_renewing_and_checks_once_more_at_the_end():
    context = PooledContext(lease_seconds=0.3)

    async def work():
        await asyncio.sleep(0.35)
        return "done"

    assert await _heartbeat_while(context, work()) == "done"
    # One renewal at the start, one per 0.1 s third of the lease, one after the result.
    assert 4 <= context.beats <= 6


async def test_cancellation_request_stops_the_work():
    context = PooledContext(lease_seconds=0.3, cancel_after=1)
    stopped = asyncio.Event()

    async def work():
        try:
            await asyncio.sleep(10)
        finally:
            stopped.set()

    async with asyncio.timeout(2):
        with pytest.raises(CancellationRequested):
            await _heartbeat_while(context, work())
    assert stopped.is_set()
