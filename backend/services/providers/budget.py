"""A token bucket in PostgreSQL, shared by every worker process.

The Scholarxiv Papers and Router APIs share one rolling hourly limit per account, so the
bucket lives in the database rather than in one process. ``acquire`` refills by elapsed
time, takes one token under a row lock and raises :class:`RateLimited` with the wait until
the next token when the bucket is empty. ``drain`` empties it after a provider 429, so
every process waits instead of hammering the provider.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from services.database import Database
from services.jobs.retries import RateLimited
from services.models import provider_buckets


@dataclass(frozen=True)
class TokenBucket:
    database: Database
    name: str
    per_hour: int

    @property
    def per_second(self) -> float:
        return self.per_hour / 3600

    async def acquire(self) -> None:
        async with self.database.engine.begin() as connection:
            await connection.execute(
                insert(provider_buckets)
                .values(name=self.name, tokens=float(self.per_hour), updated_at=func.now())
                .on_conflict_do_nothing(index_elements=["name"])
            )
            row = (
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
            tokens = min(float(self.per_hour), row.tokens + elapsed * self.per_second)
            if tokens < 1:
                await connection.execute(
                    update(provider_buckets)
                    .where(provider_buckets.c.name == self.name)
                    .values(tokens=tokens, updated_at=now)
                )
                raise RateLimited((1 - tokens) / self.per_second)
            await connection.execute(
                update(provider_buckets)
                .where(provider_buckets.c.name == self.name)
                .values(tokens=tokens - 1, updated_at=now)
            )

    async def drain(self) -> None:
        async with self.database.engine.begin() as connection:
            await connection.execute(
                update(provider_buckets)
                .where(provider_buckets.c.name == self.name)
                .values(tokens=0.0, updated_at=func.now())
            )
