"""POST /v1/voice/actions (BE-10, #33; BC-D04): allowlist, ownership, audit and replay."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select, update
from test_intake_api import CONTRACT_VALIDATOR, URL_BODY, assert_error, guest
from test_reports_api import create_investigation, publish

from services.api.main import create_app
from services.jobs.models import jobs
from services.models import saved_reports, voice_actions
from services.pipeline.intake import intake_stage_key
from services.settings import Settings

VOICE = "/v1/voice/actions"
RESPONSE_SCHEMA = "voice-action-response.schema.json"
REQUEST_SCHEMA = "voice-action-request.schema.json"


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


def body(action, kind, target_id, request_id=None):
    return {
        "request_id": request_id or f"req_{uuid.uuid4().hex}",
        "action": action,
        "target": {"kind": kind, "id": target_id},
    }


async def act(client, headers, payload):
    response = await client.post(VOICE, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    CONTRACT_VALIDATOR.validate(result, RESPONSE_SCHEMA)
    assert result["request_id"] == payload["request_id"]
    assert result["action"] == payload["action"]
    if "error" in result:
        assert result["error"]["request_id"] == payload["request_id"]
    return result


def assert_accepted(result, payload, message):
    assert result == {
        "request_id": payload["request_id"],
        "result": "accepted",
        "action": payload["action"],
        "target": payload["target"],
        "message": message,
    }


def assert_denied(result, code, action):
    assert result["result"] == "denied"
    assert result["error"]["code"] == code
    assert result["error"]["action"] == action
    assert result["error"]["retryable"] is False


async def job_of(app, investigation_id):
    key = intake_stage_key(uuid.UUID(investigation_id))
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(
                select(jobs.c.id, jobs.c.state, jobs.c.cancel_outcome).where(
                    jobs.c.version == key.version,
                    jobs.c.stage == key.stage,
                    jobs.c.input_hash == key.input_hash,
                )
            )
        ).one()


async def set_job_state(app, job_id, state):
    async with app.state.database.engine.begin() as connection:
        await connection.execute(update(jobs).where(jobs.c.id == job_id).values(state=state))


async def audit_rows(app, request_ids):
    # The test database is shared by the session; look only at this test's requests.
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(
                select(voice_actions)
                .where(voice_actions.c.request_id.in_(list(request_ids)))
                .order_by(voice_actions.c.created_at)
            )
        ).all()


def test_audit_table_has_no_transcript_column():
    assert set(voice_actions.columns.keys()) == {
        "owner_id",
        "request_id",
        "action",
        "target_kind",
        "target_id",
        "result",
        "error_code",
        "response",
        "created_at",
    }


async def test_each_allowlisted_action_is_accepted_for_the_owner(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "open")
    report = await publish(app, investigation_id)
    sent = []

    payload = body("open_check", "investigation", investigation_id)
    sent.append(payload["request_id"])
    assert_accepted(await act(client, headers, payload), payload, "Opened the check.")
    CONTRACT_VALIDATOR.validate(payload, REQUEST_SCHEMA)

    payload = body("save_report", "report", report.id)
    sent.append(payload["request_id"])
    assert_accepted(await act(client, headers, payload), payload, "Saved the report.")
    saved = (await client.get("/v1/reports/saved", headers=headers)).json()["items"]
    assert [item["report_id"] for item in saved] == [report.id]

    job = await job_of(app, investigation_id)
    assert job.state == "queued"
    for action in ("queue_retry", "queue_continue"):
        payload = body(action, "job", str(job.id))
        sent.append(payload["request_id"])
        result = await act(client, headers, payload)
        assert_accepted(result, payload, "The check is already in progress.")
    assert (await job_of(app, investigation_id)).state == "queued"

    payload = body("queue_cancel", "job", str(job.id))
    sent.append(payload["request_id"])
    assert_accepted(await act(client, headers, payload), payload, "Cancelled the check.")
    cancelled = await job_of(app, investigation_id)
    assert cancelled.state == "cancelled" and cancelled.cancel_outcome == "effective"
    # The REST route replays the receipt the voice action stored.
    receipt = await client.post(f"/v1/jobs/{job.id}/cancel", headers=headers)
    assert receipt.status_code == 200 and receipt.json()["cancellation"] == "effective"

    rows = await audit_rows(app, sent)
    assert [(row.action, row.result, row.error_code) for row in rows] == [
        ("open_check", "accepted", None),
        ("save_report", "accepted", None),
        ("queue_retry", "accepted", None),
        ("queue_continue", "accepted", None),
        ("queue_cancel", "accepted", None),
    ]


async def test_job_state_decides_cancel_retry_and_continue(client, app):
    headers = await guest(client)
    running = await job_of(app, await create_investigation(client, headers, "running"))
    await set_job_state(app, running.id, "running")
    payload = body("queue_cancel", "job", str(running.id))
    assert_accepted(await act(client, headers, payload), payload, "Stopping the check.")
    assert (await job_of(app, str(await _investigation_for(app, running.id)))).state == "running"

    for state in ("published", "failed", "cancelled"):
        finished = await job_of(app, await create_investigation(client, headers, state))
        await set_job_state(app, finished.id, state)
        for action in ("queue_retry", "queue_continue"):
            result = await act(client, headers, body(action, "job", str(finished.id)))
            assert_denied(result, "VOICE_ACTION_INVALID_STATE", "none")
            assert state in result["error"]["message"]
        if state != "cancelled":
            result = await act(client, headers, body("queue_cancel", "job", str(finished.id)))
            assert_denied(result, "VOICE_ACTION_INVALID_STATE", "none")
        assert (await job_of(app, str(await _investigation_for(app, finished.id)))).state == state

    deleted = await job_of(app, await create_investigation(client, headers, "deleted"))
    assert (await client.delete(f"/v1/jobs/{deleted.id}", headers=headers)).status_code == 200
    for action in ("queue_cancel", "queue_retry", "queue_continue"):
        result = await act(client, headers, body(action, "job", str(deleted.id)))
        assert_denied(result, "VOICE_TARGET_NOT_FOUND", "fix_request")


async def _investigation_for(app, job_id):
    async with app.state.database.engine.connect() as connection:
        payload = await connection.scalar(select(jobs.c.payload).where(jobs.c.id == job_id))
    return payload["investigation_id"]


async def test_other_owners_targets_are_not_found_and_untouched(client, app):
    owner = await guest(client)
    other = await guest(client)
    investigation_id = await create_investigation(client, owner, "mine")
    report = await publish(app, investigation_id)
    job = await job_of(app, investigation_id)
    for action, kind, target in (
        ("open_check", "investigation", investigation_id),
        ("save_report", "report", report.id),
        ("queue_cancel", "job", str(job.id)),
        ("queue_retry", "job", str(job.id)),
        ("queue_continue", "job", str(job.id)),
        ("open_check", "investigation", str(uuid.uuid4())),
        ("save_report", "report", "rpt_synthetic_0001"),
        ("queue_cancel", "job", str(job.id).upper()),
    ):
        payload = body(action, kind, target)
        result = await act(client, other, payload)
        assert_denied(result, "VOICE_TARGET_NOT_FOUND", "fix_request")
        assert result["target"] == payload["target"]
        assert target not in result["message"]
    assert (await job_of(app, investigation_id)).state == "queued"
    async with app.state.database.engine.connect() as connection:
        saves = await connection.scalar(
            select(func.count())
            .select_from(saved_reports)
            .where(saved_reports.c.report_id == uuid.UUID(report.id))
        )
    assert saves == 0


async def test_unsupported_action_is_a_typed_denial(client, app):
    headers = await guest(client)
    payload = body("delete_report", "report", "rpt_synthetic_0101")
    sent = [payload["request_id"]]
    result = await act(client, headers, payload)
    assert_denied(result, "VOICE_ACTION_UNSUPPORTED", "fix_request")
    assert result["target"] == payload["target"]
    for action in ("switch_tab", "open_tab", "publish_report", "x" * 64):
        malformed = {**body(action, "tab", "space"), "target": {"kind": "tab", "tab": "space"}}
        sent.append(malformed["request_id"])
        result = await act(client, headers, malformed)
        assert_denied(result, "VOICE_ACTION_UNSUPPORTED", "fix_request")
        assert "target" not in result
    rows = await audit_rows(app, sent)
    assert [(row.action[:16], row.result, row.error_code) for row in rows] == [
        ("delete_report", "denied", "VOICE_ACTION_UNSUPPORTED"),
        ("switch_tab", "denied", "VOICE_ACTION_UNSUPPORTED"),
        ("open_tab", "denied", "VOICE_ACTION_UNSUPPORTED"),
        ("publish_report", "denied", "VOICE_ACTION_UNSUPPORTED"),
        ("x" * 16, "denied", "VOICE_ACTION_UNSUPPORTED"),
    ]


async def test_malformed_requests_fail_validation_without_audit(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    valid = body("open_check", "investigation", investigation_id)
    cases = [
        {key: value for key, value in valid.items() if key != "target"},
        {**valid, "target": {"kind": "job", "id": investigation_id}},
        {**valid, "confirmed": True},
        {**valid, "target": {**valid["target"], "confirmed": True}},
        {**valid, "request_id": "bad id"},
        {**valid, "request_id": "a" * 129},
        {**valid, "request_id": "abc\n"},
        {**valid, "target": {"kind": "investigation", "id": "-leading-dash"}},
        {**valid, "action": ""},
        {**valid, "action": "x" * 65},
        {**valid, "action": 7},
        ["not", "an", "object"],
    ]
    for case in cases:
        assert_error(await client.post(VOICE, json=case, headers=headers), 422, "VALIDATION_FAILED")
    assert_error(
        await client.post(VOICE, content=b"{not json", headers=headers), 422, "VALIDATION_FAILED"
    )
    assert_error(
        await client.post(VOICE, json={**valid, "user_id": "someone"}, headers=headers),
        422,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(
        await client.post(VOICE, json=valid, params={"owner_id": "x"}, headers=headers),
        400,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(await client.post(VOICE, json=valid), 401, "AUTHENTICATION_REQUIRED")
    assert await audit_rows(app, [valid["request_id"], "bad id", "abc\n", "a" * 129]) == []


async def test_repeated_request_id_replays_without_running_again(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    report = await publish(app, investigation_id)
    payload = body("save_report", "report", report.id, request_id="req_synthetic_replay")
    first = await act(client, headers, payload)
    saved_at = (await client.get("/v1/reports/saved", headers=headers)).json()["items"][0][
        "saved_at"
    ]
    again = await act(client, headers, payload)
    assert again == first
    items = (await client.get("/v1/reports/saved", headers=headers)).json()["items"]
    assert [item["saved_at"] for item in items] == [saved_at]

    # A replayed denial stays a denial; the request_id namespace is per caller.
    other = await guest(client)
    denied = await act(client, other, payload)
    assert_denied(denied, "VOICE_TARGET_NOT_FOUND", "fix_request")
    assert await act(client, other, payload) == denied

    reused = body("open_check", "investigation", investigation_id, "req_synthetic_replay")
    assert_error(
        await client.post(VOICE, json=reused, headers=headers), 409, "IDEMPOTENCY_KEY_REUSED"
    )
    rows = await audit_rows(app, ["req_synthetic_replay"])
    assert len(rows) == 2 and {row.request_id for row in rows} == {"req_synthetic_replay"}
    assert all(row.response["request_id"] == "req_synthetic_replay" for row in rows)
    assert URL_BODY["source"]["url"] not in str([row.response for row in rows])
