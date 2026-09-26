import asyncio
import os
import subprocess
import sys
import time

import pytest
from sqlalchemy import text

from services.database import Database
from services.settings import Settings


def test_migration_roundtrip_and_drift(database_url):
    env = {**os.environ, "OVRLY_DATABASE_URL": database_url}
    for args in (
        ["upgrade", "head"],
        ["current"],
        ["check"],
        ["downgrade", "base"],
        ["upgrade", "head"],
        ["check"],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if args == ["current"]:
            assert "0001_baseline" in result.stdout


async def test_real_database_connection_and_disposal(database_url):
    database = Database(Settings(database_url=database_url, _env_file=None))
    try:
        await database.ping()
        async with database.engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT current_database()"))
                == database.engine.url.database
            )
    finally:
        await database.close()
    assert database.engine.pool.checkedout() == 0


async def test_readiness_probe_is_bounded_when_pool_is_exhausted(database_url):
    settings = Settings(database_url=database_url, database_timeout_seconds=0.05, _env_file=None)
    database = Database(settings)
    try:
        async with database.engine.connect(), database.engine.connect():
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(database.ping(), timeout=1)
            assert time.monotonic() - started < 0.5
    finally:
        await database.close()
