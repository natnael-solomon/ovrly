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
import math
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from secrets import SystemRandom
from typing import Any

from sqlalchemy import Row, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from services.database import Database
from services.jobs.retries import RateLimited
from services.models import provider_buckets, provider_slots
from services.providers.http import ProviderError

_RANDOM = SystemRandom()
DEFAULT_BLOCK_SECONDS = 60.0


@dataclass(frozen=True)
class TokenBucket:
    database: Database
    name: str
    per_hour: int
    max_wait_seconds: float = 300.0
    sleep: Callable[[float], Awaitable[Any]] = field(default=asyncio.sleep, compare=False)
    concurrency: int = 0
    request_timeout_seconds: float = 20.0

    @staticmethod
    async def balance(connection: AsyncConnection, name: str, per_hour: int) -> float:
        """Local refill estimate, not the provider's remaining account entitlement."""
        row = (
            await connection.execute(
                select(provider_buckets, func.clock_timestamp().label("now")).where(
                    provider_buckets.c.name == name
                )
            )
        ).first()
        if row is None:
            return float(per_hour)
        elapsed = max(float((row.now - row.updated_at).total_seconds()), 0.0)
        return min(float(per_hour), float(row.tokens) + elapsed * per_hour / 3600)

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
                    func.clock_timestamp().label("now"),
                )
                .where(provider_buckets.c.name == self.name)
                .with_for_update()
            )
        ).one()
        now: datetime = row.now
        elapsed = max((now - row.updated_at).total_seconds(), 0.0)
        return min(float(self.per_hour), row.tokens + elapsed * self.per_second), now

    async def _take(self, amount: float = 1) -> float:
        """Take the requested units, or return the seconds until they are available."""
        async with self.database.engine.begin() as connection:
            tokens, now = await self._locked(connection)
            if tokens >= amount:
                tokens -= amount
                wait = 0.0
            else:
                wait = (amount - tokens) / self.per_second
            await connection.execute(
                update(provider_buckets)
                .where(provider_buckets.c.name == self.name)
                .values(tokens=tokens, updated_at=now)
            )
        return wait

    async def acquire(self, amount: float = 1) -> None:
        if not math.isfinite(amount) or not 0 < amount <= self.per_hour:
            raise ValueError("Token cost must be positive, finite and within the bucket capacity")
        while True:
            wait = await self._take(amount)
            if wait == 0:
                return
            if wait > self.max_wait_seconds:
                raise RateLimited(wait)
            await self.sleep(wait + _RANDOM.uniform(0, 0.25 * wait + 0.05))

    async def _slot(self, lease_id: uuid.UUID) -> int | None:
        async with self.database.engine.begin() as connection:
            for slot in range(self.concurrency):
                result = await connection.execute(
                    insert(provider_slots)
                    .values(
                        provider=self.name,
                        slot=slot,
                        lease_id=lease_id,
                        expires_at=func.clock_timestamp()
                        + timedelta(seconds=self.request_timeout_seconds + 5),
                    )
                    .on_conflict_do_update(
                        index_elements=["provider", "slot"],
                        set_={
                            "lease_id": lease_id,
                            "expires_at": func.clock_timestamp()
                            + timedelta(seconds=self.request_timeout_seconds + 5),
                        },
                        where=provider_slots.c.expires_at <= func.clock_timestamp(),
                    )
                    .returning(provider_slots.c.slot)
                )
                acquired = result.scalar_one_or_none()
                if acquired is not None:
                    return int(acquired)
        return None

    @asynccontextmanager
    async def request(self) -> AsyncIterator[None]:
        """Acquire an expiring request slot and rate units without holding a DB connection.

        The hard request deadline is shorter than the slot lease. A crashed process cannot
        hold capacity forever, and release cannot erase a successor's lease.
        """
        if not self.concurrency:
            await self.acquire()
            yield
            return
        lease_id = uuid.uuid4()
        slot = await self._slot(lease_id)
        if slot is None:
            raise RateLimited(1)
        try:
            try:
                async with asyncio.timeout(self.request_timeout_seconds):
                    wait = await self._take()
                    if wait:
                        raise RateLimited(wait)
                    yield
            except TimeoutError:
                raise ProviderError("Provider request deadline exceeded") from None
        finally:
            async with self.database.engine.begin() as connection:
                await connection.execute(
                    delete(provider_slots).where(
                        provider_slots.c.provider == self.name,
                        provider_slots.c.slot == slot,
                        provider_slots.c.lease_id == lease_id,
                    )
                )

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
