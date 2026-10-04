"""In-process API + worker harness with fault-injection hooks for the recovery suite."""

import asyncio
import uuid
from contextlib import suppress

import httpx
import pytest
from sqlalchemy import select, text

from services.api.auth import load_owned, owned_rows
from services.api.auth.dependency import create_guest_principal
from services.api.errors import ApiError
from services.api.main import create_app
from services.database import Database
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.models import jobs
from services.jobs.queue import JobQueue, StageKey
from services.jobs.retries import UnknownOutcome
from services.settings import Settings
from services.worker.runtime import Worker


class ScriptedFaults:
    """Crash at ``crash_at`` for the listed attempts and record every checkpoint hit."""

    def __init__(self, crash_at=None, attempts=(1,), on_checkpoint=None):
        self.crash_at = crash_at
        self.attempts = set(attempts)
        self.on_checkpoint = on_checkpoint
        self.hits = []

    async def checkpoint(self, name: Checkpoint, job):
        self.hits.append((name, job.attempts))
        if self.on_checkpoint is not None:
            await self.on_checkpoint(name, job)
        if name == self.crash_at and job.attempts in self.attempts:
            raise SimulatedCrash(f"simulated crash at {name}")


class StubProvider:
    """In-test stand-in for a hosted model call; no network, no credentials.

    ``call`` records the request id and returns a result. With ``timeout_next`` set, the
    next call raises ``UnknownOutcome`` after the work was accepted, so the following
    attempt must reconcile through ``lookup`` instead of calling again.
    """

    def __init__(self):
        self.calls = []
        self.accepted = {}
        self.timeout_next = False
        self.lookups = []

    async def call(self, request_id, payload):
        self.calls.append(request_id)
        result = {"request_id": request_id, "text": f"result for {payload.get('input')}"}
        self.accepted[request_id] = result
        if self.timeout_next:
            self.timeout_next = False
            raise UnknownOutcome("provider timed out")
        return result

    async def lookup(self, request_id):
        self.lookups.append(request_id)
        return self.accepted.get(request_id)


class StubArtifactStore:
    """Idempotent artifact writes keyed by the stage key."""

    def __init__(self):
        self.writes = []
        self.artifacts = {}

    async def put(self, key, content):
        self.writes.append(key)
        self.artifacts.setdefault(key, content)
        return f"artifact://{key.stage}/{key.input_hash}"


class Harness:
    def __init__(self, database_url):
        self.database_url = database_url
        self.stage = "stage-" + uuid.uuid4().hex[:8]
        self.databases = []
        self.workers = []
        self.control = self.database()
        self.queue = JobQueue(self.control)
        self.calls = []
        self.provider = StubProvider()
        self.artifacts = StubArtifactStore()
        self.owner = None
        self.outsider = None
        self.token = None
        self.job_ids = []

    async def initialize(self):
        async with self.control.engine.begin() as connection:
            self.owner, self.token = await create_guest_principal(connection)
            self.outsider, self.outsider_token = await create_guest_principal(connection)

    def client(self, app, *, outsider=False):
        token = self.outsider_token if outsider else self.token
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )

    def settings(self, **overrides):
        values = {
            "database_url": self.database_url,
            "worker_shutdown_seconds": 2,
            "job_lease_seconds": 0.5,
            "job_poll_seconds": 0.05,
            "_env_file": None,
        }
        values.update(overrides)
        return Settings(**values)

    def database(self, **overrides):
        database = Database(self.settings(**overrides))
        self.databases.append(database)
        return database

    def worker(self, handler, *, faults=None, worker_id=None, retry_policy=None, **overrides):
        settings = self.settings(**overrides)
        worker = Worker(
            self.database(**overrides),
            settings.worker_shutdown_seconds,
            handlers={self.stage: handler},
            faults=faults,
            lease_seconds=settings.job_lease_seconds,
            poll_seconds=settings.job_poll_seconds,
            retry_policy=retry_policy,
            worker_id=worker_id,
        )
        self.workers.append(worker)
        return worker

    def app(self, handler, *, faults=None, **overrides):
        return create_app(
            self.settings(embed_worker=True, **overrides),
            handlers={self.stage: handler},
            faults=faults,
        )

    def key(self):
        return StageKey(1, self.stage, uuid.uuid4().hex)

    async def enqueue(self, payload=None):
        key = self.key()
        async with self.control.engine.begin() as connection:
            enqueued = await self.queue.enqueue(
                connection, key, payload or {"input": "x"}, owner_id=self.owner.id
            )
        self.job_ids.append(enqueued.job_id)
        return key, enqueued.job_id

    def recording_handler(self, result=None):
        async def handler(job, context):
            self.calls.append((job.attempts, job.lease.worker_id))
            return result if result is not None else {"attempt": job.attempts}

        return handler

    def provider_handler(self, *, store_artifact=False):
        """Stub stage: record a request id, call the provider once, reconcile on re-lease,
        optionally store an artifact under the stage key, then return the result."""

        async def handler(job, context):
            self.calls.append((job.attempts, job.lease.worker_id))
            request_id = job.provider_request_id
            if request_id is None:
                request_id = f"req-{job.id.hex[:8]}-{job.attempts}"
                await context.record_request_id(request_id)
                result = await self.provider.call(request_id, job.payload)
                await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
            else:
                result = await self.provider.lookup(request_id)
                if result is None:
                    raise UnknownOutcome("provider has no record of the request")
            if store_artifact:
                location = await self.artifacts.put(job.key, result)
                await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
                result = {**result, "artifact": location}
            return result

        return handler

    async def wait_for_state(self, job_id, *states, seconds=5):
        async with asyncio.timeout(seconds):
            while True:
                record = await self.queue.get(job_id)
                if record is not None and record.status.state in states:
                    return record
                await asyncio.sleep(0.02)

    async def wait_until(self, predicate, seconds=5):
        async with asyncio.timeout(seconds):
            while True:
                if predicate():
                    return
                await asyncio.sleep(0.02)

    async def assert_invariants(self, key, job_id, *, worker_ids=()):
        """No duplicate publication, no lost job, no version conflict, no leases held."""
        await self.assert_no_cross_owner_read(job_id)
        record = await self.queue.get(job_id)
        assert record is not None, "The job was lost"
        assert record.status.state.value == "published", record.status
        results = await self.queue.published(key)
        assert len(results) == 1, f"Expected exactly one publication, found {len(results)}"
        (result,) = results
        assert result.job_id == job_id
        assert result.key == key == record.key, "Published result does not match the stage key"
        assert result.fencing_token == record.fencing_token, "Stale fencing token was published"
        assert result.generation == record.generation, "Publication crossed a generation"
        assert record.lease_owner is None and record.lease_expires_at is None
        for worker_id in worker_ids:
            assert await self.queue.owned_leases(worker_id) == []
        for worker in self.workers:
            assert worker.owned_leases == frozenset()
        return result

    async def assert_deleted(self, key, job_id):
        """Tombstoned, payload cleared, no published result, no held lease."""
        await self.assert_no_cross_owner_read(job_id)
        record = await self.queue.get(job_id)
        assert record is not None, "A tombstone must remain"
        assert record.status.state.value == "deleted", record.status
        assert record.lease_owner is None and record.lease_expires_at is None
        assert await self.queue.published(key) == [], "Deleted content was resurrected"
        async with self.control.engine.connect() as connection:
            payload = await connection.scalar(
                text("SELECT payload FROM jobs WHERE id = :id"), {"id": job_id}
            )
        assert payload == {}, "A tombstone must not keep its payload"
        for worker in self.workers:
            assert job_id not in worker.owned_leases
        return record

    async def assert_failed(self, key, job_id, retry_class, *, failure=None):
        await self.assert_no_cross_owner_read(job_id)
        record = await self.queue.get(job_id)
        assert record is not None, "The job was lost"
        assert record.status.state.value == "failed", record.status
        assert record.retry_class is retry_class
        if failure is not None:
            assert record.failure == failure
        assert record.lease_owner is None
        assert await self.queue.published(key) == []
        return record

    async def assert_no_cross_owner_read(self, job_id):
        async with self.control.engine.connect() as connection:
            owner = await load_owned(connection, jobs, job_id, self.owner)
            assert owner.owner_id == self.owner.id
            assert (
                await connection.execute(owned_rows(jobs, self.outsider).where(jobs.c.id == job_id))
            ).first() is None
            with pytest.raises(ApiError) as error:
                await load_owned(connection, jobs, job_id, self.outsider)
            assert error.value.status_code == 404
            assert error.value.code == "NOT_FOUND"
        app = create_app(self.settings())
        async with app.router.lifespan_context(app), self.client(app, outsider=True) as client:
            async with self.control.engine.connect() as connection:
                before = (await connection.execute(select(jobs).where(jobs.c.id == job_id))).one()
            for method, suffix in (("POST", "/cancel"), ("DELETE", "")):
                response = await client.request(method, f"/v1/jobs/{job_id}{suffix}")
                missing = await client.request(
                    method,
                    f"/v1/jobs/{uuid.uuid4()}{suffix}",
                    headers={"X-Request-Id": response.headers["X-Request-Id"]},
                )
                assert response.status_code == missing.status_code == 404
                assert response.json() == missing.json()
            async with self.control.engine.connect() as connection:
                after = (await connection.execute(select(jobs).where(jobs.c.id == job_id))).one()
            assert before == after, "An outsider mutated the job"

    async def close(self):
        for worker in self.workers:
            with suppress(Exception, SimulatedCrash):
                await worker.stop()
        for database in self.databases:
            await database.close()
        for database in self.databases:
            assert database.engine.pool.checkedout() == 0
