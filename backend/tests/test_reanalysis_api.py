"""POST /v1/investigations/{id}/reanalyze and the export route (BE-10, #33)."""

import hashlib
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select, update
from test_intake_api import CONTRACT_VALIDATOR, ContractInvalid, assert_error, guest
from test_reports_api import assert_component, correction, create_investigation, publish

from services.api.errors import _validation_message
from services.api.main import create_app
from services.api.schemas import ExpansionReanalysis
from services.jobs.handlers import default_handlers
from services.jobs.models import jobs
from services.jobs.retries import NonRetriableInput
from services.models import reanalysis_requests, report_versions
from services.pipeline.intake import intake_stage_key
from services.pipeline.stub_reports import enable_stub_reports, stub_reanalysis
from services.reanalysis import request_hash
from services.reports import REANALYSIS_STAGE
from services.settings import Settings

REPORT_SCHEMA = "report-version.schema.json"


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


async def reanalyze(client, headers, investigation_id, body, key=None):
    return await client.post(
        f"/v1/investigations/{investigation_id}/reanalyze",
        json=body,
        headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )


async def job_row(app, job_id):
    async with app.state.database.engine.connect() as connection:
        return (await connection.execute(select(jobs).where(jobs.c.id == uuid.UUID(job_id)))).one()


async def read(client, headers, investigation_id, version):
    response = await client.get(
        f"/v1/investigations/{investigation_id}/reports/{version}", headers=headers
    )
    assert response.status_code == 200, response.text
    CONTRACT_VALIDATOR.validate(response.json(), REPORT_SCHEMA)
    return response.json()


async def test_correction_publishes_a_new_version_and_reruns_one_claim(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    first = await publish(app, investigation_id)
    claim = first.claims[0]
    body = {
        "reason": "correction",
        "base_version": 1,
        "claim_id": claim.id,
        "proposition": "A corrected, synthetic normalized meaning.",
    }
    response = await reanalyze(client, headers, investigation_id, body, key="fix-1")
    assert response.status_code == 202, response.text
    receipt = response.json()
    assert_component(receipt, "ReanalysisResponse")
    assert receipt["reason"] == "correction" and receipt["base_version"] == 1
    assert receipt["published_version"] == 2
    # The job runs the internal reanalysis handler; clients see the contract stage it starts at.
    assert receipt["job"]["state"] == "queued" and receipt["job"]["stage"] == "retrieval"

    original = await read(client, headers, investigation_id, 1)
    assert original == first.model_dump(mode="json"), "Version 1 must be unchanged"
    second = await read(client, headers, investigation_id, 2)
    assert second["supersedes"] == first.id and second["provisional"] is True
    assert second["change_summary"].startswith("Correction:")
    corrected = next(item for item in second["claims"] if item["id"] == claim.id)
    assert corrected["proposition"] == body["proposition"]
    assert corrected["original_text"] == claim.original_text
    assert corrected["correction"]["attributed_to"] == "user"
    assert corrected["correction"]["superseded_proposition"] == claim.proposition
    assert all(item["claim_id"] != claim.id for item in second["assessments"])
    assert all(item["claim_id"] != claim.id for item in second["evidence"])
    others = [item for item in first.assessments if item.claim_id != claim.id]
    assert [item["id"] for item in second["assessments"]] == [item.id for item in others]
    assert all(item["version"] == 2 for item in second["assessments"])
    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    # The correction of a stub stays labelled as a fixture.
    assert [item["fixture"] for item in listing.json()["items"]] == [True, True]

    job = await job_row(app, receipt["job"]["id"])
    assert str(job.owner_id) == (await owner(app, investigation_id))
    assert job.payload["claim_ids"] == [claim.id]
    assert job.payload["supersedes_version"] == 2
    assert job.payload["reason"] == "correction"
    assert body["proposition"] not in str(job.payload), "No user text in the job payload"

    replay = await reanalyze(client, headers, investigation_id, body, key="fix-1")
    assert replay.status_code == 202 and replay.json() == receipt
    assert_error(
        await reanalyze(
            client, headers, investigation_id, {**body, "proposition": "Other."}, key="fix-1"
        ),
        409,
        "IDEMPOTENCY_KEY_REUSED",
    )
    assert_error(
        await reanalyze(client, headers, investigation_id, body),
        409,
        "REPORT_VERSION_STALE",
        "fix_request",
    )
    for bad, code in (
        ({**body, "base_version": 2, "claim_id": "clm_missing"}, "CLAIM_NOT_IN_VERSION"),
        ({**body, "base_version": 2}, "CORRECTION_UNCHANGED"),
    ):
        assert_error(await reanalyze(client, headers, investigation_id, bad), 422, code)
    # The cancel route reaches the reanalysis job like any other owned job.
    cancel = await client.post(f"/v1/jobs/{receipt['job']['id']}/cancel", headers=headers)
    assert cancel.status_code == 200 and cancel.json()["cancellation"] == "effective"


async def owner(app, investigation_id):
    async with app.state.database.engine.connect() as connection:
        return str(
            await connection.scalar(
                select(report_versions.c.owner_id).where(
                    report_versions.c.investigation_id == uuid.UUID(investigation_id)
                )
            )
        )


async def test_expansion_requires_a_confirmed_match_and_deeper_queues(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    await publish(app, investigation_id)
    assert_error(
        await reanalyze(
            client,
            headers,
            investigation_id,
            {"reason": "expansion", "base_version": 1, "match_confirmed": False},
        ),
        422,
        "MATCH_CONFIRMATION_REQUIRED",
        "fix_request",
    )
    assert_error(
        await reanalyze(
            client, headers, investigation_id, {"reason": "expansion", "base_version": 1}
        ),
        422,
        "VALIDATION_FAILED",
    )
    full_video = await create_investigation(client, headers, "full-video")
    for reason, extra in (
        ("expansion", {"match_confirmed": True, "source_investigation_id": full_video}),
        ("deeper", {}),
    ):
        response = await reanalyze(
            client, headers, investigation_id, {"reason": reason, "base_version": 1, **extra}
        )
        assert response.status_code == 202, response.text
        assert_component(response.json(), "ReanalysisResponse")
        assert response.json()["published_version"] is None
        job = await job_row(app, response.json()["job"]["id"])
        assert job.stage == REANALYSIS_STAGE
        assert job.payload["supersedes_version"] == 1 and job.payload["claim_ids"] == []
        expected = "media_validation" if reason == "expansion" else "retrieval"
        assert response.json()["job"]["stage"] == expected
        source = full_video if reason == "expansion" else None
        assert response.json()["source_investigation_id"] == source
        assert job.payload["source_investigation_id"] == source
    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert [item["version"] for item in listing.json()["items"]] == [1]


async def test_reanalysis_rejects_bad_requests_and_other_owners(client, app):
    headers = await guest(client)
    other = await guest(client)
    investigation_id = await create_investigation(client, headers)
    deeper = {"reason": "deeper", "base_version": 1}
    assert_error(
        await reanalyze(client, headers, investigation_id, deeper),
        409,
        "REPORT_NOT_AVAILABLE",
        "retry",
    )
    await publish(app, investigation_id)
    response = await client.post(
        f"/v1/investigations/{investigation_id}/reanalyze", json=deeper, headers=headers
    )
    assert_error(response, 400, "IDEMPOTENCY_KEY_REQUIRED")
    for bad in (
        {"reason": "verdict", "base_version": 1},
        {"reason": "deeper", "base_version": 0},
        {"reason": "deeper"},
        {**deeper, "confirmed": True},
        {"reason": "correction", "base_version": 1, "claim_id": "c", "proposition": ""},
    ):
        assert_error(
            await reanalyze(client, headers, investigation_id, bad), 422, "VALIDATION_FAILED"
        )
    assert_error(
        await reanalyze(client, headers, investigation_id, {**deeper, "owner_id": "x"}),
        422,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(await reanalyze(client, other, investigation_id, deeper), 404, "NOT_FOUND")
    assert_error(await reanalyze(client, headers, str(uuid.uuid4()), deeper), 404, "NOT_FOUND")
    assert_error(
        await client.post(
            f"/v1/investigations/{investigation_id}/reanalyze",
            json=deeper,
            headers={"Idempotency-Key": "k"},
        ),
        401,
        "AUTHENTICATION_REQUIRED",
    )
    async with app.state.database.engine.connect() as connection:
        recorded = (
            await connection.execute(
                select(reanalysis_requests.c.id).where(
                    reanalysis_requests.c.investigation_id == uuid.UUID(investigation_id)
                )
            )
        ).all()
    assert recorded == []


async def test_same_key_for_two_investigations_is_reused_not_duplicated(client, app):
    headers = await guest(client)
    first = await create_investigation(client, headers, "first")
    second = await create_investigation(client, headers, "second")
    for investigation_id in (first, second):
        await publish(app, investigation_id)
    body = {"reason": "deeper", "base_version": 1}
    assert (await reanalyze(client, headers, first, body, key="shared")).status_code == 202
    assert_error(
        await reanalyze(client, headers, second, body, key="shared"),
        409,
        "IDEMPOTENCY_KEY_REUSED",
    )


async def test_export_route_is_owner_scoped_and_matches_the_contract(client, app):
    headers = await guest(client)
    other = await guest(client)
    investigation_id = await create_investigation(client, headers)
    await publish(app, investigation_id)
    await publish(app, investigation_id, correction)
    path = f"/v1/investigations/{investigation_id}/reports/1/export"
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    exported = response.json()
    assert_component(exported, "ReportExport")
    assert exported["version"] == 1 and exported["fixture"] is True
    assert exported["limitations"][0].startswith("Development fixture")
    second = (
        await client.get(f"/v1/investigations/{investigation_id}/reports/2/export", headers=headers)
    ).json()
    assert second["fixture"] is False and second["supersedes"] == exported["report_id"]
    assert_error(await client.get(path, headers=other), 404, "NOT_FOUND")
    assert_error(await client.get(path), 401, "AUTHENTICATION_REQUIRED")
    assert_error(
        await client.get(
            f"/v1/investigations/{investigation_id}/reports/3/export", headers=headers
        ),
        404,
        "NOT_FOUND",
    )


async def test_stub_reanalysis_publishes_one_fixture_version(client, app):
    assert REANALYSIS_STAGE not in default_handlers()
    handlers = dict(default_handlers())
    enable_stub_reports(handlers)
    assert handlers[REANALYSIS_STAGE] is stub_reanalysis

    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    base = await publish(app, investigation_id)
    claim = base.claims[0]
    receipt = (
        await reanalyze(
            client,
            headers,
            investigation_id,
            {
                "reason": "correction",
                "base_version": 1,
                "claim_id": claim.id,
                "proposition": "Synthetic corrected meaning.",
            },
        )
    ).json()
    job = await job_row(app, receipt["job"]["id"])
    context = SimpleNamespace(queue=SimpleNamespace(database=app.state.database))
    claimed = SimpleNamespace(payload=job.payload)
    results = [await stub_reanalysis(claimed, context) for _ in range(2)]
    assert results[0] == results[1] == {"investigation_id": investigation_id, "version": 3}
    third = await read(client, headers, investigation_id, 3)
    assert third["change_summary"].startswith("Development fixture, not a check of this media")
    placeholder = next(item for item in third["assessments"] if item["claim_id"] == claim.id)
    assert placeholder["overall"] == "insufficient_evidence" and placeholder["relations"] == []
    assert {item["claim_id"] for item in third["assessments"]} == {c.id for c in base.claims}
    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert [item["fixture"] for item in listing.json()["items"]] == [True, True, True]

    for payload in (
        {},
        {**job.payload, "owner_id": str(uuid.uuid4())},
        {**job.payload, "request_id": str(uuid.uuid4())},
    ):
        with pytest.raises(NonRetriableInput):
            await stub_reanalysis(SimpleNamespace(payload=payload), context)


def _message(paths):
    errors = [{"loc": ("body", *path), "msg": "x", "type": "missing"} for path in paths]
    return _validation_message(RequestValidationError(errors))


def test_validation_messages_fit_the_error_schema():
    code, message = _message([(f"field_{index:03d}",) for index in range(60)])
    assert code == "VALIDATION_FAILED"
    assert len(message) <= 240 and message.endswith(", ...")
    assert message.startswith("The request is invalid at: body.field_000, body.field_001")


@pytest.mark.parametrize(
    "paths",
    [
        [("k" * 300,)],
        [("k" * 300,), ("other",)],
        [("short",), ("k" * 300,)],
        [("a" * 205,), ("b",)],
        [("a" * 209,)],
        [("a" * 208,), ("b",)],
    ],
)
def test_one_long_path_is_cut_to_the_limit(paths):
    code, message = _message(paths)
    assert code == "VALIDATION_FAILED"
    assert len(message) <= 240
    assert message.startswith("The request is invalid at: body.")
    assert message.endswith(", ...")
    CONTRACT_VALIDATOR.validate(
        {
            "code": code,
            "message": message,
            "retryable": False,
            "action": "fix_request",
            "request_id": "req_synthetic",
        },
        "error.schema.json",
    )


def test_short_messages_are_not_truncated():
    assert _message([("a" * 205,)])[1] == "The request is invalid at: body." + "a" * 205


async def create_capture(client, headers):
    response = await client.post(
        "/v1/captures", json={}, headers={**headers, "Idempotency-Key": uuid.uuid4().hex}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_expansion_names_a_valid_full_video_of_the_same_owner(client, app):
    headers = await guest(client)
    other = await guest(client)
    clip = await create_capture(client, headers)
    await publish(app, clip)
    full_video = await create_investigation(client, headers, "full")
    expansion = {"reason": "expansion", "base_version": 1, "match_confirmed": True}

    assert_error(
        await reanalyze(client, headers, clip, expansion),
        422,
        "EXPANSION_SOURCE_REQUIRED",
        "fix_request",
    )
    assert_error(
        await reanalyze(client, headers, clip, {**expansion, "source_investigation_id": clip}),
        422,
        "EXPANSION_SOURCE_SELF",
    )
    other_capture = await create_capture(client, headers)
    assert_error(
        await reanalyze(
            client, headers, clip, {**expansion, "source_investigation_id": other_capture}
        ),
        422,
        "EXPANSION_SOURCE_UNSUPPORTED",
    )
    foreign = await create_investigation(client, other, "foreign")
    for missing in (foreign, str(uuid.uuid4())):
        assert_error(
            await reanalyze(
                client, headers, clip, {**expansion, "source_investigation_id": missing}
            ),
            404,
            "NOT_FOUND",
        )
    assert_error(
        await reanalyze(
            client, headers, clip, {**expansion, "source_investigation_id": "not-a-uuid"}
        ),
        422,
        "VALIDATION_FAILED",
    )
    # match_confirmed false is refused before the source is looked at.
    assert_error(
        await reanalyze(
            client,
            headers,
            clip,
            {**expansion, "match_confirmed": False, "source_investigation_id": foreign},
        ),
        422,
        "MATCH_CONFIRMATION_REQUIRED",
    )
    for state in ("failed", "cancelled"):
        broken = await create_investigation(client, headers, f"broken-{state}")
        await set_intake_state(app, broken, state)
        assert_error(
            await reanalyze(
                client, headers, clip, {**expansion, "source_investigation_id": broken}
            ),
            409,
            "EXPANSION_SOURCE_UNAVAILABLE",
        )

    body = {**expansion, "source_investigation_id": full_video}
    accepted = await reanalyze(client, headers, clip, body, key="expand")
    assert accepted.status_code == 202, accepted.text
    receipt = accepted.json()
    assert_component(receipt, "ReanalysisResponse")
    assert receipt["source_investigation_id"] == full_video
    assert receipt["published_version"] is None
    replay = await reanalyze(client, headers, clip, body, key="expand")
    assert replay.status_code == 202 and replay.json() == receipt
    second = await create_investigation(client, headers, "full-2")
    assert_error(
        await reanalyze(
            client, headers, clip, {**expansion, "source_investigation_id": second}, key="expand"
        ),
        409,
        "IDEMPOTENCY_KEY_REUSED",
    )
    job = await job_row(app, receipt["job"]["id"])
    assert job.payload["source_investigation_id"] == full_video
    assert set(job.payload) == {
        "request_id",
        "investigation_id",
        "owner_id",
        "reason",
        "supersedes_version",
        "claim_ids",
        "source_investigation_id",
    }
    async with app.state.database.engine.connect() as connection:
        stored = (
            await connection.execute(
                select(reanalysis_requests.c.source_investigation_id).where(
                    reanalysis_requests.c.id == uuid.UUID(receipt["id"])
                )
            )
        ).scalar_one()
    assert str(stored) == full_video


async def set_intake_state(app, investigation_id, state):
    key = intake_stage_key(uuid.UUID(investigation_id))
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.stage == key.stage, jobs.c.input_hash == key.input_hash)
            .values(state=state)
        )


def test_expansion_shape_and_request_hash():
    investigation_id = uuid.uuid4()
    source = str(uuid.uuid4())
    body = {"reason": "expansion", "base_version": 1, "match_confirmed": True}
    assert_component(body, "ReanalysisRequest")
    assert_component({**body, "source_investigation_id": source}, "ReanalysisRequest")
    for bad in ({**body, "source_investigation_id": "x"}, {**body, "source": source}):
        with pytest.raises(ContractInvalid):
            assert_component(bad, "ReanalysisRequest")
    # Requests stored before the field existed keep their hash, so a replay still matches.
    legacy = json.dumps(
        {"investigation_id": str(investigation_id), **body}, sort_keys=True, separators=(",", ":")
    )
    without = ExpansionReanalysis.model_validate(body)
    assert request_hash(investigation_id, without) == hashlib.sha256(legacy.encode()).hexdigest()
    named = ExpansionReanalysis.model_validate({**body, "source_investigation_id": source})
    assert request_hash(investigation_id, named) != request_hash(investigation_id, without)
