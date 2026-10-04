import asyncio
import uuid

import pytest
from sqlalchemy import text

from services.database import Database
from services.jobs.queue import (
    CancelOutcome,
    Enqueued,
    JobQueue,
    Lease,
    LeaseLost,
    PublishRejected,
    StageKey,
)
from services.jobs.retries import RetryClass
from services.jobs.states import JobState, JobStatus
from services.settings import Settings


def key(stage=None):
    return StageKey(1, stage or "stage-" + uuid.uuid4().hex[:8], uuid.uuid4().hex)


@pytest.fixture
async def queue(database_url):
    database = Database(Settings(database_url=database_url, _env_file=None))
    try:
        yield JobQueue(database)
    finally:
        await database.close()
    assert database.engine.pool.checkedout() == 0


async def enqueue(queue, stage_key=None, payload=None):
    stage_key = stage_key or key()
    async with queue.database.engine.begin() as connection:
        enqueued = await queue.enqueue(connection, stage_key, payload or {"input": "x"})
    return stage_key, enqueued.job_id


async def test_enqueue_is_idempotent_per_stage_key(queue):
    stage_key, job_id = await enqueue(queue)
    async with queue.database.engine.begin() as connection:
        again = await queue.enqueue(connection, stage_key, {"input": "other"})
    assert again == Enqueued(job_id, created=False)
    record = await queue.get(job_id)
    assert record.status == JobStatus(JobState.QUEUED)
    assert record.key == stage_key


async def test_enqueue_joins_the_callers_transaction(queue):
    stage_key = key()
    with pytest.raises(RuntimeError, match="business record"):
        async with queue.database.engine.begin() as connection:
            await queue.enqueue(connection, stage_key, {})
            raise RuntimeError("business record write failed")
    async with queue.database.engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT count(*) FROM jobs WHERE input_hash = :h"), {"h": stage_key.input_hash}
        )
    assert count == 0, "The queue row must roll back with the business record"


async def test_claim_orders_by_availability_and_skips_other_stages(queue):
    first, first_id = await enqueue(queue, key("stage-order"))
    second, second_id = await enqueue(queue, key("stage-order"))
    await enqueue(queue, key("stage-other"))
    claimed = await queue.claim("w1", ["stage-order"], 30)
    assert claimed.id == first_id
    assert claimed.lease.fencing_token == 1
    assert claimed.attempts == 1
    assert claimed.payload == {"input": "x"}
    assert (await queue.claim("w2", ["stage-order"], 30)).id == second_id
    assert await queue.claim("w3", ["stage-order"], 30) is None
    assert await queue.claim("w3", [], 30) is None


async def test_concurrent_claims_never_hand_one_job_to_two_workers(queue):
    stage = "stage-concurrent"
    for _ in range(6):
        await enqueue(queue, key(stage))
    claims = await asyncio.gather(*(queue.claim(f"w{i}", [stage], 30) for i in range(10)))
    ids = [claim.id for claim in claims if claim is not None]
    assert len(ids) == 6
    assert len(set(ids)) == 6


async def full_claim(queue, worker="w1", lease_seconds=30, stage_key=None):
    stage_key, job_id = await enqueue(queue, stage_key)
    claimed = await queue.claim(worker, [stage_key.stage], lease_seconds)
    assert claimed.id == job_id
    return stage_key, claimed


async def test_publish_once_then_duplicate_publish_is_rejected(queue):
    stage_key, claimed = await full_claim(queue)
    assert await queue.start(claimed.lease)
    published = await queue.publish(claimed.lease, {"ok": True})
    assert published.key == stage_key
    with pytest.raises(PublishRejected, match="the job is published"):
        await queue.publish(claimed.lease, {"ok": True})
    results = await queue.published(stage_key)
    assert len(results) == 1
    assert results[0].fencing_token == 1
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.PUBLISHED)
    assert record.lease_owner is None


async def test_publish_requires_running_state(queue):
    _, claimed = await full_claim(queue)
    with pytest.raises(PublishRejected, match="the job is leased"):
        await queue.publish(claimed.lease, {})


async def test_stale_fencing_token_cannot_publish_or_mutate(queue):
    stage_key, stale = await full_claim(queue, lease_seconds=0.05)
    assert await queue.start(stale.lease)
    await asyncio.sleep(0.1)
    fresh = await queue.claim("w2", [stage_key.stage], 30)
    assert fresh.id == stale.id
    assert fresh.lease.fencing_token == 2
    assert fresh.attempts == 2
    with pytest.raises(PublishRejected, match="fencing token changed"):
        await queue.publish(stale.lease, {"from": "stale"})
    with pytest.raises(LeaseLost):
        await queue.heartbeat(stale.lease, 30)
    assert not await queue.fail(stale.lease, "late")
    assert not await queue.release(stale.lease)
    assert not await queue.cancel(stale.lease)
    assert await queue.start(fresh.lease)
    await queue.publish(fresh.lease, {"from": "fresh"})
    assert [r.result for r in await queue.published(stage_key)] == [{"from": "fresh"}]


async def test_publish_after_cancel_is_rejected(queue):
    stage_key, claimed = await full_claim(queue)
    assert await queue.start(claimed.lease)
    assert await queue.request_cancel(claimed.id) is CancelOutcome.REQUESTED
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.RUNNING, cancel_requested=True)
    with pytest.raises(PublishRejected, match="cancellation was requested"):
        await queue.publish(claimed.lease, {})
    assert await queue.heartbeat(claimed.lease, 30) is True
    assert await queue.cancel(claimed.lease)
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.CANCELLED, cancel_requested=True)
    assert record.generation == 1
    with pytest.raises(PublishRejected, match="cancelled \\(generation changed\\)"):
        await queue.publish(claimed.lease, {})
    assert await queue.published(stage_key) == []
    assert await queue.request_cancel(claimed.id) is CancelOutcome.NOT_CANCELLABLE


async def test_queued_cancel_is_effective_immediately_and_blocks_start(queue):
    _, job_id = await enqueue(queue)
    assert await queue.request_cancel(job_id) is CancelOutcome.EFFECTIVE
    record = await queue.get(job_id)
    assert record.status == JobStatus(JobState.CANCELLED, cancel_requested=True)
    assert await queue.claim("w1", [record.key.stage], 30) is None

    _, leased = await full_claim(queue)
    assert await queue.request_cancel(leased.id) is CancelOutcome.REQUESTED
    assert not await queue.start(leased.lease)
    assert await queue.cancel(leased.lease)
    assert await queue.request_cancel(uuid.uuid4()) is CancelOutcome.NOT_CANCELLABLE


async def test_publish_after_delete_is_rejected_and_results_are_removed(queue):
    stage_key, claimed = await full_claim(queue)
    assert await queue.start(claimed.lease)
    assert await queue.delete(claimed.id)
    with pytest.raises(PublishRejected, match="deleted \\(generation changed\\)"):
        await queue.publish(claimed.lease, {"resurrected": True})
    assert await queue.published(stage_key) == []
    assert not await queue.delete(claimed.id)
    assert not await queue.delete(uuid.uuid4())

    other_key, other = await full_claim(queue)
    assert await queue.start(other.lease)
    await queue.publish(other.lease, {"kept": False})
    assert await queue.delete(other.id)
    assert await queue.published(other_key) == []
    record = await queue.get(other.id)
    assert record.status.state is JobState.DELETED
    assert record.generation == 1
    assert record.lease_owner is None


async def test_release_returns_to_queue_or_makes_cancellation_effective(queue):
    stage_key, claimed = await full_claim(queue)
    assert await queue.release(claimed.lease)
    assert (await queue.get(claimed.id)).status == JobStatus(JobState.QUEUED)
    again = await queue.claim("w2", [stage_key.stage], 30)
    assert again.lease.fencing_token == 2
    assert await queue.request_cancel(again.id) is CancelOutcome.REQUESTED
    assert await queue.release(again.lease)
    record = await queue.get(again.id)
    assert record.status == JobStatus(JobState.CANCELLED, cancel_requested=True)
    assert record.generation == 1


async def test_expired_lease_with_cancel_request_becomes_cancelled(queue):
    stage_key, claimed = await full_claim(queue, lease_seconds=0.05)
    assert await queue.request_cancel(claimed.id) is CancelOutcome.REQUESTED
    await asyncio.sleep(0.1)
    assert await queue.claim("w2", [stage_key.stage], 30) is None
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.CANCELLED, cancel_requested=True)
    assert record.generation == 1


async def test_fail_records_reason_and_heartbeat_extends(queue):
    worker = "w-" + uuid.uuid4().hex[:8]
    _, claimed = await full_claim(queue, worker=worker, lease_seconds=0.2)
    assert await queue.start(claimed.lease)
    assert await queue.owned_leases(worker) == [claimed.id]
    assert await queue.heartbeat(claimed.lease, 30) is False
    assert (await queue.get(claimed.id)).lease_expires_at > claimed.lease.expires_at
    assert await queue.fail(claimed.lease, "ValueError")
    record = await queue.get(claimed.id)
    assert record.status.state is JobState.FAILED
    assert record.failure == "ValueError"
    assert await queue.owned_leases(worker) == []


async def test_unknown_job_reads_as_missing(queue):
    missing = uuid.uuid4()
    assert await queue.get(missing) is None
    with pytest.raises(PublishRejected, match="no longer exists"):
        await queue.publish(Lease(missing, "w1", 1, 0, None), {})


async def test_retry_schedules_availability_and_counts_per_class(queue):
    stage_key, claimed = await full_claim(queue)
    assert claimed.retry_counts == {}
    assert claimed.provider_request_id is None
    assert await queue.start(claimed.lease)
    assert await queue.retry(claimed.lease, RetryClass.TRANSIENT, 0.3)
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.QUEUED)
    assert record.retry_class is RetryClass.TRANSIENT
    assert record.retry_counts == {"transient": 1}
    assert record.lease_owner is None
    assert await queue.claim("w2", [stage_key.stage], 30) is None, "Not available yet"
    await asyncio.sleep(0.35)
    again = await queue.claim("w2", [stage_key.stage], 30)
    assert again.id == claimed.id
    assert again.retry_counts == {"transient": 1}
    assert again.attempts == 2
    assert await queue.retry(again.lease, RetryClass.RATE_LIMITED, 0)
    third = await queue.claim("w3", [stage_key.stage], 30)
    assert third.retry_counts == {"transient": 1, "rate_limited": 1}
    assert await queue.retry(third.lease, RetryClass.TRANSIENT, 0)
    assert (await queue.get(claimed.id)).retry_counts == {"transient": 2, "rate_limited": 1}


async def test_retry_with_pending_cancel_becomes_cancelled(queue):
    _, claimed = await full_claim(queue)
    assert await queue.request_cancel(claimed.id) is CancelOutcome.REQUESTED
    assert await queue.retry(claimed.lease, RetryClass.TRANSIENT, 0)
    record = await queue.get(claimed.id)
    assert record.status == JobStatus(JobState.CANCELLED, cancel_requested=True)
    assert record.generation == 1
    assert record.retry_counts == {"transient": 1}


async def test_stale_lease_cannot_retry_or_record_a_request_id(queue):
    stage_key, stale = await full_claim(queue, lease_seconds=0.05)
    await asyncio.sleep(0.1)
    fresh = await queue.claim("w2", [stage_key.stage], 30)
    assert fresh.id == stale.id
    assert not await queue.retry(stale.lease, RetryClass.TRANSIENT, 0)
    with pytest.raises(LeaseLost):
        await queue.record_request_id(stale.lease, "req-stale")
    assert (await queue.get(stale.id)).provider_request_id is None
    assert (await queue.get(stale.id)).status.state is JobState.LEASED


async def test_fail_records_the_retry_class(queue):
    _, claimed = await full_claim(queue)
    assert await queue.fail(claimed.lease, "NonRetriableInput", RetryClass.NON_RETRIABLE_INPUT)
    record = await queue.get(claimed.id)
    assert record.status.state is JobState.FAILED
    assert record.failure == "NonRetriableInput"
    assert record.retry_class is RetryClass.NON_RETRIABLE_INPUT


async def test_request_id_is_recorded_and_found_across_re_leases(queue):
    stage_key, claimed = await full_claim(queue, lease_seconds=0.05)
    request_id = "req-" + uuid.uuid4().hex
    assert await queue.find_by_request_id(request_id) is None
    await queue.record_request_id(claimed.lease, request_id)
    found = await queue.find_by_request_id(request_id)
    assert found.id == claimed.id
    assert found.provider_request_id == request_id
    await asyncio.sleep(0.1)
    re_leased = await queue.claim("w2", [stage_key.stage], 30)
    assert re_leased.provider_request_id == request_id, "The next attempt must reconcile"
    assert await queue.start(re_leased.lease)
    assert await queue.retry(re_leased.lease, RetryClass.UNKNOWN_OUTCOME, 0)
    assert (await queue.get(claimed.id)).provider_request_id == request_id
    assert await queue.delete(claimed.id)
    assert (await queue.find_by_request_id(request_id)).status.state is JobState.DELETED
