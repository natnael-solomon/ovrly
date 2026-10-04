"""RFC section 11 recovery cases: executable, against real PostgreSQL, in both worker modes."""

import asyncio
import logging
import os
import signal
import sys

import httpx
import pytest

import services.worker.__main__ as standalone
from recovery.harness import ScriptedFaults
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.states import JobState


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_worker_killed_before_state_commit_is_re_leased_and_completes_exactly_once(
    harness, caplog
):
    """Case: kill before state commit. The embedded API worker dies after the handler
    produced its result but before publication; a standalone worker re-leases the job."""
    faults = ScriptedFaults(crash_at=Checkpoint.BEFORE_PUBLISH, attempts=(1,))
    app = harness.app(harness.recording_handler(), faults=faults)
    with pytest.raises(SimulatedCrash):
        async with app.router.lifespan_context(app):
            crashed = app.state.worker
            key, job_id = await harness.enqueue()
            with pytest.raises(SimulatedCrash):
                await crashed.wait()
            async with client(app) as http:
                response = await http.get("/healthz")
            assert response.status_code == 503
            assert response.json()["reason"] == "worker"
            record = await harness.queue.get(job_id)
            assert record.status.state is JobState.RUNNING
            assert record.lease_owner == crashed.worker_id
            assert await harness.queue.published(key) == []
    assert "Worker failed" in caplog.text

    recovering = harness.worker(harness.recording_handler(), worker_id="recovering")
    await recovering.start()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await recovering.stop()

    assert [attempt for attempt, _ in harness.calls] == [1, 2], "At-least-once, exactly two runs"
    assert record.attempts == 2
    assert record.fencing_token == 2
    result = await harness.assert_invariants(
        key, job_id, worker_ids=[crashed.worker_id, "recovering"]
    )
    assert result.result == {"attempt": 2}
    assert faults.hits == [(Checkpoint.CLAIMED, 1), (Checkpoint.BEFORE_PUBLISH, 1)]


async def test_lease_expiry_while_worker_alive_rejects_the_stale_publish(harness, caplog):
    """Case: lease expiry with a live worker. The slow embedded worker stays alive, loses
    its lease to a standalone worker, and its late publish is rejected by the fencing token."""
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def slow_then_fast(job, context):
        harness.calls.append((job.attempts, job.lease.worker_id))
        if job.attempts == 1:
            entered.set()
            await gate.wait()
            return {"from": "stale"}
        return {"from": "fresh"}

    app = harness.app(slow_then_fast, job_lease_seconds=0.3)
    async with app.router.lifespan_context(app):
        slow = app.state.worker
        key, job_id = await harness.enqueue()
        await entered.wait()
        assert slow.owned_leases == {job_id}
        fast = harness.worker(slow_then_fast, worker_id="fast", job_lease_seconds=5)
        await fast.start()
        record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
        assert record.fencing_token == 2
        assert record.lease_owner is None
        gate.set()
        await harness.wait_until(lambda: not slow.owned_leases)
        await fast.stop()
        assert slow.running, "A rejected publish must not crash the live worker"
        async with client(app) as http:
            assert (await http.get("/healthz")).status_code == 200
        result = await harness.assert_invariants(key, job_id, worker_ids=[slow.worker_id, "fast"])
    assert result.result == {"from": "fresh"}
    assert result.fencing_token == 2
    assert sorted(harness.calls) == [(1, slow.worker_id), (2, "fast")]
    assert "not published: the lease is stale (fencing token changed)" in caplog.text


async def test_graceful_shutdown_drains_the_in_flight_lease_in_embedded_mode(harness):
    entered = asyncio.Event()
    gate = asyncio.Event()
    exit_requested = asyncio.Event()

    async def handler(job, context):
        entered.set()
        await gate.wait()
        await context.heartbeat()
        return {"drained": True}

    app = harness.app(handler, job_lease_seconds=5)

    async def serve_until_exit():
        async with app.router.lifespan_context(app):
            await exit_requested.wait()

    lifespan = asyncio.create_task(serve_until_exit())
    try:
        key, job_id = await harness.enqueue()
        await entered.wait()
        worker = app.state.worker
        exit_requested.set()
        await harness.wait_until(lambda: worker._stop.is_set())
        _, late_job = await harness.enqueue()
        await asyncio.sleep(0.2)
        assert not lifespan.done(), "Shutdown must wait for the owned lease"
        gate.set()
        async with asyncio.timeout(5):
            await lifespan
    finally:
        if not lifespan.done():
            lifespan.cancel()
    assert not worker.running
    await harness.assert_invariants(key, job_id, worker_ids=[worker.worker_id])
    late = await harness.queue.get(late_job)
    assert late.status.state is JobState.QUEUED, "No new job is claimed after stop is requested"
    assert app.state.database.engine.pool.checkedout() == 0


async def test_forced_shutdown_releases_the_lease_for_another_worker(harness, caplog):
    caplog.set_level(logging.INFO)
    entered = asyncio.Event()

    async def stuck_then_done(job, context):
        harness.calls.append((job.attempts, job.lease.worker_id))
        if job.attempts == 1:
            entered.set()
            await asyncio.Event().wait()
        return {"attempt": job.attempts}

    stuck = harness.worker(stuck_then_done, worker_id="stuck", worker_shutdown_seconds=0.2)
    await stuck.start()
    key, job_id = await harness.enqueue()
    await entered.wait()
    with pytest.raises(RuntimeError, match="shutdown timed out"):
        await stuck.stop()
    record = await harness.queue.get(job_id)
    assert record.status.state is JobState.QUEUED
    assert record.lease_owner is None
    assert await harness.queue.published(key) == []
    assert "lease released during shutdown" in caplog.text

    second = harness.worker(stuck_then_done, worker_id="second", job_lease_seconds=5)
    await second.start()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await second.stop()
    assert record.fencing_token == 2
    result = await harness.assert_invariants(key, job_id, worker_ids=["stuck", "second"])
    assert result.result == {"attempt": 2}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
async def test_standalone_worker_drains_its_lease_on_sigterm(harness, monkeypatch):
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def handler(job, context):
        entered.set()
        await gate.wait()
        return {"standalone": True}

    monkeypatch.setattr(standalone, "load_settings", lambda: harness.settings(job_lease_seconds=5))
    monkeypatch.setattr(standalone, "default_handlers", lambda: {harness.stage: handler})
    serving = asyncio.create_task(standalone.serve())
    try:
        key, job_id = await harness.enqueue()
        await entered.wait()
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.2)
        assert not serving.done(), "The standalone worker must finish its lease first"
        assert (await harness.queue.get(job_id)).status.state is JobState.RUNNING
        gate.set()
        async with asyncio.timeout(5):
            await serving
    finally:
        if not serving.done():
            serving.cancel()
    await harness.assert_invariants(key, job_id)
