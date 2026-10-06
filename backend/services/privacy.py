"""Opt-in demo retention on the durable queue; see the Proposed BC-D06 policy."""

import hashlib
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import ColumnElement, and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth.linking import ACCOUNT_KIND
from services.database import Database
from services.jobs.handlers import JobContext
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, JobQueue, StageKey
from services.models import (
    capture_chunks,
    capture_sessions,
    investigations,
    principals,
    reanalysis_requests,
    uploads,
)
from services.settings import Settings
from services.storage import UploadStore

RETENTION_STAGE = "privacy_retention"


class Retention:
    def __init__(self, database: Database, settings: Settings, store: UploadStore):
        self.database = database
        self.settings = settings
        self.store = store
        self.queue = JobQueue(database)
        self._next_schedule = 0.0

    async def schedule(self) -> None:
        if time.monotonic() < self._next_schedule:
            return
        async with self.database.engine.begin() as connection:
            now: datetime = (await connection.execute(select(func.now()))).scalar_one()
            slot = int(now.timestamp()) // self.settings.retention_poll_seconds
            key = hashlib.sha256(f"retention:{slot}".encode()).hexdigest()
            await self.queue.enqueue(connection, StageKey(1, RETENTION_STAGE, key), {})
        self._next_schedule = time.monotonic() + self.settings.retention_poll_seconds

    async def _delete_jobs(self, connection: AsyncConnection, owner_id: UUID) -> int:
        rows = (
            await connection.execute(
                select(jobs.c.id).where(jobs.c.owner_id == owner_id).order_by(jobs.c.id)
            )
        ).all()
        for row in rows:
            await self.queue.delete(row.id, connection=connection)
            # Keep only a detached fence until the tombstone window ends.
            await connection.execute(
                update(jobs)
                .where(jobs.c.id == row.id)
                .values(
                    owner_id=None,
                    input_hash=row.id.hex,
                    provider_request_id=None,
                    failure=None,
                    cancel_outcome=None,
                )
            )
        return len(rows)

    @staticmethod
    def _expired_workspace(now: datetime, lifetime: timedelta) -> ColumnElement[bool]:
        """Guest workspaces past their lifetime, including guests already merged into an account.

        Linked accounts (BC-D07) are the durable half of the saved-report promise and are
        never expired by the demo sweep, however old their original guest row is.
        """
        return and_(
            principals.c.created_at <= now - lifetime,
            principals.c.kind != ACCOUNT_KIND,
        )

    async def run(self, job: ClaimedJob, context: JobContext) -> dict[str, int]:
        counts = {"principals": 0, "uploads": 0, "jobs": 0, "purged": 0}
        lifetime = timedelta(seconds=self.settings.retention_data_seconds)
        tombstone = timedelta(seconds=self.settings.retention_tombstone_seconds)
        async with self.database.engine.connect() as connection:
            now: datetime = (await connection.execute(select(func.now()))).scalar_one()
            owners: Sequence[UUID] = (
                (
                    await connection.execute(
                        select(principals.c.id)
                        .where(self._expired_workspace(now, lifetime))
                        .order_by(principals.c.created_at, principals.c.id)
                        .limit(self.settings.retention_batch_size)
                    )
                )
                .scalars()
                .all()
            )
        for owner_id in owners:
            await context.heartbeat()
            async with self.database.engine.begin() as connection:
                owner = await connection.scalar(
                    select(principals.c.id)
                    .where(principals.c.id == owner_id, self._expired_workspace(now, lifetime))
                    .with_for_update()
                )
                if owner is None:
                    continue
                stored = (
                    await connection.execute(
                        select(uploads)
                        .where(uploads.c.owner_id == owner_id)
                        .order_by(uploads.c.id)
                        .with_for_update()
                    )
                ).all()
                for upload in stored:
                    await self.store.delete(upload.storage_key)
                sessions: Sequence[UUID] = (
                    (
                        await connection.execute(
                            select(capture_sessions.c.id)
                            .where(capture_sessions.c.owner_id == owner_id)
                            .order_by(capture_sessions.c.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                captured: Sequence[str] = (
                    (
                        await connection.execute(
                            select(capture_chunks.c.storage_key).where(
                                capture_chunks.c.session_id.in_(sessions)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for storage_key in captured:
                    await self.store.delete(storage_key)
                # Reanalysis requests before jobs: the lock order of a forwarded cancel and
                # of an evidence publish, so the cascade below never waits behind them.
                await connection.execute(
                    select(reanalysis_requests.c.id)
                    .where(reanalysis_requests.c.owner_id == owner_id)
                    .order_by(reanalysis_requests.c.id)
                    .with_for_update()
                )
                counts["jobs"] += await self._delete_jobs(connection, owner_id)
                # Idempotency responses cascade with investigations; credentials with owners.
                await connection.execute(
                    delete(investigations).where(investigations.c.owner_id == owner_id)
                )
                await connection.execute(delete(uploads).where(uploads.c.owner_id == owner_id))
                await connection.execute(delete(principals).where(principals.c.id == owner_id))
            counts["principals"] += 1
            counts["uploads"] += len(stored)

        await context.heartbeat()
        async with self.database.engine.begin() as connection:
            expired_captures: Sequence[UUID] = (
                (
                    await connection.execute(
                        select(capture_sessions.c.id)
                        .where(
                            (capture_sessions.c.expires_at <= now)
                            | (capture_sessions.c.state == "closed")
                        )
                        .where(
                            select(capture_chunks.c.seq)
                            .where(
                                capture_chunks.c.session_id == capture_sessions.c.id,
                                capture_chunks.c.received_at.is_(None),
                            )
                            .exists()
                        )
                        .order_by(capture_sessions.c.id)
                        .limit(self.settings.retention_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for capture_id in expired_captures:
                pending = capture_chunks.c.session_id == capture_id
                pending &= capture_chunks.c.received_at.is_(None)
                keys: Sequence[str] = (
                    (await connection.execute(select(capture_chunks.c.storage_key).where(pending)))
                    .scalars()
                    .all()
                )
                for key in keys:
                    await self.store.delete(key)
                await connection.execute(delete(capture_chunks).where(pending))
        await context.heartbeat()
        async with self.database.engine.begin() as connection:
            expired_uploads = (
                await connection.execute(
                    select(uploads)
                    .where(uploads.c.state == "pending", uploads.c.expires_at <= now)
                    .order_by(uploads.c.id)
                    .limit(self.settings.retention_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for upload in expired_uploads:
                await self.store.delete(upload.storage_key)
                await connection.execute(delete(uploads).where(uploads.c.id == upload.id))
                counts["uploads"] += 1
        await context.heartbeat()
        async with self.database.engine.begin() as connection:
            expired_jobs: Sequence[UUID] = (
                (
                    await connection.execute(
                        select(jobs.c.id)
                        .where(
                            jobs.c.owner_id.is_(None),
                            jobs.c.stage != RETENTION_STAGE,
                            jobs.c.state != "deleted",
                            jobs.c.created_at <= now - lifetime,
                        )
                        .order_by(jobs.c.id)
                        .limit(self.settings.retention_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for job_id in expired_jobs:
                await self.queue.delete(job_id, connection=connection)
                await connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id)
                    .values(input_hash=job_id.hex, provider_request_id=None, failure=None)
                )
                counts["jobs"] += 1
            old = (
                select(jobs.c.id)
                .where(
                    jobs.c.updated_at <= now - tombstone,
                    (jobs.c.state == "deleted")
                    | (
                        (jobs.c.stage == RETENTION_STAGE)
                        & jobs.c.state.in_(["published", "failed"])
                    ),
                )
                .order_by(jobs.c.id)
                .limit(self.settings.retention_batch_size)
                .with_for_update(skip_locked=True)
            )
            purged = await connection.execute(delete(jobs).where(jobs.c.id.in_(old)))
            counts["purged"] = purged.rowcount
        logging.getLogger(__name__).info(
            "Retention completed (%d principals, %d uploads, %d jobs, %d purged)",
            counts["principals"],
            counts["uploads"],
            counts["jobs"],
            counts["purged"],
        )
        return counts
