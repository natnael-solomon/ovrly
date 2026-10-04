"""Worker handling of failures, cancellation requests and lost leases."""

import asyncio
import logging

from recovery.harness import ScriptedFaults
from services.jobs.faults import Checkpoint
from services.jobs.queue import CancelOutcome
from services.jobs.states import JobState, JobStatus


async def test_handler_failure_marks_the_job_failed_and_the_worker_continues(harness, caplog):
    async def flaky(job, context):
        if job.payload.get("explode"):
            raise ValueError("private failure details")
        return {"ok": True}

    worker = harness.worker(flaky, worker_id="flaky")
    await worker.start()
    _, failing = await harness.enqueue({"explode": True})
    key, healthy = await harness.enqueue({"explode": False})
    failed = await harness.wait_for_state(failing, JobState.FAILED)
    await harness.wait_for_state(healthy, JobState.PUBLISHED)
    await worker.stop()
    assert failed.failure == "ValueError"
    assert failed.lease_owner is None
    assert "private failure details" not in caplog.text
    assert "failed in stage" in caplog.text
    await harness.assert_invariants(key, healthy, worker_ids=["flaky"])


async def test_cancellation_requested_during_the_stage_is_made_effective(harness):
    entered = asyncio.Event()
    proceed = asyncio.Event()
    published = []

    async def cooperative(job, context):
        entered.set()
        await proceed.wait()
        await context.heartbeat()
        published.append(job.id)
        return {"should": "never publish"}

    worker = harness.worker(cooperative, worker_id="cooperative", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    await entered.wait()
    assert await harness.queue.request_cancel(job_id) is CancelOutcome.REQUESTED
    assert (await harness.queue.get(job_id)).status == JobStatus(JobState.RUNNING, True)
    proceed.set()
    record = await harness.wait_for_state(job_id, JobState.CANCELLED)
    await worker.stop()
    assert published == []
    assert record.generation == 1
    assert record.lease_owner is None
    assert await harness.queue.published(key) == []
    assert worker.owned_leases == frozenset()


async def test_cancellation_requested_before_start_never_runs_the_handler(harness, caplog):
    caplog.set_level(logging.INFO)
    ran = []

    async def cancel_on_claim(name, job):
        if name is Checkpoint.CLAIMED:
            assert await harness.queue.request_cancel(job.id) is CancelOutcome.REQUESTED

    async def handler(job, context):
        ran.append(job.id)
        return {}

    faults = ScriptedFaults(on_checkpoint=cancel_on_claim)
    worker = harness.worker(handler, faults=faults, worker_id="pre-cancel")
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.CANCELLED)
    await worker.stop()
    assert ran == []
    assert record.status == JobStatus(JobState.CANCELLED, True)
    assert "cancelled before it started" in caplog.text
    assert await harness.queue.published(key) == []


async def test_lost_lease_is_detected_by_heartbeat(harness, caplog):
    entered = asyncio.Event()
    proceed = asyncio.Event()

    async def slow(job, context):
        if job.attempts == 1:
            entered.set()
            await proceed.wait()
            await context.heartbeat()
            raise AssertionError("heartbeat must raise once the lease is lost")
        return {"attempt": job.attempts}

    slow_worker = harness.worker(slow, worker_id="slow", job_lease_seconds=0.2)
    await slow_worker.start()
    key, job_id = await harness.enqueue()
    await entered.wait()
    fast_worker = harness.worker(slow, worker_id="fast-heartbeat", job_lease_seconds=5)
    await fast_worker.start()
    await harness.wait_for_state(job_id, JobState.PUBLISHED)
    proceed.set()
    await harness.wait_until(lambda: not slow_worker.owned_leases)
    await slow_worker.stop()
    await fast_worker.stop()
    assert "lease lost during stage" in caplog.text
    result = await harness.assert_invariants(key, job_id, worker_ids=["slow", "fast-heartbeat"])
    assert result.result == {"attempt": 2}


async def test_lease_lost_before_start_is_logged_without_cancelling(harness, caplog):
    async def steal_on_claim(name, job):
        if name is Checkpoint.CLAIMED and job.attempts == 1:
            await asyncio.sleep(0.3)

    async def handler(job, context):
        return {"attempt": job.attempts}

    slow = harness.worker(
        handler,
        faults=ScriptedFaults(on_checkpoint=steal_on_claim),
        worker_id="slow-start",
        job_lease_seconds=0.1,
    )
    await slow.start()
    key, job_id = await harness.enqueue()
    await asyncio.sleep(0.15)
    fast = harness.worker(handler, worker_id="fast-start", job_lease_seconds=5)
    await fast.start()
    await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await harness.wait_until(lambda: not slow.owned_leases)
    await slow.stop()
    await fast.stop()
    assert "lease lost before it started" in caplog.text
    result = await harness.assert_invariants(key, job_id, worker_ids=["slow-start", "fast-start"])
    assert result.result == {"attempt": 2}


async def test_worker_without_handlers_claims_nothing(harness):
    key, job_id = await harness.enqueue()
    worker = harness.worker(harness.recording_handler(), worker_id="idle")
    worker.handlers = {}
    await worker.start()
    await asyncio.sleep(0.2)
    await worker.stop()
    assert (await harness.queue.get(job_id)).status == JobStatus(JobState.QUEUED)
    assert harness.calls == []
