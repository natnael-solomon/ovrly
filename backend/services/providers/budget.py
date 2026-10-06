"""A token bucket in PostgreSQL, shared by every worker process.

The Scholarxiv Papers and Router APIs share one rolling hourly limit per account, so the
bucket lives in the database rather than in one process. ``acquire`` refills by elapsed
time and takes one token under a row lock. When the bucket is empty it *waits* for the next
token (with jitter, so waiting workers do not wake in step) instead of failing the stage;
the evidence handlers keep their lease alive meanwhile. Only a wait longer than
``max_wait_seconds`` raises :class:`RateLimited` so the job is rescheduled. ``block`` holds
the bucket for a provider's ``Retry-After`` after a 429 by driving the balance negative, so
every process pauses exactly that long and then resumes at the normal rate.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from secrets import SystemRandom
from typing import Any

from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from services.database import Database
from services.jobs.retries import RateLimited
from services.models import provider_buckets

_RANDOM = SystemRandom()
DEFAULT_BLOCK_SECONDS = 60.0


@dataclass(frozen=True)
class TokenBucket:
    database: Database
    name: str
    per_hour: int
    max_wait_seconds: float = 300.0
    sleep: Callable[[float], Awaitable[Any]] = field(default=asyncio.sleep, compare=False)

    @property
    def per_second(self) -> float:
        return self.per_hour / 3600

    async def _locked(self, connection: AsyncConnection) -> tuple[float, datetime]:
        await connection.execute(
            insert(provider_buckets)
            .values(name=self.name, tokens=float(self.per_hour), updated_at=func.now())
            .on_conflict_do_nothing(index_elements=["name"])
        )
        row: Row[Any] = (
            await connection.execute(
                select(
                    provider_buckets.c.tokens,
                    provider_buckets.c.updated_at,
                    func.now().label("now"),
                )
                .where(provider_buckets.c.name == self.name)
                .with_for_update()
            )
        ).one()
        now: datetime = row.now
        elapsed = max((now - row.updated_at).total_seconds(), 0.0)
        return min(float(self.per_hour), row.tokens + elapsed * self.per_second), now

    async def _take(self) -> float:
        """Take one token and return 0, or return the seconds until the next token."""
        async with self.database.engine.begin() as connection:
            tokens, now = await self._locked(connection)
            if tokens >= 1:
                tokens -= 1
                wait = 0.0
            else:
                wait = (1 - tokens) / self.per_second
            await connection.execute(
                update(provider_buckets)
                .where(provider_buckets.c.name == self.name)
                .values(tokens=tokens, updated_at=now)
            )
        return wait

    async def acquire(self) -> None:
        while True:
            wait = await self._take()
            if wait == 0:
                return
            if wait > self.max_wait_seconds:
                raise RateLimited(wait)
            await self.sleep(wait + _RANDOM.uniform(0, 0.25 * wait + 0.05))

    async def block(self, seconds: float | None) -> None:
        """Pause every user of the bucket for ``seconds`` (a provider's Retry-After)."""
        hold = DEFAULT_BLOCK_SECONDS if seconds is None else max(seconds, 0.0)
        async with self.database.engine.begin() as connection:
            tokens, now = await self._locked(connection)
            await connection.execute(
                update(provider_buckets)
                .where(provider_buckets.c.name == self.name)
                .values(tokens=min(tokens, -hold * self.per_second), updated_at=now)
            )
