import asyncio
import os
import signal
import sys
from unittest.mock import AsyncMock

import pytest

from services.database import Database
from services.settings import Settings
from services.worker.runtime import Worker


async def test_start_stop_idempotence_and_no_task_leak(database_url):
    database = Database(Settings(database_url=database_url, _env_file=None))
    worker = Worker(database, 2)
    try:
        await worker.stop()
        await worker.start()
        assert worker.running
        await worker.stop()
        await worker.wait()
        assert not worker.running
        await worker.stop()
        with pytest.raises(RuntimeError, match="only be started once"):
            await worker.start()
    finally:
        await database.close()
    assert not [t for t in asyncio.all_tasks() if t.get_name() == "ovrly-worker"]


async def test_start_failure_is_visible(database_url, monkeypatch, caplog):
    database = Database(Settings(database_url=database_url, _env_file=None))
    monkeypatch.setattr(database, "ping", AsyncMock(side_effect=OSError("private details")))
    worker = Worker(database, 2)
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await worker.start()
        assert not worker.running
        assert "Worker failed" in caplog.text
        assert "private details" not in caplog.text
        with pytest.raises(RuntimeError, match="database unavailable"):
            await worker.stop()
    finally:
        await database.close()


async def test_bounded_shutdown_cancels_stuck_owned_task(database_url):
    class StuckWorker(Worker):
        async def run(self):
            self._started.set()
            await asyncio.Event().wait()

    database = Database(Settings(database_url=database_url, _env_file=None))
    worker = StuckWorker(database, 0.01)
    try:
        await worker.start()
        with pytest.raises(RuntimeError, match="shutdown timed out"):
            await worker.stop()
        assert not worker.running
        assert worker._task.cancelled()
    finally:
        await database.close()


async def test_cancelled_start_can_be_cleaned_up(database_url, monkeypatch):
    database = Database(Settings(database_url=database_url, _env_file=None))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_ping():
        entered.set()
        await release.wait()

    monkeypatch.setattr(database, "ping", delayed_ping)
    worker = Worker(database, 2)
    start = asyncio.create_task(worker.start())
    try:
        await entered.wait()
        start.cancel()
        with pytest.raises(asyncio.CancelledError):
            await start
        release.set()
        await worker.stop()
        assert not worker.running
    finally:
        await database.close()


async def test_standalone_process_handles_sigterm(database_url):
    env = {**os.environ, "OVRLY_DATABASE_URL": database_url, "OVRLY_EMBED_WORKER": "0"}
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "services.worker",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(15):
            while True:
                line = await process.stderr.readline()
                assert line, "Worker exited before reporting readiness"
                if b"Worker ready" in line:
                    break
            process.send_signal(signal.SIGTERM)
            await process.communicate()
            assert process.returncode == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
