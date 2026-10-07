"""Readiness behind ``/healthz`` and the operational signals of RFC section 16 (BE-11, #21).

Readiness fails (503 with one safe ``reason``) when the database is unreachable, the schema
is not at the Alembic head this build ships, the upload directory is not writable, or the
embedded worker has stopped or not polled within the heartbeat limit. Queue depth, the age
of the oldest claimable job and the worker heartbeat age are reported as signals. No
provider is contacted: a probe would spend the shared Scholarxiv quota and a provider
outage must not make the host restart the service.
"""

import asyncio
import logging
import os
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.jobs.models import jobs
from services.settings import Settings
from services.worker.runtime import Worker

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
# RFC section 16: a worker that has not polled or extended a lease for two minutes is stuck.
HEARTBEAT_STALE_SECONDS = 120.0

DATABASE_ERRORS = (SQLAlchemyError, OSError, TimeoutError)


def migration_heads(directory: Path = MIGRATIONS_DIR) -> frozenset[str]:
    """Alembic head revisions shipped with this build."""
    return frozenset(ScriptDirectory(str(directory)).get_heads())


def heartbeat_limit(settings: Settings) -> float:
    """Age after which the embedded worker counts as stuck.

    Never below two minutes, and never below one lease or two idle waits, so a configured
    long lease or idle backoff cannot make a healthy worker look stale.
    """
    idle = settings.job_idle_poll_max_seconds or settings.job_poll_seconds
    return max(HEARTBEAT_STALE_SECONDS, settings.job_lease_seconds, 2 * idle)


def storage_ready(path: Path) -> bool:
    """True when uploads can be written under ``path``.

    The local store creates the directory on first write, so the nearest existing
    ancestor must be a writable directory.
    """
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            return False
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)


@dataclass(frozen=True)
class DatabaseState:
    versions: frozenset[str]
    queue_depth: int
    oldest_queued_seconds: float | None


async def database_state(
    database: Database, heads: frozenset[str], stages: Collection[str] | None
) -> DatabaseState:
    """Applied Alembic revisions and claimable queue signals in one bounded connection.

    The queue is read only when the schema is at ``heads``. ``stages`` limits the queue
    signals to stages the embedded worker can run; stages without a handler yet (for
    example ``asr``) stay queued by design. ``None`` counts every stage.
    """
    async with asyncio.timeout(database.timeout):
        async with database.engine.connect() as connection:
            present: bool = (
                await connection.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL"))
            ).scalar_one()
            if not present:
                return DatabaseState(frozenset(), 0, None)
            versions: frozenset[str] = frozenset(
                (await connection.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            if versions != heads:
                return DatabaseState(versions, 0, None)
            query = select(
                func.count(),
                func.extract("epoch", func.now() - func.min(jobs.c.available_at)),
            ).where(jobs.c.state == "queued", jobs.c.available_at <= func.now())
            if stages is not None:
                query = query.where(jobs.c.stage.in_(list(stages)))
            depth, oldest = (await connection.execute(query)).one()
    return DatabaseState(versions, int(depth), None if oldest is None else round(float(oldest), 1))


def _unavailable(reason: str) -> tuple[int, dict[str, Any]]:
    return 503, {"status": "unavailable", "reason": reason}


async def readiness(
    database: Database,
    worker: Worker | None,
    settings: Settings,
    heads: frozenset[str],
) -> tuple[int, dict[str, Any]]:
    try:
        await database.ping()
        stages = list(worker.handlers) if worker is not None else None
        state = await database_state(database, heads, stages)
    except DATABASE_ERRORS:
        logger.warning("Readiness failed: database unavailable")
        return _unavailable("database")
    if state.versions != heads:
        logger.warning("Readiness failed: database schema is not at the migration head")
        return _unavailable("migrations")
    if not storage_ready(settings.storage_dir):
        logger.warning("Readiness failed: upload storage is not writable")
        return _unavailable("storage")
    heartbeat: float | None = None
    if settings.embed_worker:
        age = worker.heartbeat_age if worker is not None else None
        if worker is None or not worker.running or age is None:
            logger.warning("Readiness failed: embedded worker unavailable")
            return _unavailable("worker")
        if age > heartbeat_limit(settings):
            logger.warning("Readiness failed: embedded worker heartbeat is stale")
            return _unavailable("worker")
        heartbeat = round(age, 1)
    return 200, {
        "status": "ok",
        "checks": {
            "database": "ok",
            "migrations": "ok",
            "storage": "ok",
            "worker": "ok" if settings.embed_worker else "not_embedded",
        },
        "signals": {
            "queue_depth": state.queue_depth,
            "oldest_queued_seconds": state.oldest_queued_seconds,
            "worker_heartbeat_seconds": heartbeat,
            "scholarxiv": "configured" if settings.scholarxiv_api_key else "not_configured",
        },
    }
