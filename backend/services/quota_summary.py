"""Read-only operator summary: ``python -m services.quota_summary``. No provider calls."""

import asyncio
import json
import math
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.models import provider_buckets, provider_slots
from services.providers.budget import TokenBucket
from services.settings import Settings, load_settings


async def summary(database: Database, config: Settings) -> dict[str, Any]:
    async with database.engine.connect() as connection:
        now: datetime = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
        tracked = await connection.scalar(
            select(provider_buckets.c.name).where(provider_buckets.c.name == "scholarxiv")
        )
        balance = await TokenBucket.balance(
            connection, "scholarxiv", config.scholarxiv_requests_per_hour
        )
        slots = await connection.scalar(
            select(func.count())
            .select_from(provider_slots)
            .where(
                provider_slots.c.provider == "scholarxiv",
                provider_slots.c.expires_at > func.clock_timestamp(),
            )
        )
    configured = config.scholarxiv_api_key is not None
    wait = max(
        0,
        math.ceil(
            (config.quota_provider_reserve - balance) * 3600 / config.scholarxiv_requests_per_hour
        ),
    )
    return {
        "as_of": now.isoformat(),
        "policy": "proposed",
        "enforced": config.quotas_enabled,
        "intake_paused": config.quotas_enabled and configured and wait > 0,
        "retry_after_seconds": wait if config.quotas_enabled and configured else 0,
        "limits": {
            "daily_checks_per_principal": config.quota_daily_checks,
            "active_checks_per_principal": config.quota_active_checks,
            "daily_upload_bytes_per_principal": config.quota_daily_upload_bytes,
            "claims_per_run": min(config.evidence_max_claims, config.quota_claims_per_run),
        },
        "providers": {
            "scholarxiv": {
                "status": "configured" if configured else "not_configured",
                "measurement": "local_token_bucket_only",
                "observed": tracked is not None,
                "local_available_units": max(0, math.floor(balance)) if tracked else None,
                "refill_units_per_hour": config.scholarxiv_requests_per_hour,
                "upstream_remaining": None,
                "active_request_slots": slots,
                "concurrency_limit": config.quota_provider_concurrency
                if config.quotas_enabled
                else None,
            },
            "groq": {"status": "not_integrated", "upstream_remaining": None},
            "voxide": {"status": "client_managed_not_integrated", "upstream_remaining": None},
        },
    }


async def main() -> None:
    config = load_settings()
    database = Database(config)
    try:
        result = await summary(database, config)
    except (SQLAlchemyError, OSError, TimeoutError):
        print("Quota summary unavailable: database request failed", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await database.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
