"""Idempotency: a duplicate provider callback with the same request id publishes once.

Chunk ``(session, seq)`` and investigation POST idempotency belong to the intake
endpoints and are tested with those issues; no stub endpoints are added here.
"""

import asyncio

from services.jobs.states import JobState


class CallbackRouter:
    """Routes provider callbacks to the waiting stage by request id.

    A callback for a request that already finished is answered from the published result
    (or the tombstone) and never re-enters the stage.
    """

    def __init__(self, harness):
        self.harness = harness
        self.pending = {}
        self.outcomes = []

    def expect(self, request_id):
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        return future

    async def deliver(self, request_id, result):
        job = await self.harness.queue.find_by_request_id(request_id)
        if job is None:
            self.outcomes.append((request_id, "unknown"))
            return None
        if job.status.state is JobState.PUBLISHED:
            (published,) = await self.harness.queue.published(job.key)
            self.outcomes.append((request_id, "already_published"))
            return published.result
        if job.status.state is JobState.DELETED:
            self.outcomes.append((request_id, "deleted"))
            return None
        future = self.pending.get(request_id)
        if future is None or future.done():
            self.outcomes.append((request_id, "duplicate_in_flight"))
            return None
        future.set_result(result)
        self.outcomes.append((request_id, "delivered"))
        return result


async def test_duplicate_provider_callback_publishes_once(harness):
    router = CallbackRouter(harness)
    waiting = asyncio.Event()

    async def callback_stage(job, context):
        harness.calls.append(job.attempts)
        request_id = f"req-{job.id.hex[:8]}"
        await context.record_request_id(request_id)
        future = router.expect(request_id)
        await harness.provider.call(request_id, job.payload)
        waiting.set()
        return await future

    worker = harness.worker(callback_stage, worker_id="callbacks", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue({"input": "clip"})
    await waiting.wait()
    request_id = f"req-{job_id.hex[:8]}"
    payload = {"request_id": request_id, "claims": ["one"]}

    first, second = await asyncio.gather(
        router.deliver(request_id, payload), router.deliver(request_id, dict(payload))
    )
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    # Exactly one callback reaches the stage. Depending on scheduling the duplicate is
    # either dropped while the first is in flight (None) or answered from the publication.
    outcomes = [outcome for _, outcome in router.outcomes]
    assert outcomes.count("delivered") == 1
    assert sorted(outcomes) in (
        ["delivered", "duplicate_in_flight"],
        ["already_published", "delivered"],
    ), outcomes
    assert payload in (first, second)
    duplicate = second if first == payload else first
    assert duplicate is None or duplicate == payload, "No second result reached the stage"
    late = await router.deliver(request_id, {"request_id": request_id, "claims": ["other"]})
    assert late == payload, "A late duplicate is answered from the published result"
    await worker.stop()

    assert harness.calls == [1], "The stage ran once"
    assert len(harness.provider.calls) == 1
    assert router.outcomes[-1] == (request_id, "already_published")
    assert record.provider_request_id == request_id
    result = await harness.assert_invariants(key, job_id, worker_ids=["callbacks"])
    assert result.result == payload


async def test_callback_for_a_deleted_job_is_dropped(harness):
    router = CallbackRouter(harness)
    waiting = asyncio.Event()

    async def callback_stage(job, context):
        request_id = f"req-{job.id.hex[:8]}"
        await context.record_request_id(request_id)
        waiting.set()
        return await router.expect(request_id)

    worker = harness.worker(callback_stage, worker_id="dropped-callbacks", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    await waiting.wait()
    request_id = f"req-{job_id.hex[:8]}"
    assert await harness.queue.delete(job_id)
    assert await router.deliver(request_id, {"resurrected": True}) is None
    assert router.outcomes == [(request_id, "deleted")]
    assert await router.deliver("req-never-seen", {}) is None
    router.pending[request_id].set_result({"late": True})
    await harness.wait_until(lambda: not worker.owned_leases)
    await worker.stop()
    assert worker.running is False
    await harness.assert_deleted(key, job_id)
