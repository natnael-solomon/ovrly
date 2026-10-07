"""An owner can retry only explicitly quota-blocked speech, preserving other work."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from test_contract_roundtrip import validate

from services.api.main import create_app
from services.asr import reservations
from services.jobs.models import jobs
from services.jobs.retries import RetryPolicy
from services.pipeline.media_validation import media_stage_key

from .test_analysis import audiovisual as synthetic_av
from .test_analysis import send_text, text_source
from .test_media_validation import audio_bytes, submit
from .test_speech import speech_error, speech_settings, speech_worker, success, wait_for_speech

audiovisual = synthetic_av


def exhausted(_):
    return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})


async def retry(http, identifier, headers, key):
    response = await http.post(
        f"/v1/investigations/{identifier}/speech/retry",
        headers={**headers, "Idempotency-Key": key},
        json={"protocol_version": 1},
    )
    assert response.status_code == 200, response.text
    validate.Validator().validate(response.json(), "speech-retry-response.schema.json")
    assert response.json()["investigation_id"] == identifier
    return response.json()["outcome"]


async def speech_job(app, identifier):
    key = media_stage_key(uuid.UUID(identifier))
    async with app.state.database.engine.connect() as connection:
        return await connection.scalar(
            select(jobs.c.id).where(
                jobs.c.stage == "upload_asr",
                jobs.c.version == key.version,
                jobs.c.input_hash == key.input_hash,
            )
        )


async def test_owner_retry_rechecks_quota_and_reuses_text_deadline_and_media(
    harness, tmp_path, audiovisual, monkeypatch
):
    config = speech_settings(harness, tmp_path, asr_requests_per_day=1)
    clock = [datetime(2026, 10, 6, tzinfo=UTC)]
    monkeypatch.setattr(reservations, "now", lambda: clock[0], raising=False)
    calls = []

    def replay(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})
        return success(request)

    worker = speech_worker(harness, config, replay, quota_clock=lambda: clock[0])
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await worker.start()
        blocked = await wait_for_speech(http, identifier, headers)
        path = f"/v1/investigations/{identifier}"
        await send_text(http, path + "/device-text", headers, text_source(blocked, audiovisual))
        before = (await http.get(path, headers=headers)).json()
        await worker.stop()
        rejected = await http.post(
            path + "/speech/retry",
            headers={**headers, "Idempotency-Key": "still-blocked"},
            json={"protocol_version": 1},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["outcome"] == "quota_exhausted"
        assert len(calls) == 1
        clock[0] += timedelta(days=1)
        replies = await asyncio.gather(
            *(
                http.post(
                    path + "/speech/retry",
                    headers={**headers, "Idempotency-Key": "retry-after-reset"},
                    json={"protocol_version": 1},
                )
                for _ in range(3)
            )
        )
        assert all(response.status_code == 200 for response in replies)
        assert replies[0].json()["outcome"] == "accepted"
        assert all(response.json() == replies[0].json() for response in replies)
        pending = (await http.get(path, headers=headers)).json()
        assert pending["analysis"]["status"] == "partial"
        assert pending["analysis"]["text"] == before["analysis"]["text"]
        assert pending["analysis"]["text_deadline"] == before["analysis"]["text_deadline"]
        restarted = speech_worker(harness, config, replay, quota_clock=lambda: clock[0])
        await restarted.start()
        done = await wait_for_speech(http, identifier, headers)
        await restarted.stop()
        assert done["speech"]["status"] == "completed"
        assert done["id"] == identifier
        assert done["analysis"]["status"] == "complete"
        assert done["analysis"]["text"] == before["analysis"]["text"]
        assert done["analysis"]["text_deadline"] == before["analysis"]["text_deadline"]
        assert len(calls) == 2


async def test_retry_request_errors_and_non_blocked_speech_make_no_provider_call(harness, tmp_path):
    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        assert (await wait_for_speech(http, identifier, headers))["speech"]["status"] == "completed"
        await worker.stop()
        assert await retry(http, identifier, headers, "done") == "already_complete"
        path = f"/v1/investigations/{identifier}/speech/retry"
        missing = await http.post(path, headers=headers, json={"protocol_version": 1})
        assert missing.status_code == 400
        assert missing.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        invalid = await http.post(
            path, headers={**headers, "Idempotency-Key": "bad"}, json={"protocol_version": 2}
        )
        assert invalid.status_code == 422
        other, _ = await submit(http, audio_bytes())
        foreign = await http.post(
            f"/v1/investigations/{other}/speech/retry",
            headers={**headers, "Idempotency-Key": "foreign"},
            json={"protocol_version": 1},
        )
        assert foreign.status_code == 404
        unknown = await http.post(
            f"/v1/investigations/{uuid.uuid4()}/speech/retry",
            headers={**headers, "Idempotency-Key": "unknown"},
            json={"protocol_version": 1},
        )
        assert unknown.status_code == 404
        assert len(calls) == 1


async def test_uncertain_provider_outcome_is_never_replayed_by_retry(harness, tmp_path):
    calls = []

    def replay(request):
        calls.append(request)
        raise httpx.ReadTimeout("private detail", request=request)

    config = speech_settings(harness, tmp_path)
    policy = RetryPolicy(
        transient_attempts=0,
        rate_limited_attempts=0,
        backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )
    app = create_app(config)
    worker = speech_worker(harness, config, replay, retry_policy=policy)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        failed = await wait_for_speech(http, identifier, headers)
        assert failed["speech"]["reason"] == "unknown_outcome"
        assert await retry(http, identifier, headers, "uncertain") == "not_eligible"
        await asyncio.sleep(0.2)
        await worker.stop()
        assert (await http.get(f"/v1/investigations/{identifier}", headers=headers)).json() == (
            failed
        )
        assert len(calls) == 1


async def test_repeated_quota_failure_keeps_gap_and_a_new_request_can_retry_again(
    harness, tmp_path
):
    calls = []
    answers = [exhausted, exhausted, success]

    def replay(request):
        calls.append(request)
        return answers[len(calls) - 1](request)

    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        assert speech_error(await wait_for_speech(http, identifier, headers)) == (
            "ASR_QUOTA_EXHAUSTED"
        )
        await worker.stop()
        assert await retry(http, identifier, headers, "first") == "accepted"
        assert await retry(http, identifier, headers, "while-queued") == "in_progress"
        assert await retry(http, identifier, headers, "first") == "accepted"
        worker = speech_worker(harness, config, replay)
        await worker.start()
        again = await wait_for_speech(http, identifier, headers)
        assert speech_error(again) == "ASR_QUOTA_EXHAUSTED"
        assert again["id"] == identifier
        assert await retry(http, identifier, headers, "first") == "accepted"
        assert len(calls) == 2
        assert await retry(http, identifier, headers, "second") == "accepted"
        done = await wait_for_speech(http, identifier, headers)
        await worker.stop()
        assert done["speech"]["status"] == "completed"
        assert len(calls) == 3


@pytest.mark.parametrize("loss", ["deleted", "cancelled", "source", "audio", "disabled"])
async def test_retry_cannot_bypass_deletion_cancellation_source_loss_or_settings(
    harness, tmp_path, loss
):
    calls = []

    def replay(request):
        calls.append(request)
        return exhausted(request)

    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        await wait_for_speech(http, identifier, headers)
        await worker.stop()
        job = await speech_job(app, identifier)
        if loss == "cancelled":
            assert await retry(http, identifier, headers, loss) == "accepted"
            cancelled = await http.post(f"/v1/jobs/{job}/cancel", headers=headers)
            assert cancelled.status_code in {200, 202}, cancelled.text
            assert await retry(http, identifier, headers, "after-cancel") == "not_eligible"
        else:
            if loss == "deleted":
                assert (await http.delete(f"/v1/jobs/{job}", headers=headers)).status_code == 200
            elif loss == "source":
                for stored in config.storage_dir.rglob("*"):
                    if stored.is_file():
                        stored.unlink()
            elif loss == "audio":
                for prepared in config.artifacts_dir.rglob("*.wav"):
                    prepared.unlink()
            else:
                app.state.settings = config.model_copy(update={"asr_enabled": False})
            assert await retry(http, identifier, headers, loss) == "not_eligible"
        worker = speech_worker(harness, config, replay)
        await worker.start()
        await asyncio.sleep(0.2)
        await worker.stop()
        assert len(calls) == 1
        body = await http.get(f"/v1/investigations/{identifier}", headers=headers)
        assert body.status_code == 200
        assert (body.json().get("speech") or {}).get("status") != "completed"
