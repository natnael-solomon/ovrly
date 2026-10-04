"""Opt-in demo retention on the durable queue; see the Proposed BC-D06 policy."""

import hashlib
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.database import Database
from services.jobs.handlers import JobContext
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, JobQueue, StageKey
from services.models import investigations, principals, uploads
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
                        .where(principals.c.created_at <= now - lifetime)
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
                    .where(principals.c.id == owner_id, principals.c.created_at <= now - lifetime)
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
