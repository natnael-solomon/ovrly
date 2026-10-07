"""Per-provider budgets shared by every API and worker process (BC-D06, #22).

Scholarxiv and the Groq extraction fallback use PostgreSQL token buckets
(``provider_buckets``). Groq speech uses the rolling ``asr_requests`` reservation ledger
from BE-07, which already reserves before every call; this module reads the same windows
instead of keeping a second ledger. Both kinds report one status shape: local remaining
units, the reserve below which new intake pauses, and the time until the reserve is back,
including any provider ``Retry-After`` still in force. Every figure is a local estimate.
No provider balance is read, so none is claimed.
"""

import asyncio
import contextlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.asr.reservations import window_filter, window_usage, windows
from services.database import Database
from services.models import asr_requests, provider_buckets
from services.pipeline.llm import reserved_tokens
from services.providers.budget import SharedBudget, TokenBucket
from services.settings import Settings

SCHOLARXIV_BUCKET = "scholarxiv"
Provider = Literal["scholarxiv", "groq_asr", "groq_llm"]
# Bucket name, unit, period in seconds and the setting holding its capacity.
GROQ_LLM_BUCKETS: tuple[tuple[str, str, int, str], ...] = (
    ("groq_llm:requests_minute", "requests", 60, "groq_llm_requests_per_minute"),
    ("groq_llm:requests_day", "requests", 86400, "groq_llm_requests_per_day"),
    ("groq_llm:tokens_minute", "tokens", 60, "groq_llm_tokens_per_minute"),
    ("groq_llm:tokens_day", "tokens", 86400, "groq_llm_tokens_per_day"),
)
_ASR_NAMES = {
    (60, False): ("groq_asr:requests_minute", "requests"),
    (86400, False): ("groq_asr:requests_day", "requests"),
    (3600, True): ("groq_asr:audio_seconds_hour", "audio_seconds"),
    (86400, True): ("groq_asr:audio_seconds_day", "audio_seconds"),
}


@dataclass(frozen=True)
class LimitStatus:
    name: str
    unit: str
    capacity: int
    period_seconds: int
    # None when the bucket has never been used; it then counts as full.
    available: float | None
    reserve: float
    recover_seconds: float


@dataclass(frozen=True)
class ProviderStatus:
    provider: Provider
    configured: bool
    measurement: Literal["local_token_bucket", "local_rolling_ledger"]
    limits: tuple[LimitStatus, ...]
    hold_seconds: float = 0.0

    @property
    def retry_after_seconds(self) -> float:
        return max([self.hold_seconds, *(limit.recover_seconds for limit in self.limits)])

    @property
    def near_exhaustion(self) -> bool:
        return self.retry_after_seconds > 0

    @property
    def pauses_intake(self) -> bool:
        return self.configured and self.near_exhaustion


def groq_llm_buckets(database: Database, settings: Settings) -> tuple[TokenBucket, ...]:
    return tuple(
        TokenBucket(database, name, getattr(settings, field), period_seconds=period)
        for name, _, period, field in GROQ_LLM_BUCKETS
    )


async def _bucket(
    connection: AsyncConnection, name: str, unit: str, capacity: int, period: int, reserve: float
) -> LimitStatus:
    observed = await connection.scalar(
        select(provider_buckets.c.name).where(provider_buckets.c.name == name)
    )
    balance = await TokenBucket.balance(connection, name, capacity, period)
    held = await TokenBucket.held_seconds(connection, name)
    return LimitStatus(
        name,
        unit,
        capacity,
        period,
        balance if observed is not None else None,
        reserve,
        max(0.0, held, (reserve - balance) * period / capacity),
    )


async def _window(
    connection: AsyncConnection,
    settings: Settings,
    seconds: int,
    limit: int,
    audio: bool,
    now: datetime,
) -> LimitStatus:
    used = await window_usage(connection, settings, seconds, audio, now)
    reserve = limit * settings.quota_provider_reserve_fraction
    recover = 0.0
    excess = used - (limit - reserve)
    if excess > 0:
        # The reserve returns when enough of the oldest reservations leave the window.
        rows = await connection.execute(
            select(asr_requests.c.created_at, asr_requests.c.audio_seconds)
            .where(*window_filter(settings, seconds, now))
            .order_by(asr_requests.c.created_at)
        )
        released = 0
        for created_at, audio_seconds in rows:
            released += int(audio_seconds) if audio else 1
            if released >= excess:
                recover = max(0.0, (created_at + timedelta(seconds=seconds) - now).total_seconds())
                break
    name, unit = _ASR_NAMES[(seconds, audio)]
    return LimitStatus(name, unit, limit, seconds, float(limit - used), reserve, recover)


async def _asr_hold(connection: AsyncConnection, settings: Settings, now: datetime) -> float:
    """Seconds left of the latest Groq speech ``Retry-After`` recorded in the ledger."""
    rows = await connection.execute(
        select(asr_requests.c.created_at, asr_requests.c.retry_after).where(
            *window_filter(settings, 86400, now),
            asr_requests.c.outcome == "RateLimited",
            asr_requests.c.retry_after.is_not(None),
        )
    )
    holds = [(created + timedelta(seconds=wait) - now).total_seconds() for created, wait in rows]
    return float(max([0.0, *holds]))


async def provider_statuses(
    connection: AsyncConnection, settings: Settings
) -> list[ProviderStatus]:
    """Read-only local status of every provider budget; no provider is contacted."""
    now: datetime = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
    fraction = settings.quota_provider_reserve_fraction
    scholarxiv = ProviderStatus(
        "scholarxiv",
        settings.scholarxiv_api_key is not None,
        "local_token_bucket",
        (
            await _bucket(
                connection,
                SCHOLARXIV_BUCKET,
                "requests",
                settings.scholarxiv_requests_per_hour,
                3600,
                float(settings.quota_provider_reserve),
            ),
        ),
    )
    speech = tuple(
        [
            await _window(connection, settings, seconds, limit, audio, now)
            for seconds, limit, audio in windows(settings)
            if limit is not None
        ]
    )
    asr = ProviderStatus(
        "groq_asr",
        # Default-on speech counts as configured only once its verified limits exist.
        settings.asr_enabled and bool(speech),
        "local_rolling_ledger",
        speech,
        await _asr_hold(connection, settings, now) if speech else 0.0,
    )
    llm = ProviderStatus(
        "groq_llm",
        settings.groq_extraction_enabled,
        "local_token_bucket",
        tuple(
            [
                await _bucket(
                    connection,
                    name,
                    unit,
                    getattr(settings, field),
                    period,
                    getattr(settings, field) * fraction,
                )
                for name, unit, period, field in GROQ_LLM_BUCKETS
            ]
        ),
    )
    return [scholarxiv, asr, llm]


class LlmAdmission:
    """Shared pre-send admission for extraction and reconciliation provider requests.

    A Scholarxiv routing, completion or feedback request takes one unit of the account-wide
    Scholarxiv bucket that the evidence stages also use. A Groq fallback request takes one
    request from its minute and day buckets and the conservative token approximation from
    its token buckets. Units are taken before the stage records the request, so a refusal
    never leaves an unknown outcome; a request refused afterwards by the per-input budget or
    cancellation is refunded because it was never sent.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def budget(self, database: Database, provider: str) -> SharedBudget:
        if provider == "scholarxiv":
            return SharedBudget(
                database,
                (
                    TokenBucket(
                        database, SCHOLARXIV_BUCKET, self.settings.scholarxiv_requests_per_hour
                    ),
                ),
            )
        if provider == "groq":
            return SharedBudget(database, groq_llm_buckets(database, self.settings))
        raise ValueError("No shared budget is defined for this provider")

    @staticmethod
    def costs(provider: str, input_bytes: int, output_tokens: int) -> list[float]:
        if provider == "groq":
            tokens = float(reserved_tokens(input_bytes, output_tokens))
            return [1.0, 1.0, tokens, tokens]
        return [1.0]

    async def acquire(
        self, database: Database, provider: str, input_bytes: int, output_tokens: int
    ) -> list[float]:
        costs = self.costs(provider, input_bytes, output_tokens)
        await self.budget(database, provider).acquire(costs)
        return costs

    async def refund(self, database: Database, provider: str, costs: list[float]) -> None:
        await self.budget(database, provider).refund(costs)

    async def release(self, database: Database, provider: str, costs: list[float]) -> None:
        """Refund units of a request that was never sent, even while being cancelled.

        The refund runs shielded so a cancellation (for example lease loss) cannot leak the
        units; a failed refund is suppressed so the original error stays visible.
        """
        refund = asyncio.ensure_future(self.refund(database, provider, costs))
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(refund)

    async def block(self, database: Database, provider: str, seconds: float | None) -> None:
        """Hold the provider's shared buckets for a provider-issued ``Retry-After``."""
        await self.budget(database, provider).block(seconds)


def llm_admission(settings: Settings | None) -> LlmAdmission | None:
    """Admission is part of the opt-in BC-D06 policy; without it behaviour is unchanged."""
    return LlmAdmission(settings) if settings is not None and settings.quotas_enabled else None


def describe(limit: LimitStatus) -> str:
    """Remaining units as an honest local estimate; the provider balance is unknown."""
    available = limit.capacity if limit.available is None else max(0, math.floor(limit.available))
    period = {60: "minute", 3600: "hour", 86400: "day"}.get(
        limit.period_seconds, f"{limit.period_seconds} s"
    )
    unit = limit.unit.replace("_", " ")
    return f"unknown (local estimate: {available} of {limit.capacity} {unit}/{period})"
