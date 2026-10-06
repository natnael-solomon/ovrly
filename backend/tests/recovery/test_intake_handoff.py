"""Investigation hand-off to the job engine: atomic enqueue, idempotent replay, crash recovery."""

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from services.api.auth import hash_token
from services.api.intake import QueueDispatcher
from services.api.main import create_app
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.jobs.models import jobs
from services.jobs.states import JobState
from services.models import credentials, investigations
from services.pipeline.intake import INTAKE_STAGE, intake_stage_key

URL_BODY = {"source": {"kind": "url", "url": "https://example.com/watch?v=handoff"}}


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


async def guest(http):
    response = await http.post("/v1/principals/guest")
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['credential']['token']}"}


async def counts(harness, headers):
    """Investigations of the guest behind ``headers`` and the intake jobs keyed to them."""
    async with harness.control.engine.connect() as connection:
        owner = await connection.scalar(
            select(credentials.c.principal_id).where(
                credentials.c.token_hash == hash_token(headers["Authorization"].split(" ", 1)[1])
            )
        )
        ids = (
            (
                await connection.execute(
                    select(investigations.c.id).where(investigations.c.owner_id == owner)
                )
            )
            .scalars()
            .all()
        )
        hashes = [intake_stage_key(identifier).input_hash for identifier in ids]
        job_count = await connection.scalar(
            select(func.count())
            .select_from(jobs)
            .where(jobs.c.stage == INTAKE_STAGE, jobs.c.input_hash.in_(hashes or ["none"]))
        )
    return len(ids), job_count


async def test_dispatcher_failure_rolls_back_investigation_and_job(harness, tmp_path):
    """The queue row is written, then the dispatcher fails: neither row may survive."""

    class FailingAfterEnqueue(QueueDispatcher):
        async def dispatch(self, connection, investigation_id, owner_id):
            await super().dispatch(connection, investigation_id, owner_id)
            enqueued = await connection.scalar(
                select(func.count())
                .select_from(jobs)
                .where(jobs.c.input_hash == intake_stage_key(investigation_id).input_hash)
            )
            assert enqueued == 1, "the job must exist inside the transaction"
            raise RuntimeError("simulated dispatcher failure")

    app = create_app(
        harness.settings(storage_dir=tmp_path), dispatcher=FailingAfterEnqueue(harness.queue)
    )
    async with app.router.lifespan_context(app), client(app) as http:
        headers = {**(await guest(http)), "Idempotency-Key": "rollback"}
        response = await http.post("/v1/investigations", json=URL_BODY, headers=headers)
        assert response.status_code == 500
        assert response.json()["code"] == "INTERNAL_ERROR"
        assert "simulated" not in response.text
        assert (await http.get("/v1/investigations", headers=headers)).json() == {"items": []}
        assert await counts(harness, headers) == (0, 0)


async def test_duplicate_idempotent_post_yields_exactly_one_job(harness, tmp_path):
    app = create_app(harness.settings(storage_dir=tmp_path))
    async with app.router.lifespan_context(app), client(app) as http:
        headers = {**(await guest(http)), "Idempotency-Key": "dup"}
        sequential = [
            await http.post("/v1/investigations", json=URL_BODY, headers=headers) for _ in range(2)
        ]
        concurrent = await asyncio.gather(
            *(http.post("/v1/investigations", json=URL_BODY, headers=headers) for _ in range(4))
        )
        responses = sequential + list(concurrent)
        assert {response.status_code for response in responses} == {202}
        bodies = [response.json() for response in responses]
        assert all(body == bodies[0] for body in bodies), "replays return the original body"
        investigation_id = uuid.UUID(bodies[0]["id"])
        assert await counts(harness, headers) == (1, 1)
    async with harness.control.engine.connect() as connection:
        job = (
            await connection.execute(
                select(jobs).where(
                    jobs.c.input_hash == intake_stage_key(investigation_id).input_hash
                )
            )
        ).one()
    assert job.stage == INTAKE_STAGE and job.version == 1
    assert job.state == JobState.QUEUED.value
    assert job.payload["investigation_id"] == str(investigation_id)


class CrashOnInvestigation:
    """Crash at ``before_publish`` only for the intake job of one investigation.

    The test database is shared, so earlier tests may have left other queued intake jobs
    behind; the production stage table processes them normally.
    """

    def __init__(self):
        self.investigation_id = None
        self.hits = []

    async def checkpoint(self, name, job):
        if job.payload.get("investigation_id") != str(self.investigation_id):
            return
        self.hits.append((name, job.attempts))
        if name is Checkpoint.BEFORE_PUBLISH and job.attempts == 1:
            raise SimulatedCrash("simulated crash before publishing the intake result")


async def test_worker_crash_before_publish_re_leases_and_publishes_intake_once(
    harness, tmp_path, caplog
):
    """The embedded worker dies between the intake handler and publication; a standalone
    worker with the production stage table re-leases the job and the investigation ends
    up published exactly once."""
    faults = CrashOnInvestigation()

    class RecordingDispatcher(QueueDispatcher):
        async def dispatch(self, connection, investigation_id, owner_id):
            # Known to the fault before the transaction commits, so the worker cannot
            # claim the job before the crash is armed.
            faults.investigation_id = investigation_id
            await super().dispatch(connection, investigation_id, owner_id)

    app = create_app(
        harness.settings(embed_worker=True, storage_dir=tmp_path),
        handlers=default_handlers(settings=harness.settings(storage_dir=tmp_path)),
        faults=faults,
        dispatcher=RecordingDispatcher(harness.queue),
    )
    with pytest.raises(SimulatedCrash):
        async with app.router.lifespan_context(app):
            crashed = app.state.worker
            async with client(app) as http:
                headers = {**(await guest(http)), "Idempotency-Key": "crash"}
                created = await http.post("/v1/investigations", json=URL_BODY, headers=headers)
                assert created.status_code == 202, created.text
                investigation_id = uuid.UUID(created.json()["id"])
                assert faults.investigation_id == investigation_id
                key = intake_stage_key(investigation_id)
                with pytest.raises(SimulatedCrash):
                    await crashed.wait()
                record = await harness.wait_for_state_by_key(key, JobState.RUNNING)
                assert record.lease_owner == crashed.worker_id
                during = await http.get(f"/v1/investigations/{investigation_id}", headers=headers)
                assert during.json()["state"] == "running"
                assert crashed.worker_id not in during.text
                assert await harness.queue.published(key) == []
    assert "Worker failed" in caplog.text

    recovering = harness.worker(
        None,
        stages=default_handlers(settings=harness.settings(storage_dir=tmp_path)),
        worker_id="intake-recovering",
        storage_dir=tmp_path,
    )
    await recovering.start()
    record = await harness.wait_for_state_by_key(key, JobState.PUBLISHED)
    await recovering.stop()
    assert record.attempts == 2 and record.fencing_token == 2
    result = await harness.assert_invariants(
        key, record.id, worker_ids=[crashed.worker_id, "intake-recovering"]
    )
    assert result.result == {
        "investigation_id": str(investigation_id),
        "stage": INTAKE_STAGE,
        "coverage": {"status": "not_started"},
    }
    assert faults.hits == [(Checkpoint.CLAIMED, 1), (Checkpoint.BEFORE_PUBLISH, 1)]
    api = create_app(harness.settings(storage_dir=tmp_path))
    async with api.router.lifespan_context(api), client(api) as http:
        after = await http.get(f"/v1/investigations/{investigation_id}", headers=headers)
        assert after.status_code == 200
        assert after.json()["state"] == "queued" and after.json()["stage"] == INTAKE_STAGE
        assert after.json()["error"] is None
    assert await counts(harness, headers) == (1, 1)


async def test_intake_stage_rejects_a_missing_or_foreign_investigation(harness):
    """A job whose investigation is gone or belongs to another owner fails without retry."""
    from services.jobs.queue import StageKey
    from services.pipeline.intake import intake_payload

    worker = harness.worker(
        None, stages=default_handlers(settings=harness.settings()), worker_id="intake-guard"
    )
    await worker.start()
    missing = uuid.uuid4()
    async with harness.control.engine.begin() as connection:
        enqueued = await harness.queue.enqueue(
            connection, intake_stage_key(missing), intake_payload(missing, uuid.uuid4())
        )
        malformed = await harness.queue.enqueue(
            connection, StageKey(1, INTAKE_STAGE, uuid.uuid4().hex), {"investigation_id": "x"}
        )
    for job_id in (enqueued.job_id, malformed.job_id):
        record = await harness.wait_for_state(job_id, JobState.FAILED)
        assert record.retry_class is not None and record.retry_class.value == "non_retriable_input"
        assert record.failure == "NonRetriableInput"
    await worker.stop()
