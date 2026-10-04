"""Negative tests for every retry class, executed through the worker against PostgreSQL."""

import asyncio
import logging

from services.jobs.retries import (
    InvalidModelSchema,
    NonRetriableInput,
    RateLimited,
    RetryClass,
    RetryPolicy,
    Transient,
    UnknownOutcome,
)
from services.jobs.states import JobState


def fast_policy(**overrides):
    values = {
        "transient_attempts": 2,
        "rate_limited_attempts": 2,
        "schema_repair_attempts": 1,
        "unknown_outcome_attempts": 1,
        "backoff_seconds": 0.05,
        "max_backoff_seconds": 0.2,
        "jitter": lambda: 1.0,
    }
    values.update(overrides)
    return RetryPolicy(**values)


async def test_transient_retries_with_backoff_then_succeeds(harness, caplog):
    caplog.set_level(logging.INFO)

    async def flaky(job, context):
        harness.calls.append((job.attempts, dict(job.retry_counts)))
        if job.attempts <= 2:
            raise Transient("upstream 503 with private details")
        return {"attempt": job.attempts}

    worker = harness.worker(flaky, worker_id="transient", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await worker.stop()
    assert harness.calls == [(1, {}), (2, {"transient": 1}), (3, {"transient": 2})]
    assert record.retry_counts == {"transient": 2}
    assert record.retry_class is RetryClass.TRANSIENT, "The last scheduled class is kept"
    assert "retry 1 (transient) scheduled in 0.05s" in caplog.text
    assert "retry 2 (transient) scheduled in 0.10s" in caplog.text
    assert "private details" not in caplog.text
    await harness.assert_invariants(key, job_id, worker_ids=["transient"])


async def test_transient_exhaustion_fails_with_the_class_recorded(harness, caplog):
    async def always_transient(job, context):
        harness.calls.append(job.attempts)
        raise Transient()

    worker = harness.worker(always_transient, worker_id="exhausted", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await worker.stop()
    assert harness.calls == [1, 2, 3], "Two retries, then the third outcome exhausts the cap"
    assert record.retry_counts == {"transient": 2}
    await harness.assert_failed(key, job_id, RetryClass.TRANSIENT, failure="Transient")
    assert "(transient, retries exhausted after 2)" in caplog.text


async def test_rate_limited_waits_for_the_providers_hint(harness, caplog):
    caplog.set_level(logging.INFO)
    started = []

    async def limited(job, context):
        started.append(asyncio.get_running_loop().time())
        if job.attempts == 1:
            raise RateLimited(retry_after_seconds=0.4)
        return {"attempt": job.attempts}

    worker = harness.worker(
        limited, worker_id="limited", retry_policy=fast_policy(max_backoff_seconds=5)
    )
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await worker.stop()
    assert len(started) == 2
    assert started[1] - started[0] >= 0.4, "The hint was honoured"
    assert "retry 1 (rate_limited) scheduled in 0.40s" in caplog.text
    assert record.retry_counts == {"rate_limited": 1}
    await harness.assert_invariants(key, job_id, worker_ids=["limited"])


async def test_rate_limited_hint_is_clamped_and_exhaustion_fails(harness, caplog):
    caplog.set_level(logging.INFO)

    async def always_limited(job, context):
        raise RateLimited(retry_after_seconds=3600)

    worker = harness.worker(always_limited, worker_id="clamped", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await worker.stop()
    assert "retry 1 (rate_limited) scheduled in 0.20s" in caplog.text, "Clamped to the maximum"
    assert record.retry_counts == {"rate_limited": 2}
    await harness.assert_failed(key, job_id, RetryClass.RATE_LIMITED, failure="RateLimited")


async def test_non_retriable_input_fails_on_the_first_attempt(harness, caplog):
    async def rejects(job, context):
        harness.calls.append(job.attempts)
        raise NonRetriableInput("unsupported media")

    worker = harness.worker(rejects, worker_id="non-retriable", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await asyncio.sleep(0.3)
    await worker.stop()
    assert harness.calls == [1], "Never retried"
    assert record.retry_counts == {}
    await harness.assert_failed(
        key, job_id, RetryClass.NON_RETRIABLE_INPUT, failure="NonRetriableInput"
    )
    assert "unsupported media" not in caplog.text
    assert "(non_retriable_input, retries exhausted after 0)" in caplog.text


async def test_invalid_model_schema_gets_a_bounded_repair_attempt(harness):
    async def repairs(job, context):
        repairs_so_far = job.retry_counts.get("invalid_model_schema", 0)
        harness.calls.append(repairs_so_far)
        if repairs_so_far == 0:
            raise InvalidModelSchema("output did not validate")
        return {"repaired": True, "repairs": repairs_so_far}

    worker = harness.worker(repairs, worker_id="repair", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await worker.stop()
    assert harness.calls == [0, 1], "The handler saw the repair count and changed strategy"
    assert record.retry_counts == {"invalid_model_schema": 1}
    result = await harness.assert_invariants(key, job_id, worker_ids=["repair"])
    assert result.result == {"repaired": True, "repairs": 1}


async def test_invalid_model_schema_exhaustion_fails(harness):
    async def never_valid(job, context):
        harness.calls.append(job.attempts)
        raise InvalidModelSchema()

    worker = harness.worker(never_valid, worker_id="unrepairable", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await worker.stop()
    assert harness.calls == [1, 2], "One repair attempt, then failure"
    assert record.retry_counts == {"invalid_model_schema": 1}
    await harness.assert_failed(
        key, job_id, RetryClass.INVALID_MODEL_SCHEMA, failure="InvalidModelSchema"
    )


async def test_unknown_outcome_reconciles_by_request_id_and_never_re_calls(harness):
    handler = harness.provider_handler()
    worker = harness.worker(handler, worker_id="reconcile", retry_policy=fast_policy())
    await worker.start()
    # The first provider call times out after the provider accepted the work.
    harness.provider.timeout_next = True
    key, job_id = await harness.enqueue({"input": "slow"})
    record = await harness.wait_for_state(job_id, JobState.PUBLISHED)
    await worker.stop()
    request_id = f"req-{job_id.hex[:8]}-1"
    assert harness.provider.calls == [request_id], "Exactly one provider call"
    assert harness.provider.lookups == [request_id], "The retry reconciled, it did not re-call"
    assert record.retry_counts == {"unknown_outcome": 1}
    assert record.provider_request_id == request_id
    result = await harness.assert_invariants(key, job_id, worker_ids=["reconcile"])
    assert result.result["request_id"] == request_id


async def test_unknown_outcome_exhaustion_fails_without_re_calling(harness):
    async def never_resolves(job, context):
        harness.calls.append(job.attempts)
        if job.provider_request_id is None:
            await context.record_request_id(f"req-{job.id.hex[:8]}")
            await harness.provider.call(f"req-{job.id.hex[:8]}", job.payload)
        raise UnknownOutcome("still unknown")

    worker = harness.worker(never_resolves, worker_id="unresolved", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await worker.stop()
    assert harness.calls == [1, 2], "One reconciliation attempt, then failure"
    assert len(harness.provider.calls) == 1, "The provider was never called again"
    assert record.retry_counts == {"unknown_outcome": 1}
    await harness.assert_failed(key, job_id, RetryClass.UNKNOWN_OUTCOME, failure="UnknownOutcome")


async def test_unknown_outcome_without_a_recorded_request_id_fails_immediately(harness, caplog):
    async def forgot_the_id(job, context):
        harness.calls.append(job.attempts)
        await harness.provider.call(f"req-{job.id.hex[:8]}", job.payload)
        raise UnknownOutcome("timed out, no id recorded")

    worker = harness.worker(forgot_the_id, worker_id="forgetful", retry_policy=fast_policy())
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.FAILED)
    await worker.stop()
    assert harness.calls == [1], "Re-running would silently repeat the provider call"
    assert len(harness.provider.calls) == 1
    assert record.retry_counts == {}
    assert record.provider_request_id is None
    await harness.assert_failed(key, job_id, RetryClass.UNKNOWN_OUTCOME, failure="UnknownOutcome")
    assert "(unknown_outcome, retries exhausted after 0)" in caplog.text


async def test_retry_with_a_pending_cancellation_becomes_effective(harness):
    async def transient_then_cancel(job, context):
        assert await harness.queue.request_cancel(job.id) == "requested"
        raise Transient()

    worker = harness.worker(
        transient_then_cancel, worker_id="cancel-retry", retry_policy=fast_policy()
    )
    await worker.start()
    key, job_id = await harness.enqueue()
    record = await harness.wait_for_state(job_id, JobState.CANCELLED)
    await worker.stop()
    assert record.status.cancel_requested
    assert record.generation == 1
    assert record.retry_counts == {"transient": 1}
    assert await harness.queue.published(key) == []
