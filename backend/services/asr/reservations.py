"""Shared rolling-window accounting. No outcome is assumed to refund provider usage."""

import hashlib
import math
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.asr.groq import ASRQuotaExhausted, ASRUnavailable, ASRUnknownOutcome
from services.jobs.handlers import JobContext
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob
from services.jobs.retries import RateLimited
from services.models import asr_requests
from services.settings import Settings


def now() -> datetime:
    return datetime.now(UTC)


def payload_index(payload: Mapping[str, Any], retry_counts: Mapping[str, int]) -> int:
    return int(payload.get("speech_retry", 0)) * 100 + sum(retry_counts.values())


def retry_index(job: ClaimedJob) -> int:
    return payload_index(job.payload, job.retry_counts)


# Outcomes after which a model may have processed (and charged for) the audio.
UNSAFE_TO_RESEND = frozenset({"reserved", "uncertain", "ASRUnknownOutcome", "completed"})


async def attempts(job: ClaimedJob, context: JobContext) -> dict[str, Row[Any]]:
    """Latest ledger row per model for this run; an unrecorded marker is an unknown outcome."""
    async with context.queue.database.engine.begin() as connection:
        await context.queue.lock_active(connection, job.lease)
        rows = (
            await connection.execute(
                select(asr_requests)
                .where(asr_requests.c.job_id == job.id)
                .order_by(asr_requests.c.created_at, asr_requests.c.id)
            )
        ).all()
    if not rows and job.provider_request_id is not None:
        raise ASRUnknownOutcome
    index = retry_index(job)
    return {row.model: row for row in rows if row.retry_index == index}


async def begin(
    job: ClaimedJob,
    context: JobContext,
    settings: Settings,
    model: str,
    duration_seconds: float,
    clock: Callable[[], datetime] | None = None,
) -> uuid.UUID:
    """Reserve ``model``'s quota (or claim an owner-retry reservation) before its one call.

    The row is stored as ``uncertain`` before HTTP, so a crash during the call is never
    followed by a silent second request to the same model.
    """
    async with context.queue.database.engine.begin() as connection:
        await context.queue.lock_active(connection, job.lease)
        reserved = (
            await connection.execute(
                select(asr_requests)
                .where(
                    asr_requests.c.job_id == job.id,
                    asr_requests.c.model == model,
                    asr_requests.c.retry_index == retry_index(job),
                    asr_requests.c.outcome == "reserved",
                )
                .with_for_update()
            )
        ).first()
        if reserved is not None:
            if reserved.account_id != settings.groq_account_id:
                raise ASRUnavailable
            await connection.execute(
                update(asr_requests)
                .where(asr_requests.c.id == reserved.id)
                .values(outcome="uncertain")
            )
            return uuid.UUID(str(reserved.id))
        return await reserve_in(
            connection,
            job.id,
            settings,
            duration_seconds,
            retry_index(job),
            model=model,
            timestamp=clock() if clock is not None else now(),
            outcome="uncertain",
        )


async def replay_safe(
    connection: AsyncConnection,
    job_id: uuid.UUID,
    payload: Mapping[str, Any],
    counts: Mapping[str, int],
) -> bool:
    """True when no model in the job's latest run may have processed the audio."""
    outcomes: list[str] = list(
        (
            await connection.execute(
                select(asr_requests.c.outcome).where(
                    asr_requests.c.job_id == job_id,
                    asr_requests.c.retry_index == payload_index(payload, counts),
                )
            )
        ).scalars()
    )
    return not any(outcome in UNSAFE_TO_RESEND for outcome in outcomes)


async def reserve_in(
    connection: AsyncConnection,
    job_id: uuid.UUID,
    settings: Settings,
    duration_seconds: float,
    index: int,
    *,
    model: str | None = None,
    timestamp: datetime | None = None,
    outcome: str = "reserved",
) -> uuid.UUID:
    """Reserve one request for ``model`` (default: the primary); each model has its own windows."""
    account = settings.groq_account_id
    model = model or settings.groq_model
    lock = int.from_bytes(
        hashlib.sha256(f"groq:{account}:{model}".encode()).digest()[:8], "big", signed=True
    )
    seconds = max(math.ceil(duration_seconds), settings.asr_minimum_billable_seconds or 1)
    await connection.execute(select(func.pg_advisory_xact_lock(lock)))
    timestamp = timestamp if timestamp is not None else now()
    for window, limit, audio in (
        (60, settings.asr_requests_per_minute, False),
        (86400, settings.asr_requests_per_day, False),
        (3600, settings.asr_audio_seconds_per_hour, True),
        (86400, settings.asr_audio_seconds_per_day, True),
    ):
        used = await connection.scalar(
            select(
                func.coalesce(func.sum(asr_requests.c.audio_seconds), 0) if audio else func.count()
            )
            .select_from(asr_requests)
            .where(
                asr_requests.c.account_id == account,
                asr_requests.c.model == model,
                asr_requests.c.created_at > timestamp - timedelta(seconds=window),
            )
        )
        if limit is None or int(used or 0) + (seconds if audio else 1) > limit:
            raise ASRQuotaExhausted
    request_id = uuid.uuid4()
    await connection.execute(
        insert(asr_requests).values(
            id=request_id,
            job_id=job_id,
            account_id=account,
            model=model,
            audio_seconds=seconds,
            created_at=timestamp,
            outcome=outcome,
            retry_index=index,
        )
    )
    await connection.execute(
        update(jobs).where(jobs.c.id == job_id).values(provider_request_id=str(request_id))
    )
    return request_id


async def complete(
    job: ClaimedJob,
    context: JobContext,
    request_id: uuid.UUID,
    result: dict[str, Any] | None,
    *,
    error: Exception | None = None,
) -> None:
    async with context.queue.database.engine.begin() as connection:
        await context.queue.lock_active(connection, job.lease)
        await connection.execute(
            update(asr_requests)
            .where(asr_requests.c.id == request_id, asr_requests.c.job_id == job.id)
            .values(
                outcome=type(error).__name__ if error is not None else "completed",
                result=result,
                retry_after=(
                    math.ceil(error.retry_after_seconds)
                    if isinstance(error, RateLimited) and error.retry_after_seconds is not None
                    else None
                ),
            )
        )
