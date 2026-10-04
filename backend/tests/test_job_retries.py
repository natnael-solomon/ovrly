"""Retry-class policy decisions: caps, backoff, jitter, hints and the request-id rule."""

import pytest

from services.jobs.retries import (
    InvalidModelSchema,
    NonRetriableInput,
    RateLimited,
    RetryClass,
    RetryPolicy,
    Transient,
    UnknownOutcome,
)
from services.settings import Settings


def policy(jitter=1.0, **overrides):
    return RetryPolicy(jitter=lambda: jitter, **overrides)


def test_transient_backs_off_exponentially_until_the_cap():
    decisions = [
        policy(backoff_seconds=1, max_backoff_seconds=60, transient_attempts=3).decide(
            Transient(), {RetryClass.TRANSIENT.value: count}, request_id_recorded=False
        )
        for count in range(4)
    ]
    assert [d.delay_seconds for d in decisions] == [1, 2, 4, None]
    assert [d.count for d in decisions] == [1, 2, 3, 4]
    assert decisions[-1].exhausted
    assert all(d.retry_class is RetryClass.TRANSIENT for d in decisions)


def test_backoff_is_clamped_and_fully_jittered():
    clamped = policy(backoff_seconds=10, max_backoff_seconds=15)
    assert clamped.backoff(1) == 10
    assert clamped.backoff(2) == 15
    assert clamped.backoff(9) == 15
    jittered = policy(jitter=0.25, backoff_seconds=8, max_backoff_seconds=60)
    assert jittered.backoff(1) == 2
    assert jittered.backoff(3) == 8
    assert policy(jitter=0.0).backoff(5) == 0


def test_rate_limited_honours_the_hint_within_the_ceiling():
    limited = policy(max_backoff_seconds=30, rate_limited_attempts=2)
    assert limited.decide(RateLimited(7), {}, request_id_recorded=False).delay_seconds == 7
    assert limited.decide(RateLimited(90), {}, request_id_recorded=False).delay_seconds == 30
    assert limited.decide(RateLimited(-3), {}, request_id_recorded=False).delay_seconds == 0
    without_hint = limited.decide(RateLimited(), {}, request_id_recorded=False)
    assert without_hint.delay_seconds == 1
    exhausted = limited.decide(
        RateLimited(1), {RetryClass.RATE_LIMITED.value: 2}, request_id_recorded=False
    )
    assert exhausted.exhausted
    assert exhausted.retry_class is RetryClass.RATE_LIMITED


def test_non_retriable_input_is_never_scheduled():
    decision = policy().decide(NonRetriableInput(), {}, request_id_recorded=True)
    assert decision.exhausted
    assert decision.retry_class is RetryClass.NON_RETRIABLE_INPUT
    assert decision.count == 1
    assert RetryPolicy().cap(RetryClass.NON_RETRIABLE_INPUT) == 0


def test_schema_repair_is_bounded_separately_from_other_classes():
    repairing = policy(schema_repair_attempts=2)
    counts = {RetryClass.TRANSIENT.value: 10, RetryClass.INVALID_MODEL_SCHEMA.value: 1}
    assert not repairing.decide(InvalidModelSchema(), counts, request_id_recorded=False).exhausted
    counts[RetryClass.INVALID_MODEL_SCHEMA.value] = 2
    assert repairing.decide(InvalidModelSchema(), counts, request_id_recorded=False).exhausted


def test_unknown_outcome_requires_a_recorded_request_id():
    reconciling = policy(unknown_outcome_attempts=3)
    refused = reconciling.decide(UnknownOutcome(), {}, request_id_recorded=False)
    assert refused.exhausted, "Re-running without a request id would silently re-call"
    assert refused.retry_class is RetryClass.UNKNOWN_OUTCOME
    scheduled = reconciling.decide(UnknownOutcome(), {}, request_id_recorded=True)
    assert scheduled.delay_seconds == 1
    capped = reconciling.decide(
        UnknownOutcome(), {RetryClass.UNKNOWN_OUTCOME.value: 3}, request_id_recorded=True
    )
    assert capped.exhausted


def test_zero_caps_fail_on_the_first_outcome():
    strict = policy(transient_attempts=0, unknown_outcome_attempts=0)
    assert strict.decide(Transient(), {}, request_id_recorded=False).exhausted
    assert strict.decide(UnknownOutcome(), {}, request_id_recorded=True).exhausted


def test_policy_is_built_from_settings():
    settings = Settings(
        database_url="postgresql+psycopg://ovrly:test-only@127.0.0.1:55432/ovrly",
        _env_file=None,
        job_retry_transient_attempts=7,
        job_retry_rate_limited_attempts=6,
        job_retry_schema_repair_attempts=4,
        job_retry_unknown_outcome_attempts=5,
        job_retry_backoff_seconds=2,
        job_retry_max_backoff_seconds=20,
    )
    built = RetryPolicy.from_settings(settings)
    assert (built.transient_attempts, built.rate_limited_attempts) == (7, 6)
    assert (built.schema_repair_attempts, built.unknown_outcome_attempts) == (4, 5)
    assert (built.backoff_seconds, built.max_backoff_seconds) == (2, 20)
    assert 0 <= built.jitter() < 1, "The default jitter is a unit random draw"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (Transient(), RetryClass.TRANSIENT),
        (RateLimited(), RetryClass.RATE_LIMITED),
        (NonRetriableInput(), RetryClass.NON_RETRIABLE_INPUT),
        (InvalidModelSchema(), RetryClass.INVALID_MODEL_SCHEMA),
        (UnknownOutcome(), RetryClass.UNKNOWN_OUTCOME),
    ],
)
def test_each_exception_declares_its_class(error, expected):
    assert error.retry_class is expected
