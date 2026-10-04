import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update
from test_intake_api import CONTRACT_VALIDATOR, ContractInvalid, assert_error

from services.api.main import create_app
from services.api.routes.jobs import CancelResponse, DeleteResponse
from services.jobs.models import jobs
from services.jobs.queue import JobQueue, PublishRejected
from services.jobs.states import JobState
from services.pipeline.intake import intake_stage_key

FIXTURES = Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/jobs"
URL_BODY = {"source": {"kind": "url", "url": "https://example.com/watch?v=job-actions"}}


@pytest.fixture
async def job_client(harness):
    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        yield client


async def row(harness, job_id):
    async with harness.control.engine.connect() as connection:
        return (await connection.execute(select(jobs).where(jobs.c.id == job_id))).one()


def assert_fixture(response, name, job_id):
    fixture = json.loads((FIXTURES / f"{name}.json").read_text())
    expected = fixture["response"]
    if "job_id" in expected:
        expected["job_id"] = str(job_id)
    else:
        expected["request_id"] = response.headers["X-Request-Id"]
    assert response.status_code == fixture["status"], response.text
    assert response.json() == expected
    if response.status_code >= 400:
        CONTRACT_VALIDATOR.validate(response.json(), "error.schema.json")
    else:
        operation = fixture["operation"]
        CONTRACT_VALIDATOR.validate(response.json(), f"job-{operation}-response.schema.json")
        model = CancelResponse if operation == "cancel" else DeleteResponse
        assert model.model_validate_json(response.content).model_dump(mode="json") == expected


async def test_queued_cancel_replays_without_mutation(harness, job_client):
    key, job_id = await harness.enqueue()
    path = f"/v1/jobs/{job_id}/cancel"
    first = await job_client.post(path)
    assert_fixture(first, "cancel-effective", job_id)
    original = await row(harness, job_id)
    for response in await asyncio.gather(*(job_client.post(path) for _ in range(4))):
        assert_fixture(response, "cancel-effective", job_id)
    assert await row(harness, job_id) == original
    assert original.generation == 1
    assert await harness.queue.published(key) == []


async def test_requested_receipt_survives_acknowledgement_and_api_restart(harness, job_client):
    _, job_id = await harness.enqueue()
    claim = await harness.queue.claim("worker", [harness.stage], 5)
    assert claim.id == job_id
    first = await job_client.post(f"/v1/jobs/{job_id}/cancel")
    assert_fixture(first, "cancel-requested", job_id)
    assert await harness.queue.release(claim.lease)
    snapshot = await row(harness, job_id)
    assert snapshot.state == "cancelled"
    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        replay = await client.post(f"/v1/jobs/{job_id}/cancel")
    assert_fixture(replay, "cancel-requested", job_id)
    assert await row(harness, job_id) == snapshot


@pytest.mark.parametrize("state", ["published", "failed", "cancelled"])
async def test_terminal_cancellation(harness, job_client, state):
    _, job_id = await harness.enqueue()
    async with harness.control.engine.begin() as connection:
        await connection.execute(update(jobs).where(jobs.c.id == job_id).values(state=state))
    name = "cancel-effective" if state == "cancelled" else "not-cancellable"
    assert_fixture(await job_client.post(f"/v1/jobs/{job_id}/cancel"), name, job_id)
    snapshot = await row(harness, job_id)
    assert_fixture(await job_client.post(f"/v1/jobs/{job_id}/cancel"), name, job_id)
    assert await row(harness, job_id) == snapshot


async def test_concurrent_delete_removes_published_result_and_replays(harness, job_client):
    key, job_id = await harness.enqueue({"private": "synthetic payload"})
    claim = await harness.queue.claim("publisher", [harness.stage], 5)
    await harness.queue.start(claim.lease)
    await harness.queue.publish(claim.lease, {"private": "synthetic result"})
    path = f"/v1/jobs/{job_id}"
    responses = await asyncio.gather(*(job_client.delete(path) for _ in range(4)))
    for response in responses:
        assert_fixture(response, "delete-complete", job_id)
    snapshot = await row(harness, job_id)
    assert snapshot.generation == 1
    assert_fixture(await job_client.delete(path), "delete-complete", job_id)
    assert await row(harness, job_id) == snapshot
    assert_error(await job_client.post(path + "/cancel"), 404, "NOT_FOUND")
    await harness.assert_deleted(key, job_id)


@pytest.mark.parametrize("method,suffix", [("POST", "/cancel"), ("DELETE", "")])
async def test_authentication_validation_and_unowned_legacy_jobs(
    harness, job_client, method, suffix
):
    _, job_id = await harness.enqueue()
    path = f"/v1/jobs/{job_id}{suffix}"
    snapshot = await row(harness, job_id)
    assert_error(
        await job_client.request(method, path, headers={"Authorization": ""}),
        401,
        "AUTHENTICATION_REQUIRED",
    )
    assert_error(
        await job_client.request(method, path, headers={"Authorization": "Bearer invalid"}),
        401,
        "INVALID_CREDENTIAL",
    )
    for kwargs in (
        {"content": '{"owner_id":"forged"}'},
        {"content": "{}"},
        {"content": "not json"},
    ):
        assert_error(await job_client.request(method, path, **kwargs), 422, "VALIDATION_FAILED")
    for kwargs in (
        {"params": {"owner_id": str(harness.owner.id)}},
        {"headers": {"X-User-Id": str(harness.owner.id)}},
    ):
        assert_error(
            await job_client.request(method, path, **kwargs), 400, "CLIENT_IDENTITY_REJECTED"
        )
    assert_error(
        await job_client.request(method, f"/v1/jobs/not-a-uuid{suffix}"), 422, "VALIDATION_FAILED"
    )
    assert_error(
        await job_client.request(method, f"/v1/jobs/{uuid.uuid4()}{suffix}"), 404, "NOT_FOUND"
    )
    assert await row(harness, job_id) == snapshot
    async with harness.control.engine.begin() as connection:
        legacy = await harness.queue.enqueue(
            connection, harness.key(), {"owner_id": str(harness.owner.id)}
        )
    assert_error(
        await job_client.request(method, f"/v1/jobs/{legacy.job_id}{suffix}"), 404, "NOT_FOUND"
    )


async def test_cross_owner_stage_key_cannot_return_another_owners_job(harness):
    key, job_id = await harness.enqueue()
    async with harness.control.engine.begin() as connection:
        replay = await harness.queue.enqueue(connection, key, {}, owner_id=harness.owner.id)
        assert replay.job_id == job_id and not replay.created
    async with harness.control.engine.begin() as connection:
        with pytest.raises(ValueError, match="across job owners"):
            await harness.queue.enqueue(connection, key, {}, owner_id=harness.outsider.id)


async def test_intake_job_of_a_new_investigation_is_owned_by_its_creator(harness, job_client):
    """The production dispatcher enqueues with the investigation's owner, so the creator can
    cancel and delete the job through the API while another principal sees 404."""
    headers = {"Idempotency-Key": "job-actions-owned"}
    created = await job_client.post("/v1/investigations", json=URL_BODY, headers=headers)
    assert created.status_code == 202, created.text
    investigation_id = uuid.UUID(created.json()["id"])
    job_id = await harness.job_id_for(intake_stage_key(investigation_id))
    assert job_id is not None, "the intake job was not enqueued"
    harness.job_ids.append(job_id)
    assert (await row(harness, job_id)).owner_id == harness.owner.id

    replay = await job_client.post("/v1/investigations", json=URL_BODY, headers=headers)
    assert replay.status_code == 202 and replay.json() == created.json()
    assert await harness.job_id_for(intake_stage_key(investigation_id)) == job_id

    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app, outsider=True) as outsider:
        assert_error(await outsider.post(f"/v1/jobs/{job_id}/cancel"), 404, "NOT_FOUND")
        assert_error(await outsider.delete(f"/v1/jobs/{job_id}"), 404, "NOT_FOUND")
    assert (await row(harness, job_id)).state == JobState.QUEUED.value

    assert_fixture(await job_client.post(f"/v1/jobs/{job_id}/cancel"), "cancel-effective", job_id)
    after_cancel = await job_client.get(f"/v1/investigations/{investigation_id}")
    assert after_cancel.status_code == 200 and after_cancel.json()["state"] == "cancelled"
    assert_fixture(await job_client.delete(f"/v1/jobs/{job_id}"), "delete-complete", job_id)
    await harness.assert_deleted(intake_stage_key(investigation_id), job_id)
    after_delete = await job_client.get(f"/v1/investigations/{investigation_id}")
    assert after_delete.status_code == 200 and after_delete.json()["state"] == "cancelled"


async def test_failed_delete_rolls_back_tombstone_and_returns_shared_error(
    harness, job_client, monkeypatch
):
    _, job_id = await harness.enqueue()
    snapshot = await row(harness, job_id)
    original = JobQueue.delete

    async def fail_after_delete(self, job_id, *, connection=None):
        await original(self, job_id, connection=connection)
        raise TimeoutError("private database detail")

    monkeypatch.setattr(JobQueue, "delete", fail_after_delete)
    assert_error(await job_client.delete(f"/v1/jobs/{job_id}"), 503, "DATABASE_UNAVAILABLE")
    assert await row(harness, job_id) == snapshot


async def test_failed_cancel_rolls_back_state_and_receipt(harness, job_client, monkeypatch):
    _, job_id = await harness.enqueue()
    snapshot = await row(harness, job_id)
    original = JobQueue.request_cancel

    async def fail_after_cancel(self, job_id, *, connection=None):
        await original(self, job_id, connection=connection)
        raise TimeoutError("private database detail")

    monkeypatch.setattr(JobQueue, "request_cancel", fail_after_cancel)
    assert_error(await job_client.post(f"/v1/jobs/{job_id}/cancel"), 503, "DATABASE_UNAVAILABLE")
    assert await row(harness, job_id) == snapshot


async def test_concurrent_cancel_delete_always_leaves_tombstone(harness, job_client):
    key, job_id = await harness.enqueue()
    cancel, delete = await asyncio.gather(
        job_client.post(f"/v1/jobs/{job_id}/cancel"),
        job_client.delete(f"/v1/jobs/{job_id}"),
    )
    assert cancel.status_code in (200, 404)
    assert_fixture(delete, "delete-complete", job_id)
    await harness.assert_deleted(key, job_id)
    assert_error(await job_client.post(f"/v1/jobs/{job_id}/cancel"), 404, "NOT_FOUND")


async def test_api_cancel_mid_retrieval(harness, job_client):
    entered, proceed = asyncio.Event(), asyncio.Event()
    fetched = []

    async def retrieval(job, context):
        for seq in range(4):
            if seq == 2:
                entered.set()
                await proceed.wait()
            await context.heartbeat()
            fetched.append(seq)
        return {"chunks": fetched}

    worker = harness.worker(retrieval, job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    await asyncio.wait_for(entered.wait(), 5)
    try:
        assert_fixture(
            await job_client.post(f"/v1/jobs/{job_id}/cancel"), "cancel-requested", job_id
        )
    finally:
        proceed.set()
    await harness.wait_for_state(job_id, JobState.CANCELLED)
    await harness.wait_until(lambda: not worker.owned_leases)
    await worker.stop()
    assert fetched == [0, 1]
    assert await harness.queue.published(key) == []
    await harness.assert_no_cross_owner_read(job_id)


async def test_api_delete_with_delayed_provider_callback(harness, job_client):
    entered, proceed = asyncio.Event(), asyncio.Event()
    leases = []

    async def delayed(job, context):
        leases.append(job.lease)
        await context.record_request_id("req-" + str(job.id))
        result = await harness.provider.call("req-" + str(job.id), job.payload)
        entered.set()
        await proceed.wait()
        return result

    worker = harness.worker(delayed, job_lease_seconds=5)
    await worker.start()
    key, job_id = await harness.enqueue()
    await asyncio.wait_for(entered.wait(), 5)
    try:
        assert_fixture(await job_client.delete(f"/v1/jobs/{job_id}"), "delete-complete", job_id)
    finally:
        proceed.set()
    await harness.wait_until(lambda: not worker.owned_leases)
    await worker.stop()
    with pytest.raises(PublishRejected):
        await harness.queue.publish(leases[0], {"late": "synthetic callback"})
    await harness.assert_deleted(key, job_id)


@pytest.mark.parametrize(
    "schema,payload",
    [
        ("job-action-request", {}),
        ("job-action-request", {"job_id": "not-a-uuid"}),
        ("job-action-request", {"job_id": "00000000-0000-4000-8000-000000000075\n"}),
        (
            "job-cancel-response",
            {"job_id": "00000000-0000-4000-8000-000000000075", "cancellation": "done"},
        ),
        (
            "job-delete-response",
            {"job_id": "00000000-0000-4000-8000-000000000075", "state": "deleted"},
        ),
    ],
)
def test_invalid_job_contract_payloads_fail(schema, payload):
    with pytest.raises(ContractInvalid):
        CONTRACT_VALIDATOR.validate(payload, schema + ".schema.json")
