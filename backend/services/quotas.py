"""Opt-in admission budgets. Callers lock the principal before any owned object."""

import math
import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import String, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import Principal
from services.api.auth.dependency import lock_active_principal
from services.api.errors import ApiError
from services.jobs.models import jobs
from services.models import capture_sessions, quota_usage
from services.providers.budget import TokenBucket
from services.settings import Settings


async def lock_owner(connection: AsyncConnection, principal: Principal, config: Settings) -> None:
    if not config.quotas_enabled:
        return
    await lock_active_principal(connection, principal, exclusive=True)


def exhausted(code: str, message: str, seconds: float) -> ApiError:
    return ApiError(
        429,
        code,
        message,
        retryable=True,
        action="retry",
        headers={"Retry-After": str(max(1, math.ceil(seconds)))},
    )


async def provider_gate(connection: AsyncConnection, config: Settings) -> None:
    if not config.quotas_enabled or config.scholarxiv_api_key is None:
        return
    balance = await TokenBucket.balance(
        connection, "scholarxiv", config.scholarxiv_requests_per_hour
    )
    if balance < config.quota_provider_reserve:
        wait = (
            (config.quota_provider_reserve - balance) * 3600 / config.scholarxiv_requests_per_hour
        )
        raise exhausted(
            "PROVIDER_QUOTA_EXHAUSTED",
            "New checks are paused while the shared provider budget recovers",
            wait,
        )


async def active_checks(connection: AsyncConnection, owner_id: uuid.UUID) -> set[str]:
    # Chunk fan-out is one check, not dozens of user jobs. Unknown future payloads count
    # conservatively as individual jobs rather than silently evading admission limits.
    identifiers = select(
        func.coalesce(
            jobs.c.payload["investigation_id"].astext,
            jobs.c.payload["capture_id"].astext,
            jobs.c.payload["session_id"].astext,
            cast(jobs.c.id, String),
        )
    ).where(
        jobs.c.owner_id == owner_id,
        jobs.c.state.in_(["queued", "leased", "running"]),
    )
    opened = select(cast(capture_sessions.c.id, String)).where(
        capture_sessions.c.owner_id == owner_id,
        capture_sessions.c.state == "open",
        capture_sessions.c.expires_at > func.clock_timestamp(),
    )
    return set((await connection.execute(identifiers.union(opened))).scalars())


async def charge(
    connection: AsyncConnection,
    principal: Principal,
    config: Settings,
    *,
    checks: int = 0,
    upload_bytes: int = 0,
    investigation_id: uuid.UUID | None = None,
) -> None:
    """Charge in the admission transaction, after replay detection and before enqueue.

    A single UTC-day row per principal survives content deletion; failed transactions do
    not spend budget. Accepted uploads reserve declared bytes, including abandoned uploads.
    """
    if not config.quotas_enabled:
        return
    if checks:
        await provider_gate(connection, config)
        active = await active_checks(connection, principal.id)
        if len(active) >= config.quota_active_checks and str(investigation_id) not in active:
            raise exhausted("QUOTA_EXCEEDED", "Wait for an active check to finish or cancel it", 30)
    now: datetime = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
    day = now.astimezone(UTC).date()
    used = (
        await connection.execute(select(quota_usage).where(quota_usage.c.owner_id == principal.id))
    ).first()
    previous_checks = used.checks if used is not None and used.day == day else 0
    previous_bytes = used.upload_bytes if used is not None and used.day == day else 0
    reset = datetime.combine(day + timedelta(days=1), time(), UTC)
    if previous_checks + checks > config.quota_daily_checks:
        raise exhausted(
            "QUOTA_EXCEEDED", "The daily check budget is exhausted", (reset - now).total_seconds()
        )
    if previous_bytes + upload_bytes > config.quota_daily_upload_bytes:
        raise exhausted(
            "QUOTA_EXCEEDED", "The daily upload budget is exhausted", (reset - now).total_seconds()
        )
    values = {
        "day": day,
        "checks": previous_checks + checks,
        "upload_bytes": previous_bytes + upload_bytes,
    }
    await connection.execute(
        insert(quota_usage)
        .values(owner_id=principal.id, **values)
        .on_conflict_do_update(index_elements=["owner_id"], set_=values)
    )
