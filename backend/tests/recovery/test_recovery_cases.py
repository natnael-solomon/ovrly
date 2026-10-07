"""RFC section 11 recovery cases: executable, against real PostgreSQL, in both worker modes."""

import asyncio
import logging
import os
import signal
import sys

import httpx
import pytest
from sqlalchemy import text

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
    monkeypatch.setattr(
        standalone, "default_handlers", lambda store, **kwargs: {harness.stage: handler}
    )
    serving = asyncio.create_task(standalone.serve())
    try:
        key, job_id = await harness.enqueue()
        await asyncio.wait_for(entered.wait(), 5)
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


async def test_worker_killed_after_provider_call_reconciles_by_request_id(harness, caplog):
    """Case: kill after provider call. The request id was recorded before the call, so the
    re-leased attempt reconciles the accepted result instead of calling the provider again."""
    faults = ScriptedFaults(crash_at=Checkpoint.AFTER_PROVIDER_CALL, attempts=(1,))
    app = harness.app(harness.provider_handler(), faults=faults)
    with pytest.raises(SimulatedCrash):
        async with app.router.lifespan_context(app):
            crashed = app.state.worker
            key, job_id = await harness.enqueue({"input": "clip"})
            with pytest.raises(SimulatedCrash):
                await crashed.wait()
            record = await harness.queue.get(job_id)
            assert record.status.state is JobState.RUNNING
            assert record.provider_request_id is not None, "Recorded before the provider call"
            assert harness.provider.calls == [record.provider_request_id]
            assert await harness.queue.published(key) == []
    request_id = record.provider_request_id

    recovering = harness.worker(harness.provider_handler(), worker_id="reconciling")
    await recovering.start()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await recovering.stop()

    assert harness.provider.calls == [request_id], "The provider was called exactly once"
    assert harness.provider.lookups == [request_id], "Attempt two reconciled by request id"
    assert [attempt for attempt, _ in harness.calls] == [1, 2]
    assert record.attempts == 2
    assert record.provider_request_id == request_id
    result = await harness.assert_invariants(
        key, job_id, worker_ids=[crashed.worker_id, "reconciling"]
    )
    assert result.result["request_id"] == request_id
    assert faults.hits == [(Checkpoint.CLAIMED, 1), (Checkpoint.AFTER_PROVIDER_CALL, 1)]


async def test_worker_killed_after_artifact_store_writes_the_artifact_idempotently(harness):
    """Case: kill after artifact store. The artifact write is keyed by the stage key, so the
    re-leased attempt re-writes the same artifact and exactly one publication follows."""
    faults = ScriptedFaults(crash_at=Checkpoint.AFTER_ARTIFACT_STORE, attempts=(1,))
    crashing = harness.worker(
        harness.provider_handler(store_artifact=True), faults=faults, worker_id="artifact-crash"
    )
    await crashing.start()
    key, job_id = await harness.enqueue({"input": "frame"})
    with pytest.raises(SimulatedCrash):
        await crashing.wait()
    assert not crashing.running
    assert harness.artifacts.writes == [key]
    assert (await harness.queue.get(job_id)).status.state is JobState.RUNNING

    recovering = harness.worker(
        harness.provider_handler(store_artifact=True), worker_id="artifact-recover"
    )
    await recovering.start()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await recovering.stop()

    assert harness.artifacts.writes == [key, key], "The write was repeated under the same key"
    assert len(harness.artifacts.artifacts) == 1, "Exactly one artifact exists"
    assert len(harness.provider.calls) == 1
    assert record.attempts == 2
    result = await harness.assert_invariants(
        key, job_id, worker_ids=["artifact-crash", "artifact-recover"]
    )
    assert result.result["artifact"] == f"artifact://{key.stage}/{key.input_hash}"
    assert result.result["request_id"] == harness.provider.calls[0]


async def test_cancel_mid_retrieval_stops_fetching_and_publishes_nothing(harness):
    """Case: cancel during retrieval. A chunked retrieval heartbeats per chunk; the request
    arrives after two chunks, the next heartbeat observes it and no further chunk is fetched."""
    fetched = []
    two_fetched = asyncio.Event()
    proceed = asyncio.Event()

    async def retrieval(job, context):
        for seq in range(6):
            if seq == 2:
                two_fetched.set()
                await proceed.wait()
            await context.heartbeat()
            fetched.append(seq)
        return {"chunks": fetched}

    worker = harness.worker(retrieval, worker_id="retrieval", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    await two_fetched.wait()
    assert fetched == [0, 1]
    assert await harness.queue.request_cancel(job_id) == "requested"
    proceed.set()
    record = await harness.wait_for_state(job_id, JobState.CANCELLED)
    await harness.wait_until(lambda: not worker.owned_leases)
    await worker.stop()
    assert fetched == [0, 1], "No chunk was fetched after the cancellation request"
    assert record.status.cancel_requested
    assert record.generation == 1
    assert record.lease_owner is None
    assert await harness.queue.published(key) == []


async def test_delete_with_delayed_provider_callback_rejects_the_late_publish(harness, caplog):
    """Case: delete with a delayed callback. The stage awaits the provider's callback, the
    job is deleted meanwhile, and the late result can neither publish nor resurrect content."""
    waiting = asyncio.Event()
    callback = asyncio.get_running_loop().create_future()

    async def awaiting_callback(job, context):
        request_id = f"req-{job.id.hex[:8]}"
        await context.record_request_id(request_id)
        await harness.provider.call(request_id, job.payload)
        waiting.set()
        return await callback

    worker = harness.worker(awaiting_callback, worker_id="callback", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue({"input": "to-delete"})
    await waiting.wait()
    (request_id,) = harness.provider.calls
    assert await harness.queue.delete(job_id)
    late = await harness.queue.find_by_request_id(request_id)
    assert late.status.state is JobState.DELETED, "A callback router sees the tombstone"
    callback.set_result({"resurrected": True, "request_id": request_id})
    await harness.wait_until(lambda: not worker.owned_leases)
    await worker.stop()
    assert worker.running is False
    assert "not published: the job was deleted (generation changed)" in caplog.text
    record = await harness.assert_deleted(key, job_id)
    assert record.generation == 1
    assert record.provider_request_id == request_id, "The tombstone keeps the id for routing"


async def test_database_connection_drop_mid_stage_re_leases_without_failing(harness, caplog):
    """Case: database connection drop mid-stage. The stage's own connection is terminated
    server-side; the worker survives, hands the lease back and the job is re-leased."""
    caplog.set_level(logging.INFO)
    stage_database = harness.database()
    backend_pid = asyncio.get_running_loop().create_future()

    async def uses_the_database(job, context):
        harness.calls.append((job.attempts, job.lease.worker_id))
        if job.attempts == 1:
            async with stage_database.engine.connect() as connection:
                backend_pid.set_result(await connection.scalar(text("SELECT pg_backend_pid()")))
                await connection.execute(text("SELECT pg_sleep(10)"))
        return {"attempt": job.attempts}

    worker = harness.worker(uses_the_database, worker_id="dropped", job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    pid = await backend_pid
    async with harness.control.engine.connect() as connection:
        assert await connection.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    assert worker.running, "An infrastructure error must not stop the worker"
    await worker.stop()
    assert "infrastructure error in stage" in caplog.text
    assert "lease released after an error" in caplog.text
    assert "failed in stage" not in caplog.text
    assert [attempt for attempt, _ in harness.calls] == [1, 2]
    assert record.attempts == 2
    assert record.failure is None
    result = await harness.assert_invariants(key, job_id, worker_ids=["dropped"])
    assert result.result == {"attempt": 2}


async def test_database_drop_that_also_blocks_the_release_lets_the_lease_expire(
    harness, caplog, monkeypatch
):
    """Variant: the database is unreachable for the release too, so the lease expires and
    the job is re-leased once the database is back."""
    caplog.set_level(logging.INFO)

    async def flaky_database(job, context):
        harness.calls.append((job.attempts, job.lease.worker_id))
        if job.attempts == 1:
            raise OSError("connection reset by the database")
        return {"attempt": job.attempts}

    worker = harness.worker(flaky_database, worker_id="expiring", job_lease_seconds=0.3)
    original_release = worker.queue.release

    async def unreachable(lease):
        monkeypatch.setattr(worker.queue, "release", original_release)
        raise OSError("database unreachable")

    monkeypatch.setattr(worker.queue, "release", unreachable)
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    assert worker.running
    await worker.stop()
    assert "lease could not be released; it will expire" in caplog.text
    assert "failed in stage" not in caplog.text
    assert record.attempts == 2
    assert record.fencing_token == 2, "The job was re-leased after expiry, not failed"
    result = await harness.assert_invariants(key, job_id, worker_ids=["expiring"])
    assert result.result == {"attempt": 2}


async def test_api_restart_with_in_flight_request_completes_the_job_once(harness, caplog):
    """Case: API restart with in-flight requests. The lifespan exits while a request and a
    stage are in flight; the forced drain releases the lease, the lifespan re-enters on the
    same app and the job completes exactly once."""
    caplog.set_level(logging.INFO)
    entered = asyncio.Event()

    async def blocked_once(job, context):
        harness.calls.append((job.attempts, job.lease.worker_id))
        if job.attempts == 1:
            entered.set()
            await asyncio.Event().wait()
        return {"attempt": job.attempts}

    app = harness.app(blocked_once, job_lease_seconds=5, worker_shutdown_seconds=0.3)
    async with client(app) as http:
        with pytest.raises(RuntimeError, match="shutdown timed out"):
            async with app.router.lifespan_context(app):
                first = app.state.worker
                key, job_id = await harness.enqueue()
                await entered.wait()
                in_flight = asyncio.create_task(http.get("/healthz"))
                await asyncio.sleep(0)
        response = await in_flight
        assert response.status_code in (200, 503), "In-flight requests end with a response"
        record = await harness.queue.get(job_id)
        assert record.status.state is JobState.QUEUED, "The forced drain released the lease"
        assert record.lease_owner is None
        assert app.state.database.engine.pool.checkedout() == 0

        async with app.router.lifespan_context(app):
            second = app.state.worker
            assert second is not first
            record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
            assert (await http.get("/healthz")).status_code == 200
            result = await harness.assert_invariants(
                key, job_id, worker_ids=[first.worker_id, second.worker_id]
            )
    assert [attempt for attempt, _ in harness.calls] == [1, 2]
    assert record.attempts == 2
    assert record.fencing_token == 2
    assert result.result == {"attempt": 2}
    assert "lease released during shutdown" in caplog.text
