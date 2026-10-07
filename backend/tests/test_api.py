import asyncio
import socket
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from services.api.main import create_app
from services.database import Database
from services.health import database_state, migration_heads
from services.jobs.queue import JobQueue, StageKey
from services.settings import Settings
from services.worker.runtime import Worker


@pytest.mark.parametrize("embedded", [False, True])
async def test_real_readiness_and_repeated_lifecycle(database_url, embedded):
    app = create_app(Settings(database_url=database_url, embed_worker=embedded, _env_file=None))
    for _ in range(2):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/healthz")
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "ok"
            assert body["checks"] == {
                "database": "ok",
                "migrations": "ok",
                "storage": "ok",
                "worker": "ok" if embedded else "not_embedded",
            }
            assert isinstance(body["signals"]["queue_depth"], int)
            if embedded:
                assert 0 <= body["signals"]["worker_heartbeat_seconds"] < 120
                assert app.state.worker.running
            else:
                assert app.state.worker is None
        if embedded:
            assert not app.state.worker.running
        assert app.state.database.engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private database details"),
        OSError("private database details"),
        OperationalError("SELECT 1", None, Exception("private database details")),
    ],
)
async def test_database_errors_are_safe_503(database_url, monkeypatch, failure, caplog):
    app = create_app(Settings(database_url=database_url, _env_file=None))
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.database, "ping", AsyncMock(side_effect=failure))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "database"}
    assert "private database details" not in response.text + caplog.text


async def test_stopped_embedded_worker_is_not_healthy(database_url):
    app = create_app(Settings(database_url=database_url, embed_worker=True, _env_file=None))
    async with app.router.lifespan_context(app):
        await app.state.worker.stop()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 503
        assert response.json()["reason"] == "worker"


async def test_schema_behind_the_shipped_head_is_not_ready(database_url, monkeypatch):
    monkeypatch.setattr(
        "services.api.main.migration_heads", lambda: frozenset({"9999_not_applied"})
    )
    app = create_app(Settings(database_url=database_url, _env_file=None))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "migrations"}


async def test_stale_worker_heartbeat_is_not_ready(database_url):
    app = create_app(Settings(database_url=database_url, embed_worker=True, _env_file=None))
    async with app.router.lifespan_context(app):
        app.state.worker._beat -= 121
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/healthz")
        assert response.status_code == 503
        assert response.json()["reason"] == "worker"


async def test_queue_signals_count_claimable_jobs_for_the_worker_stages(database_url):
    app = create_app(Settings(database_url=database_url, _env_file=None))
    async with app.router.lifespan_context(app):
        database = app.state.database
        queue = JobQueue(database)
        stage = "health_signal_" + uuid.uuid4().hex
        async with database.engine.begin() as connection:
            await queue.enqueue(connection, StageKey(1, stage, uuid.uuid4().hex), {})
        try:
            heads = migration_heads()
            everything = await database_state(database, heads, None)
            only = await database_state(database, heads, [stage])
            none = await database_state(database, heads, ["no_such_stage"])
            behind = await database_state(database, frozenset({"other"}), None)
        finally:
            async with database.engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM jobs WHERE stage = :stage"), {"stage": stage}
                )
    assert everything.versions == heads
    assert everything.queue_depth >= 1 and only.queue_depth == 1
    assert only.oldest_queued_seconds is not None and only.oldest_queued_seconds >= 0
    assert (none.queue_depth, none.oldest_queued_seconds) == (0, None)
    assert (behind.queue_depth, behind.oldest_queued_seconds) == (0, None)


async def test_startup_failure_closes_database(database_url, monkeypatch):
    close = AsyncMock()
    monkeypatch.setattr(Database, "close", close)
    monkeypatch.setattr(Database, "ping", AsyncMock(side_effect=OSError("database down")))
    app = create_app(Settings(database_url=database_url, embed_worker=True, _env_file=None))
    with pytest.raises(RuntimeError, match="database unavailable"):
        async with app.router.lifespan_context(app):
            pytest.fail("Failed startup must not serve requests")
    close.assert_awaited_once()


async def test_real_database_unavailability_returns_503(database_url):
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        url = make_url(database_url).set(port=unused.getsockname()[1])
        app = create_app(
            Settings(
                database_url=url.render_as_string(hide_password=False),
                database_timeout_seconds=0.1,
                _env_file=None,
            )
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/healthz")
        assert response.status_code == 503
        assert response.json()["reason"] == "database"


async def test_failed_worker_stays_unhealthy_and_shutdown_disposes_resources(
    database_url, monkeypatch, caplog
):
    fail = asyncio.Event()

    class FailingWorker(Worker):
        async def run(self):
            await self.database.ping()
            self._started.set()
            await fail.wait()
            raise RuntimeError("fixture worker failure")

    monkeypatch.setattr("services.api.main.Worker", FailingWorker)
    app = create_app(Settings(database_url=database_url, embed_worker=True, _env_file=None))
    with pytest.raises(RuntimeError, match="fixture worker failure"):
        async with app.router.lifespan_context(app):
            fail.set()
            with pytest.raises(RuntimeError):
                await app.state.worker.wait()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/healthz")
            assert response.status_code == 503
            assert response.json()["reason"] == "worker"
    assert not app.state.worker.running
    assert app.state.database.engine.pool.checkedout() == 0
    assert "Worker failed" in caplog.text
