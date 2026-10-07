"""Read-only operator summary: ``python -m services.quota_summary [--text]``.

It reads the shared PostgreSQL buckets and the Groq speech ledger only. No provider is
called, so every remaining balance is a local estimate and the upstream balance is unknown.
"""

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from services.database import Database
from services.models import provider_slots
from services.provider_budgets import ProviderStatus, describe, provider_statuses
from services.settings import Settings, load_settings

VOXIDE_REMAINING = (
    "unknown (local estimate: none; sessions are client-managed, read the Voxide dashboard)"
)


def _provider(status: ProviderStatus, enforced: bool) -> dict[str, Any]:
    retry = math.ceil(status.retry_after_seconds)
    return {
        "status": "configured" if status.configured else "not_configured",
        "measurement": status.measurement,
        "upstream_remaining": None,
        "remaining": [describe(limit) for limit in status.limits],
        "near_exhaustion": status.near_exhaustion,
        "pauses_intake": enforced and status.pauses_intake,
        "retry_after_seconds": retry,
        "limits": [
            {
                "name": limit.name,
                "unit": limit.unit,
                "capacity": limit.capacity,
                "period_seconds": limit.period_seconds,
                "local_available_units": None
                if limit.available is None
                else max(0, math.floor(limit.available)),
                "reserve": limit.reserve,
                "recover_seconds": math.ceil(limit.recover_seconds),
            }
            for limit in status.limits
        ],
    }


async def summary(database: Database, config: Settings) -> dict[str, Any]:
    async with database.engine.connect() as connection:
        now: datetime = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
        statuses = {
            status.provider: status for status in await provider_statuses(connection, config)
        }
        slots = await connection.scalar(
            select(func.count())
            .select_from(provider_slots)
            .where(
                provider_slots.c.provider == "scholarxiv",
                provider_slots.c.expires_at > func.clock_timestamp(),
            )
        )
    enforced = config.quotas_enabled
    paused = [name for name, status in statuses.items() if enforced and status.pauses_intake]
    scholarxiv = statuses["scholarxiv"]
    bucket = scholarxiv.limits[0]
    return {
        "as_of": now.isoformat(),
        "policy": "BC-D06 accepted in part; see the decision for pending assumptions",
        "enforced": enforced,
        "intake_paused": bool(paused),
        "paused_by": paused,
        "retry_after_seconds": max(
            [0, *(math.ceil(statuses[name].retry_after_seconds) for name in paused)]
        ),
        "limits": {
            "daily_checks_per_principal": config.quota_daily_checks,
            "active_checks_per_principal": config.quota_active_checks,
            "daily_upload_bytes_per_principal": config.quota_daily_upload_bytes,
            "claims_per_run": min(config.evidence_max_claims, config.quota_claims_per_run),
            "provider_reserve_fraction": config.quota_provider_reserve_fraction,
        },
        "providers": {
            "scholarxiv": {
                **_provider(scholarxiv, enforced),
                "observed": bucket.available is not None,
                "local_available_units": None
                if bucket.available is None
                else max(0, math.floor(bucket.available)),
                "refill_units_per_hour": config.scholarxiv_requests_per_hour,
                "active_request_slots": slots,
                "concurrency_limit": config.quota_provider_concurrency if enforced else None,
            },
            "groq": {
                "upstream_remaining": None,
                "speech": _provider(statuses["groq_asr"], enforced),
                "extraction_fallback": _provider(statuses["groq_llm"], enforced),
            },
            "voxide": {
                "status": "client_managed_not_integrated",
                "upstream_remaining": None,
                "remaining": [VOXIDE_REMAINING],
            },
        },
    }


def render_text(result: dict[str, Any]) -> str:
    """A short plain-text view for demo day."""
    lines = [
        f"Quota summary as of {result['as_of']} (enforced: {result['enforced']})",
        "Intake: "
        + (
            f"PAUSED by {', '.join(result['paused_by'])}, retry in about "
            f"{result['retry_after_seconds']} s"
            if result["intake_paused"]
            else "open"
        ),
    ]
    providers = result["providers"]
    sections = [
        ("Scholarxiv", providers["scholarxiv"]),
        ("Groq speech", providers["groq"]["speech"]),
        ("Groq extraction fallback", providers["groq"]["extraction_fallback"]),
        ("Voxide", providers["voxide"]),
    ]
    for title, provider in sections:
        lines.append(f"{title} [{provider['status']}]")
        lines.extend(f"  {remaining}" for remaining in provider["remaining"] or ["no limits set"])
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Local per-provider quota summary")
    parser.add_argument("--text", action="store_true", help="plain text instead of JSON")
    arguments = parser.parse_args(argv or [])
    config = load_settings()
    database = Database(config)
    try:
        result = await summary(database, config)
    except (SQLAlchemyError, OSError, TimeoutError):
        print("Quota summary unavailable: database request failed", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        await database.close()
    print(render_text(result) if arguments.text else json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
