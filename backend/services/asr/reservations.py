"""Shared rolling-window accounting. No outcome is assumed to refund provider usage."""

import hashlib
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.asr.groq import ASRQuotaExhausted, ASRUnavailable, ASRUnknownOutcome
from services.jobs.handlers import JobContext
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob
from services.jobs.retries import RateLimited, Transient
from services.models import asr_requests
from services.settings import Settings


def now() -> datetime:
    return datetime.now(UTC)


def retry_index(job: ClaimedJob) -> int:
    return int(job.payload.get("speech_retry", 0)) * 100 + sum(job.retry_counts.values())


async def reserve(
    job: ClaimedJob,
    context: JobContext,
    settings: Settings,
    duration_seconds: float,
    clock: Callable[[], datetime] | None = None,
) -> uuid.UUID:
    async with context.queue.database.engine.begin() as connection:
        await context.queue.lock_active(connection, job.lease)
        reserved = (
            await connection.execute(
                select(asr_requests)
                .where(asr_requests.c.job_id == job.id, asr_requests.c.outcome == "reserved")
                .with_for_update()
            )
        ).first()
        if reserved is not None:
            if (
                reserved.account_id != settings.groq_account_id
                or reserved.model != settings.groq_model
                or reserved.retry_index != retry_index(job)
            ):
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
            timestamp=clock() if clock is not None else now(),
            outcome="uncertain",
        )


def windows(settings: Settings) -> tuple[tuple[int, int | None, bool], ...]:
    """(seconds, limit, counts audio seconds) for each configured Groq speech window."""
    return (
        (60, settings.asr_requests_per_minute, False),
        (86400, settings.asr_requests_per_day, False),
        (3600, settings.asr_audio_seconds_per_hour, True),
        (86400, settings.asr_audio_seconds_per_day, True),
    )


def window_filter(settings: Settings, seconds: int, timestamp: datetime) -> list[Any]:
    return [
        asr_requests.c.account_id == settings.groq_account_id,
        asr_requests.c.model == settings.groq_model,
        asr_requests.c.created_at > timestamp - timedelta(seconds=seconds),
    ]


async def window_usage(
    connection: AsyncConnection,
    settings: Settings,
    seconds: int,
    audio: bool,
    timestamp: datetime,
) -> int:
    """Requests or audio seconds reserved for this account/model in the rolling window."""
    used = await connection.scalar(
        select(func.coalesce(func.sum(asr_requests.c.audio_seconds), 0) if audio else func.count())
        .select_from(asr_requests)
        .where(*window_filter(settings, seconds, timestamp))
    )
    return int(used or 0)


async def reserve_in(
    connection: AsyncConnection,
    job_id: uuid.UUID,
    settings: Settings,
    duration_seconds: float,
    index: int,
    *,
    timestamp: datetime | None = None,
    outcome: str = "reserved",
) -> uuid.UUID:
    account = settings.groq_account_id
    model = settings.groq_model
    lock = int.from_bytes(
        hashlib.sha256(f"groq:{account}:{model}".encode()).digest()[:8], "big", signed=True
    )
    seconds = max(math.ceil(duration_seconds), settings.asr_minimum_billable_seconds or 1)
    await connection.execute(select(func.pg_advisory_xact_lock(lock)))
    timestamp = timestamp if timestamp is not None else now()
    for window, limit, audio in windows(settings):
        used = await window_usage(connection, settings, window, audio, timestamp)
        if limit is None or used + (seconds if audio else 1) > limit:
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


async def resume(job: ClaimedJob, context: JobContext) -> dict[str, Any] | None:
    async with context.queue.database.engine.begin() as connection:
        await context.queue.lock_active(connection, job.lease)
        latest = (
            await connection.execute(
                select(asr_requests)
                .where(asr_requests.c.job_id == job.id)
                .order_by(asr_requests.c.retry_index.desc(), asr_requests.c.created_at.desc())
                .limit(1)
            )
        ).first()
    if latest is None:
        if job.provider_request_id is not None:
            raise ASRUnknownOutcome
        return None
    if latest.outcome == "completed":
        result: dict[str, Any] = latest.result
        return result
    if latest.outcome == "reserved":
        return None
    if latest.outcome in {"Transient", "RateLimited"}:
        if latest.retry_index < retry_index(job):
            return None
        if latest.outcome == "RateLimited":
            raise RateLimited(latest.retry_after)
        raise Transient
    if latest.outcome == "ASRQuotaExhausted":
        raise ASRQuotaExhausted
    if latest.outcome in {"uncertain", "ASRUnknownOutcome"}:
        raise ASRUnknownOutcome
    raise ASRUnavailable


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
