import asyncio

import pytest

from services.jobs.queue import LeaseLost
from services.pipeline.provider_recovery import wait_for_provider


class Context:
    def __init__(self, lease_seconds, latency, fail_after=None):
        self.lease_seconds = lease_seconds
        self.latency = latency
        self.fail_after = fail_after
        self.starts = []

    async def heartbeat(self):
        self.starts.append(asyncio.get_running_loop().time())
        if self.fail_after is not None and len(self.starts) > self.fail_after:
            raise LeaseLost("lease lost")
        await asyncio.sleep(self.latency)


async def test_renewal_starts_with_the_call_and_latency_does_not_stretch_the_cadence():
    # Interval is 0.2 s; each renewal takes 0.15 s. Waiting a full interval after every
    # renewal finished would space renewal starts 0.35 s apart.
    context = Context(lease_seconds=0.6, latency=0.15)
    loop = asyncio.get_running_loop()
    began = loop.time()

    async def provider():
        await asyncio.sleep(1.0)
        return "done"

    assert await wait_for_provider(provider(), context) == "done"
    assert context.starts[0] - began < 0.1
    gaps = [
        later - earlier
        for earlier, later in zip(context.starts, context.starts[1:], strict=False)
    ]
    assert len(gaps) >= 3
    assert max(gaps) < 0.3


async def test_lost_renewal_cancels_the_pending_call():
    context = Context(lease_seconds=0.3, latency=0, fail_after=1)
    cancelled = asyncio.Event()

    async def provider():
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(LeaseLost):
        await wait_for_provider(provider(), context)
    assert cancelled.is_set()
    assert len(context.starts) == 2
