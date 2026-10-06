"""Investigation routes emit the contract read model (BE-10, #33): processing_status, job and
report derived from the queue and the published versions, validated against the schema."""

import uuid

import httpx
import pytest
from sqlalchemy import update
from test_intake_api import CONTRACT_VALIDATOR, URL_BODY, guest
from test_reports_api import correction, publish

from services.api.main import create_app
from services.jobs.models import jobs
from services.pipeline.intake import intake_stage_key
from services.settings import Settings

SCHEMA = "investigation.schema.json"


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(database_url=database_url, storage_dir=tmp_path / "uploads", _env_file=None)
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def create(client, headers):
    response = await client.post(
        "/v1/investigations",
        json=URL_BODY,
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 202, response.text
    CONTRACT_VALIDATOR.validate(response.json(), SCHEMA)
    return response.json()


async def fetch(client, headers, investigation_id):
    response = await client.get(f"/v1/investigations/{investigation_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    CONTRACT_VALIDATOR.validate(body, SCHEMA)
    listing = await client.get("/v1/investigations", headers=headers)
    listed = next(item for item in listing.json()["items"] if item["id"] == investigation_id)
    CONTRACT_VALIDATOR.validate(listed, SCHEMA)
    assert listed == body
    return body


async def set_intake_state(app, investigation_id, state):
    key = intake_stage_key(uuid.UUID(investigation_id))
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.stage == key.stage, jobs.c.input_hash == key.input_hash)
            .values(state=state)
        )


async def test_created_investigation_is_waiting_with_its_intake_job(client):
    headers = await guest(client)
    created = await create(client, headers)
    assert created["processing_status"] == "waiting" and created["state"] == "queued"
    assert created["report"] is None and created["error"] is None
    assert created["job"]["state"] == "queued" and created["job"]["stage"] == "intake"
    assert set(created["job"]) == {
        "id",
        "state",
        "stage",
        "cancel_requested",
        "attempts",
        "retry_class",
        "available_at",
        "updated_at",
    }
    assert await fetch(client, headers, created["id"]) == created


async def test_status_follows_the_job_and_the_latest_report(client, app):
    headers = await guest(client)
    investigation_id = (await create(client, headers))["id"]
    await set_intake_state(app, investigation_id, "running")
    running = await fetch(client, headers, investigation_id)
    assert (running["processing_status"], running["state"]) == ("checking", "running")

    await set_intake_state(app, investigation_id, "published")
    stub = await publish(app, investigation_id)
    partial = await fetch(client, headers, investigation_id)
    assert (partial["processing_status"], partial["state"]) == ("partial", "running")
    assert partial["report"] == stub.model_dump(mode="json") and partial["version"] == 1

    final = await publish(app, investigation_id, correction)
    complete = await fetch(client, headers, investigation_id)
    assert (complete["processing_status"], complete["state"]) == ("complete", "completed")
    assert complete["report"]["id"] == final.id and complete["version"] == 2
    assert complete["job"]["state"] == "published"

    await set_intake_state(app, investigation_id, "cancelled")
    cancelled = await fetch(client, headers, investigation_id)
    assert cancelled["processing_status"] == "cancelled" and cancelled["report"]["id"] == final.id

    await set_intake_state(app, investigation_id, "failed")
    failed = await fetch(client, headers, investigation_id)
    assert (failed["processing_status"], failed["state"]) == ("failed", "failed")
    assert failed["report"] is None and failed["error"]["code"] == "PROCESSING_FAILED"


async def test_deleted_job_reads_as_cancelled_without_a_job(client):
    headers = await guest(client)
    created = await create(client, headers)
    deleted = await client.delete(f"/v1/jobs/{created['job']['id']}", headers=headers)
    assert deleted.status_code == 200
    body = await fetch(client, headers, created["id"])
    assert body["processing_status"] == "cancelled" and body["job"] is None


async def test_newest_job_is_reported_after_a_reanalysis(client, app):
    headers = await guest(client)
    investigation_id = (await create(client, headers))["id"]
    await set_intake_state(app, investigation_id, "published")
    await publish(app, investigation_id)
    receipt = await client.post(
        f"/v1/investigations/{investigation_id}/reanalyze",
        json={"reason": "deeper", "base_version": 1},
        headers={**headers, "Idempotency-Key": "deeper"},
    )
    assert receipt.status_code == 202, receipt.text
    body = await fetch(client, headers, investigation_id)
    assert body["job"]["id"] == receipt.json()["job"]["id"]
    assert body["job"]["stage"] == "retrieval" and body["processing_status"] == "partial"
