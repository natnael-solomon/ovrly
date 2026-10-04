"""In-process API + worker harness with fault-injection hooks for the recovery suite."""

import asyncio
import uuid
from contextlib import suppress

from services.api.main import create_app
from services.database import Database
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.queue import JobQueue, StageKey
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


class Harness:
    def __init__(self, database_url):
        self.database_url = database_url
        self.stage = "stage-" + uuid.uuid4().hex[:8]
        self.databases = []
        self.workers = []
        self.control = self.database()
        self.queue = JobQueue(self.control)
        self.calls = []

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

    def worker(self, handler, *, faults=None, worker_id=None, **overrides):
        settings = self.settings(**overrides)
        worker = Worker(
            self.database(**overrides),
            settings.worker_shutdown_seconds,
            handlers={self.stage: handler},
            faults=faults,
            lease_seconds=settings.job_lease_seconds,
            poll_seconds=settings.job_poll_seconds,
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
            enqueued = await self.queue.enqueue(connection, key, payload or {"input": "x"})
        return key, enqueued.job_id

    def recording_handler(self, result=None):
        async def handler(job, context):
            self.calls.append((job.attempts, job.lease.worker_id))
            return result if result is not None else {"attempt": job.attempts}

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

    async def close(self):
        for worker in self.workers:
            with suppress(Exception):
                await worker.stop()
        for database in self.databases:
            await database.close()
        for database in self.databases:
            assert database.engine.pool.checkedout() == 0
