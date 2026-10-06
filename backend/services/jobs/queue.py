"""PostgreSQL job queue with ``FOR UPDATE SKIP LOCKED`` claims and fenced publication.

Each mutation is one transaction and corresponds to a :class:`~services.jobs.states.JobEvent`.
A worker's :class:`Lease` carries the fencing token and generation it observed when it
claimed the job; every lease-holder write is compare-and-set against the current row,
so a stale worker (expired lease, re-leased job) or a late result after cancellation or
deletion cannot publish or otherwise mutate the job.
"""

import logging
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, Text, and_, case, delete, func, select, update
from sqlalchemy.dialects.postgresql import array, insert
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from services.database import Database
from services.jobs.models import job_results, jobs
from services.jobs.retries import RetryClass
from services.jobs.states import LEASED_STATES, JobState, JobStatus

logger = logging.getLogger(__name__)

_LEASED = [state.value for state in LEASED_STATES]


@dataclass(frozen=True)
class StageKey:
    """Idempotency key of one pipeline stage: (version, stage, input hash)."""

    version: int
    stage: str
    input_hash: str


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    worker_id: str
    fencing_token: int
    generation: int
    expires_at: datetime


@dataclass(frozen=True)
class ClaimedJob:
    lease: Lease
    key: StageKey
    payload: dict[str, Any]
    attempts: int
    retry_counts: Mapping[str, int]
    # Set when an earlier attempt recorded a provider request id; reconcile, do not re-call.
    provider_request_id: str | None

    @property
    def id(self) -> UUID:
        return self.lease.job_id


@dataclass(frozen=True)
class Enqueued:
    job_id: UUID
    created: bool


@dataclass(frozen=True)
class JobRecord:
    id: UUID
    key: StageKey
    status: JobStatus
    fencing_token: int
    generation: int
    attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    failure: str | None
    retry_class: RetryClass | None
    retry_counts: Mapping[str, int]
    provider_request_id: str | None
    available_at: datetime


@dataclass(frozen=True)
class PublishedResult:
    job_id: UUID
    key: StageKey
    fencing_token: int
    generation: int
    result: dict[str, Any]


class CancelOutcome(StrEnum):
    EFFECTIVE = "effective"
    REQUESTED = "requested"
    NOT_CANCELLABLE = "not_cancellable"


class LeaseLost(Exception):
    """The lease's fencing token or generation no longer matches the job row."""


class _NotOwned(Exception):
    """Internal: the compare-and-set matched no row."""


class PublishRejected(Exception):
    def __init__(self, job_id: UUID, reason: str):
        super().__init__(f"Publication of job {job_id} rejected: {reason}")
        self.job_id = job_id
        self.reason = reason


def _key(row: Row[Any]) -> StageKey:
    return StageKey(row.version, row.stage, row.input_hash)


class JobQueue:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _owned(lease: Lease) -> ColumnElement[bool]:
        return and_(
            jobs.c.id == lease.job_id,
            jobs.c.lease_owner == lease.worker_id,
            jobs.c.fencing_token == lease.fencing_token,
            jobs.c.generation == lease.generation,
        )

    @staticmethod
    def _cleared() -> dict[str, Any]:
        return {"lease_owner": None, "lease_expires_at": None, "updated_at": func.now()}

    async def enqueue(
        self,
        connection: AsyncConnection,
        key: StageKey,
        payload: dict[str, Any],
        *,
        owner_id: UUID | None = None,
    ) -> Enqueued:
        """Insert a queued job inside the caller's transaction.

        The caller writes its business record on the same connection so both rows
        commit or roll back together. Re-enqueueing an existing stage key returns the
        existing job without creating a duplicate.
        """
        statement = (
            insert(jobs)
            .values(
                id=uuid4(),
                owner_id=owner_id,
                version=key.version,
                stage=key.stage,
                input_hash=key.input_hash,
                state=JobState.QUEUED.value,
                payload=payload,
            )
            .on_conflict_do_nothing(constraint="uq_jobs_stage_key")
            .returning(jobs.c.id)
        )
        job_id = (await connection.execute(statement)).scalar_one_or_none()
        if job_id is not None:
            return Enqueued(job_id, created=True)
        existing = await connection.execute(
            select(jobs.c.id, jobs.c.owner_id).where(
                jobs.c.version == key.version,
                jobs.c.stage == key.stage,
                jobs.c.input_hash == key.input_hash,
            )
        )
        row = existing.one()
        if row.owner_id != owner_id:
            raise ValueError("A stage key cannot be reused across job owners")
        return Enqueued(row.id, created=False)

    async def claim(
        self, worker_id: str, stages: Sequence[str], lease_seconds: float
    ) -> ClaimedJob | None:
        """Expire overdue leases, then lease one queued job for ``worker_id``."""
        if not stages:
            return None
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(jobs)
                .where(jobs.c.state.in_(_LEASED), jobs.c.lease_expires_at < func.now())
                .values(
                    state=case(
                        (jobs.c.cancel_requested, JobState.CANCELLED.value),
                        else_=JobState.QUEUED.value,
                    ),
                    generation=jobs.c.generation + case((jobs.c.cancel_requested, 1), else_=0),
                    **self._cleared(),
                )
            )
            candidate = (
                select(jobs.c.id)
                .where(
                    jobs.c.stage.in_(list(stages)),
                    jobs.c.state == JobState.QUEUED.value,
                    jobs.c.available_at <= func.now(),
                )
                .order_by(jobs.c.available_at, jobs.c.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            row = (
                await connection.execute(
                    update(jobs)
                    .where(jobs.c.id == candidate)
                    .values(
                        state=JobState.LEASED.value,
                        lease_owner=worker_id,
                        lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
                        fencing_token=jobs.c.fencing_token + 1,
                        attempts=jobs.c.attempts + 1,
                        updated_at=func.now(),
                    )
                    .returning(
                        jobs.c.id,
                        jobs.c.version,
                        jobs.c.stage,
                        jobs.c.input_hash,
                        jobs.c.fencing_token,
                        jobs.c.generation,
                        jobs.c.lease_expires_at,
                        jobs.c.payload,
                        jobs.c.attempts,
                        jobs.c.retry_counts,
                        jobs.c.provider_request_id,
                    )
                )
            ).first()
        if row is None:
            return None
        lease = Lease(row.id, worker_id, row.fencing_token, row.generation, row.lease_expires_at)
        return ClaimedJob(
            lease,
            _key(row),
            dict(row.payload),
            row.attempts,
            dict(row.retry_counts),
            row.provider_request_id,
        )

    async def _fenced_update(self, lease: Lease, states: list[str], **values: Any) -> bool:
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                update(jobs)
                .where(self._owned(lease), jobs.c.state.in_(states))
                .values({"updated_at": func.now(), **values})
            )
        return result.rowcount == 1

    async def start(self, lease: Lease) -> bool:
        """Move a leased job to running unless cancellation was requested or the lease is lost."""
        async with self.database.engine.begin() as connection:
            result = await connection.execute(
                update(jobs)
                .where(
                    self._owned(lease),
                    jobs.c.state == JobState.LEASED.value,
                    jobs.c.cancel_requested.is_(False),
                )
                .values(state=JobState.RUNNING.value, updated_at=func.now())
            )
        return result.rowcount == 1

    async def heartbeat(self, lease: Lease, lease_seconds: float) -> bool:
        """Extend an owned lease and report whether cancellation has been requested."""
        async with self.database.engine.begin() as connection:
            row = (
                await connection.execute(
                    update(jobs)
                    .where(self._owned(lease), jobs.c.state.in_(_LEASED))
                    .values(
                        lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
                        updated_at=func.now(),
                    )
                    .returning(jobs.c.cancel_requested)
                )
            ).first()
        if row is None:
            raise LeaseLost(f"Lease on job {lease.job_id} is no longer current")
        return bool(row.cancel_requested)

    async def publish(
        self,
        lease: Lease,
        result: dict[str, Any],
        *,
        connection: AsyncConnection | None = None,
    ) -> PublishedResult:
        """Publish a stage result and mark the job published in one transaction.

        The update is compare-and-set against the lease owner, fencing token and
        generation; the stage-key uniqueness of ``job_results`` rejects any duplicate.
        A supplied connection lets orchestration enqueue successors in the same transaction.
        """
        try:
            async with (
                self.database.engine.begin() if connection is None else nullcontext(connection)
            ) as connection:
                row = (
                    await connection.execute(
                        update(jobs)
                        .where(
                            self._owned(lease),
                            jobs.c.state == JobState.RUNNING.value,
                            jobs.c.cancel_requested.is_(False),
                        )
                        .values(state=JobState.PUBLISHED.value, **self._cleared())
                        .returning(jobs.c.version, jobs.c.stage, jobs.c.input_hash)
                    )
                ).first()
                if row is None:
                    raise _NotOwned
                await connection.execute(
                    job_results.insert().values(
                        job_id=lease.job_id,
                        version=row.version,
                        stage=row.stage,
                        input_hash=row.input_hash,
                        fencing_token=lease.fencing_token,
                        generation=lease.generation,
                        result=result,
                    )
                )
                published = PublishedResult(
                    lease.job_id, _key(row), lease.fencing_token, lease.generation, result
                )
        except _NotOwned:
            # Diagnose on a fresh connection after the rolled-back transaction released its own.
            raise PublishRejected(lease.job_id, await self._rejection_reason(lease)) from None
        except IntegrityError:
            raise PublishRejected(
                lease.job_id, "a result for this stage key already exists"
            ) from None
        logger.info("Published job %s with fencing token %s", lease.job_id, lease.fencing_token)
        return published

    async def _rejection_reason(self, lease: Lease) -> str:
        record = await self.get(lease.job_id)
        if record is None:
            return "the job no longer exists"
        if record.generation != lease.generation:
            return f"the job was {record.status.state.value} (generation changed)"
        if record.fencing_token != lease.fencing_token:
            return "the lease is stale (fencing token changed)"
        if record.status.state not in LEASED_STATES:
            return f"the job is {record.status.state.value}"
        if record.lease_owner != lease.worker_id:
            return "the lease is stale (owner changed)"
        if record.status.cancel_requested:
            return "cancellation was requested"
        return f"the job is {record.status.state.value}"

    async def fail(self, lease: Lease, reason: str, retry_class: RetryClass | None = None) -> bool:
        return await self._fenced_update(
            lease,
            _LEASED,
            state=JobState.FAILED.value,
            failure=reason,
            retry_class=retry_class.value if retry_class is not None else None,
            **self._cleared(),
        )

    async def retry(self, lease: Lease, retry_class: RetryClass, delay_seconds: float) -> bool:
        """Return an owned lease to the queue after ``delay_seconds`` and count the retry.

        A pending cancellation wins and becomes effective, exactly as in :meth:`release`.
        """
        counter = jobs.c.retry_counts[retry_class.value].as_integer()
        counted = func.jsonb_set(
            jobs.c.retry_counts,
            array([retry_class.value], type_=Text),
            func.to_jsonb(func.coalesce(counter, 0) + 1),
        )
        return await self._fenced_update(
            lease,
            _LEASED,
            state=case(
                (jobs.c.cancel_requested, JobState.CANCELLED.value),
                else_=JobState.QUEUED.value,
            ),
            generation=jobs.c.generation + case((jobs.c.cancel_requested, 1), else_=0),
            available_at=func.now() + timedelta(seconds=delay_seconds),
            retry_class=retry_class.value,
            retry_counts=counted,
            **self._cleared(),
        )

    async def record_request_id(self, lease: Lease, request_id: str) -> None:
        """Persist the provider request id before the call so a timeout can reconcile."""
        if not await self._fenced_update(lease, _LEASED, provider_request_id=request_id):
            raise LeaseLost(f"Lease on job {lease.job_id} is no longer current")

    async def find_by_request_id(self, request_id: str) -> JobRecord | None:
        async with self.database.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(jobs).where(jobs.c.provider_request_id == request_id)
                )
            ).first()
        return self._record(row) if row is not None else None

    async def release(self, lease: Lease) -> bool:
        """Return an owned lease to the queue, or make a requested cancellation effective."""
        return await self._fenced_update(
            lease,
            _LEASED,
            state=case(
                (jobs.c.cancel_requested, JobState.CANCELLED.value),
                else_=JobState.QUEUED.value,
            ),
            generation=jobs.c.generation + case((jobs.c.cancel_requested, 1), else_=0),
            **self._cleared(),
        )

    async def cancel(self, lease: Lease) -> bool:
        """Lease-holder acknowledgement that makes a requested cancellation effective."""
        return await self._fenced_update(
            lease,
            _LEASED,
            state=JobState.CANCELLED.value,
            cancel_requested=True,
            generation=jobs.c.generation + 1,
            **self._cleared(),
        )

    async def request_cancel(
        self, job_id: UUID, *, connection: AsyncConnection | None = None
    ) -> CancelOutcome:
        """Cancel a queued job immediately or flag a leased one for its worker."""
        if connection is None:
            async with self.database.engine.begin() as transaction:
                return await self.request_cancel(job_id, connection=transaction)
        row = (
            await connection.execute(
                select(jobs.c.state, jobs.c.cancel_requested)
                .where(jobs.c.id == job_id)
                .with_for_update()
            )
        ).first()
        if row is None:
            return CancelOutcome.NOT_CANCELLABLE
        if row.state == JobState.QUEUED.value:
            await connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state=JobState.CANCELLED.value,
                    cancel_requested=True,
                    generation=jobs.c.generation + 1,
                    **self._cleared(),
                )
            )
            return CancelOutcome.EFFECTIVE
        if row.state in _LEASED:
            if not row.cancel_requested:
                await connection.execute(
                    update(jobs)
                    .where(jobs.c.id == job_id)
                    .values(cancel_requested=True, updated_at=func.now())
                )
            return CancelOutcome.REQUESTED
        return CancelOutcome.NOT_CANCELLABLE

    async def delete(self, job_id: UUID, *, connection: AsyncConnection | None = None) -> bool:
        """Tombstone a job, bump its generation and remove any published result."""
        if connection is None:
            async with self.database.engine.begin() as transaction:
                return await self.delete(job_id, connection=transaction)
        result = await connection.execute(
            update(jobs)
            .where(jobs.c.id == job_id, jobs.c.state != JobState.DELETED.value)
            .values(
                state=JobState.DELETED.value,
                generation=jobs.c.generation + 1,
                payload={},
                **self._cleared(),
            )
        )
        if result.rowcount != 1:
            return False
        await connection.execute(delete(job_results).where(job_results.c.job_id == job_id))
        return True

    async def get(self, job_id: UUID) -> JobRecord | None:
        async with self.database.engine.connect() as connection:
            row = (await connection.execute(select(jobs).where(jobs.c.id == job_id))).first()
        return self._record(row) if row is not None else None

    @staticmethod
    def _record(row: Row[Any]) -> JobRecord:
        return JobRecord(
            id=row.id,
            key=_key(row),
            status=JobStatus(JobState(row.state), bool(row.cancel_requested)),
            fencing_token=row.fencing_token,
            generation=row.generation,
            attempts=row.attempts,
            lease_owner=row.lease_owner,
            lease_expires_at=row.lease_expires_at,
            failure=row.failure,
            retry_class=RetryClass(row.retry_class) if row.retry_class is not None else None,
            retry_counts=dict(row.retry_counts),
            provider_request_id=row.provider_request_id,
            available_at=row.available_at,
        )

    async def published(self, key: StageKey) -> list[PublishedResult]:
        async with self.database.engine.connect() as connection:
            rows = await connection.execute(
                select(job_results).where(
                    job_results.c.version == key.version,
                    job_results.c.stage == key.stage,
                    job_results.c.input_hash == key.input_hash,
                )
            )
            return [
                PublishedResult(
                    row.job_id, _key(row), row.fencing_token, row.generation, dict(row.result)
                )
                for row in rows
            ]

    async def owned_leases(self, worker_id: str) -> list[UUID]:
        async with self.database.engine.connect() as connection:
            rows = await connection.execute(
                select(jobs.c.id).where(jobs.c.lease_owner == worker_id, jobs.c.state.in_(_LEASED))
            )
            return [row.id for row in rows]
