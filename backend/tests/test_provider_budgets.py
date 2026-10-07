"""Database-free checks of the shared provider budgets (#22). PostgreSQL cases are in
``test_quotas.py`` and ``recovery/test_claim_extraction.py``."""

import asyncio
from types import SimpleNamespace

import pytest

from services.jobs.retries import RateLimited
from services.pipeline.llm import reserved_tokens
from services.provider_budgets import (
    GROQ_LLM_BUCKETS,
    LimitStatus,
    LlmAdmission,
    ProviderStatus,
    describe,
    groq_llm_buckets,
    llm_admission,
)
from services.providers.budget import SharedBudget, TokenBucket
from services.quota_summary import VOXIDE_REMAINING, render_text
from services.settings import Settings

URL = "postgresql+psycopg://local@localhost/test"


def settings(**changes):
    return Settings(database_url=URL, _env_file=None, **changes)


def test_groq_fallback_defaults_keep_headroom_under_the_documented_free_limits():
    config = settings()
    # Groq Free plan for openai/gpt-oss-20b: 30 RPM, 1K RPD, 8K TPM, 200K TPD.
    assert config.groq_llm_requests_per_minute == 27
    assert config.groq_llm_requests_per_day == 900
    assert config.groq_llm_tokens_per_minute == 7200
    assert config.groq_llm_tokens_per_day == 180000
    assert config.quota_provider_reserve_fraction == 0.05
    buckets = groq_llm_buckets(SimpleNamespace(), config)
    assert [(bucket.name, bucket.per_hour, bucket.period_seconds) for bucket in buckets] == [
        ("groq_llm:requests_minute", 27, 60),
        ("groq_llm:requests_day", 900, 86400),
        ("groq_llm:tokens_minute", 7200, 60),
        ("groq_llm:tokens_day", 180000, 86400),
    ]
    assert buckets[0].per_second == pytest.approx(27 / 60)
    assert len(GROQ_LLM_BUCKETS) == 4
    with pytest.raises(ValueError):
        settings(quota_provider_reserve_fraction=1)


def test_admission_is_opt_in_and_charges_each_provider_its_own_units():
    assert llm_admission(None) is None
    assert llm_admission(settings()) is None
    admission = llm_admission(settings(quotas_enabled=True))
    assert isinstance(admission, LlmAdmission)
    assert LlmAdmission.costs("scholarxiv", 5000, 2048) == [1.0]
    tokens = float(reserved_tokens(5000, 2048))
    assert tokens == 5000 + 2048 + 256
    assert LlmAdmission.costs("groq", 5000, 2048) == [1.0, 1.0, tokens, tokens]
    database = SimpleNamespace()
    budget = admission.budget(database, "scholarxiv")
    assert [bucket.name for bucket in budget.buckets] == ["scholarxiv"]
    assert budget.buckets[0].per_hour == 1000
    assert len(admission.budget(database, "groq").buckets) == 4
    with pytest.raises(ValueError):
        admission.budget(database, "voxide")


def test_shared_budget_validates_and_clamps_costs():
    database = SimpleNamespace()
    budget = SharedBudget(
        database,
        (TokenBucket(database, "b", 10, period_seconds=60), TokenBucket(database, "a", 5)),
    )
    # Oversized requests wait for a full bucket instead of failing forever; zero is skipped.
    assert [(bucket.name, cost) for bucket, cost in budget.charges([25, 0])] == [("b", 10.0)]
    assert [bucket.name for bucket, _ in budget.charges([1, 1])] == ["a", "b"]
    for costs in ([1], [1, -1], [float("nan"), 1], [1, float("inf")]):
        with pytest.raises(ValueError):
            budget.charges(costs)


async def test_shared_budget_waits_all_or_nothing_and_gives_up_past_the_limit(monkeypatch):
    waits = [2.0, 0.0]
    taken = []
    slept = []

    async def take(self, charged):
        taken.append(charged)
        return waits.pop(0)

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(SharedBudget, "_take", take)
    database = SimpleNamespace()
    budget = SharedBudget(
        database, (TokenBucket(database, "x", 10),), max_wait_seconds=5, sleep=sleep
    )
    await budget.acquire([1])
    assert len(taken) == 2
    assert 2.0 <= slept[0] <= 2.0 + 0.25 * 2.0 + 0.05
    waits.append(6.0)
    slept.clear()
    with pytest.raises(RateLimited) as limited:
        await budget.acquire([1])
    assert limited.value.retry_after_seconds == 6.0
    assert not slept
    taken.clear()
    await budget.acquire([0])
    assert not taken


def limit(available, reserve, recover, capacity=100, period=3600):
    return LimitStatus("bucket", "requests", capacity, period, available, reserve, recover)


def test_provider_status_pauses_only_when_configured_and_reports_the_longest_wait():
    status = ProviderStatus(
        "groq_asr",
        True,
        "local_rolling_ledger",
        (limit(10, 5, 0), limit(1, 5, 120)),
        hold_seconds=30,
    )
    assert status.retry_after_seconds == 120
    assert status.near_exhaustion and status.pauses_intake
    held = ProviderStatus("groq_asr", True, "local_rolling_ledger", (limit(10, 5, 0),), 300)
    assert held.retry_after_seconds == 300 and held.pauses_intake
    unconfigured = ProviderStatus("groq_llm", False, "local_token_bucket", (limit(0, 5, 60),))
    assert unconfigured.near_exhaustion and not unconfigured.pauses_intake
    idle = ProviderStatus("scholarxiv", True, "local_token_bucket", (limit(None, 50, 0),))
    assert idle.retry_after_seconds == 0 and not idle.pauses_intake


def test_remaining_balances_are_described_as_unknown_local_estimates():
    assert describe(limit(None, 5, 0, capacity=1000)) == (
        "unknown (local estimate: 1000 of 1000 requests/hour)"
    )
    assert describe(LimitStatus("x", "audio_seconds", 7200, 86400, -12.5, 360, 30)) == (
        "unknown (local estimate: 0 of 7200 audio seconds/day)"
    )
    assert describe(LimitStatus("x", "tokens", 7200, 60, 99.9, 360, 3)) == (
        "unknown (local estimate: 99 of 7200 tokens/minute)"
    )


def test_demo_text_names_the_paused_providers_and_every_unknown_balance():
    provider = {"status": "configured", "remaining": ["unknown (local estimate: 1 of 2 x/hour)"]}
    result = {
        "as_of": "2026-10-09T09:00:00+00:00",
        "enforced": True,
        "intake_paused": True,
        "paused_by": ["groq_asr"],
        "retry_after_seconds": 600,
        "providers": {
            "scholarxiv": provider,
            "groq": {
                "speech": provider,
                "extraction_fallback": {"status": "not_configured", "remaining": []},
            },
            "voxide": {"status": "client_managed_not_integrated", "remaining": [VOXIDE_REMAINING]},
        },
    }
    text = render_text(result)
    assert "PAUSED by groq_asr, retry in about 600 s" in text
    assert "Groq extraction fallback [not_configured]\n  no limits set" in text
    assert VOXIDE_REMAINING in text
    assert VOXIDE_REMAINING.startswith("unknown (local estimate:")
    result.update(intake_paused=False, paused_by=[])
    assert "Intake: open" in render_text(result)


async def test_release_refunds_even_when_the_caller_is_cancelled(monkeypatch):
    started = asyncio.Event()
    refunded = []

    async def refund(self, database, provider, costs):
        started.set()
        await asyncio.sleep(0.05)
        refunded.append((provider, costs))

    monkeypatch.setattr(LlmAdmission, "refund", refund)
    admission = LlmAdmission(settings(quotas_enabled=True))
    task = asyncio.create_task(admission.release(SimpleNamespace(), "scholarxiv", [1.0]))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0.1)
    assert refunded == [("scholarxiv", [1.0])]

    async def broken(self, database, provider, costs):
        raise OSError("database unavailable")

    monkeypatch.setattr(LlmAdmission, "refund", broken)
    # A failed refund never hides the caller's original error.
    await admission.release(SimpleNamespace(), "groq", [1.0, 1.0, 5.0, 5.0])
