import asyncio
import socket
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from services.api.main import create_app
from services.database import Database
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
            assert response.json() == {"status": "ok"}
            if embedded:
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
