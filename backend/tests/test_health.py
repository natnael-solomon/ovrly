"""Readiness logic and worker heartbeat without a database (the PostgreSQL cases are in
test_api.py)."""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import health
from services.jobs.handlers import CancellationRequested, JobContext
from services.settings import Settings
from services.worker.runtime import Worker

URL = "postgresql+psycopg://ovrly:test-only@127.0.0.1:55432/ovrly"


def settings(**values):
    return Settings(database_url=URL, _env_file=None, **values)


def test_migration_heads_is_the_newest_revision_file():
    newest = sorted(path.stem for path in (health.MIGRATIONS_DIR / "versions").glob("0*.py"))[-1]
    assert health.migration_heads() == frozenset({newest})


def test_heartbeat_limit_covers_lease_and_idle_backoff():
    assert health.heartbeat_limit(settings()) == 120
    assert health.heartbeat_limit(settings(job_lease_seconds=300)) == 300
    assert health.heartbeat_limit(settings(job_idle_poll_max_seconds=90)) == 180


def test_storage_ready(tmp_path):
    assert health.storage_ready(tmp_path)
    assert health.storage_ready(tmp_path / "not" / "created" / "yet")
    blocker = tmp_path / "file"
    blocker.write_bytes(b"x")
    assert not health.storage_ready(blocker)
    assert not health.storage_ready(blocker / "child")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX permissions")
def test_storage_not_writable(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert not health.storage_ready(locked / "uploads")
    finally:
        locked.chmod(0o700)


class FakeWorker:
    def __init__(self, running=True, age=1.0):
        self.running = running
        self.heartbeat_age = age
        self.handlers = {"intake": None}


def database(fail=None):
    return SimpleNamespace(ping=AsyncMock(side_effect=fail))


@pytest.fixture
def state(monkeypatch):
    calls = []

    async def fake_state(_database, heads, stages):
        calls.append(stages)
        return health.DatabaseState(fake_state.versions, 3, 12.5)

    fake_state.versions = frozenset({"head"})
    monkeypatch.setattr(health, "database_state", fake_state)
    fake_state.calls = calls
    return fake_state


async def test_ready_body_reports_checks_and_signals(state, tmp_path):
    config = settings(embed_worker=True, storage_dir=tmp_path, scholarxiv_api_key="sxv_x")
    status, body = await health.readiness(database(), FakeWorker(), config, frozenset({"head"}))
    assert status == 200
    assert body == {
        "status": "ok",
        "checks": {"database": "ok", "migrations": "ok", "storage": "ok", "worker": "ok"},
        "signals": {
            "queue_depth": 3,
            "oldest_queued_seconds": 12.5,
            "worker_heartbeat_seconds": 1.0,
            "scholarxiv": "configured",
        },
    }
    assert state.calls == [["intake"]]
    assert "sxv_" not in str(body)


async def test_api_only_readiness_does_not_claim_a_worker(state, tmp_path):
    config = settings(storage_dir=tmp_path)
    status, body = await health.readiness(database(), None, config, frozenset({"head"}))
    assert status == 200
    assert body["checks"]["worker"] == "not_embedded"
    assert body["signals"]["worker_heartbeat_seconds"] is None
    assert body["signals"]["scholarxiv"] == "not_configured"
    assert state.calls == [None]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("ping", "database"),
        ("query", "database"),
        ("behind", "migrations"),
        ("storage", "storage"),
        ("stopped", "worker"),
        ("missing", "worker"),
        ("stale", "worker"),
        ("unstarted", "worker"),
    ],
)
async def test_unready_reasons(state, tmp_path, monkeypatch, case, reason, caplog):
    config = settings(embed_worker=True, storage_dir=tmp_path)
    db = database(OSError("private details") if case == "ping" else None)
    worker = {
        "stopped": FakeWorker(running=False),
        "missing": None,
        "stale": FakeWorker(age=121.0),
        "unstarted": FakeWorker(age=None),
    }.get(case, FakeWorker())
    if case == "query":

        async def broken(*_args):
            raise TimeoutError("private details")

        monkeypatch.setattr(health, "database_state", broken)
    if case == "behind":
        state.versions = frozenset({"older"})
    if case == "storage":
        blocker = tmp_path / "file"
        blocker.write_bytes(b"x")
        config = settings(embed_worker=True, storage_dir=blocker)
    status, body = await health.readiness(db, worker, config, frozenset({"head"}))
    assert (status, body) == (503, {"status": "unavailable", "reason": reason})
    assert "private details" not in caplog.text


class FakeQueue:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.waits = []

    async def claim(self, *_args):
        return self.jobs.pop(0) if self.jobs else None


async def test_idle_backoff_doubles_to_the_cap_and_resets_after_a_claim(monkeypatch):
    waits = []
    worker = Worker(SimpleNamespace(ping=AsyncMock()), 1, poll_seconds=1, idle_poll_max_seconds=5)
    claimed = SimpleNamespace(id="job")
    worker.queue = FakeQueue([None, None, None, None, claimed, None, None])
    executed = []

    async def execute(job):
        executed.append(job)

    async def fake_wait_for(awaitable, seconds):
        awaitable.close()
        waits.append(seconds)
        if len(waits) == 6:
            worker.request_stop()
        raise TimeoutError

    monkeypatch.setattr(worker, "_execute", execute)
    monkeypatch.setattr("services.worker.runtime.asyncio.wait_for", fake_wait_for)
    await worker.run()
    assert waits == [1, 2, 4, 5, 1, 2]
    assert executed == [claimed]
    assert worker.heartbeat_age is not None and worker.heartbeat_age < 5


async def test_default_poll_is_fixed():
    worker = Worker(SimpleNamespace(), 1, poll_seconds=2)
    assert worker.idle_poll_max_seconds == 2
    assert worker.heartbeat_age is None


async def test_job_heartbeat_refreshes_the_worker_beat():
    beats = []
    queue = SimpleNamespace(heartbeat=AsyncMock(side_effect=[False, True]))
    lease = SimpleNamespace(job_id="job")
    context = JobContext(queue, lease, 30, on_heartbeat=lambda: beats.append(1))
    await context.heartbeat()
    with pytest.raises(CancellationRequested):
        await context.heartbeat()
    assert beats == [1, 1]
    await asyncio.sleep(0)
