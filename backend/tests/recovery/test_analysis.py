"""Extraction progress is not a downstream report or a claim assessment."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.api.main import create_app
from services.jobs.handlers import default_handlers
from services.media.runner import CommandLimits, run_command
from services.pipeline import analysis

from .test_device_text import frame
from .test_device_text import text_upload as prepared_text_upload
from .test_media_validation import submit
from .test_speech import speech_settings, speech_worker, success, wait_for_speech

text_upload = prepared_text_upload


async def test_no_audio_and_completed_text_are_partial_observations_not_a_report(text_upload):
    _, http, headers, path, source = text_upload
    investigation = path.removesuffix("/device-text")
    pending = (await http.get(investigation, headers=headers)).json()
    assert pending["analysis"]["status"] == "pending"
    assert pending["analysis"]["pending_modalities"] == ["text"]
    assert pending["analysis"]["unavailable_modalities"] == ["speech"]
    assert datetime.fromisoformat(pending["analysis"]["text_deadline"]) > datetime.now(UTC)
    assert (
        await http.put(path + "/batches/0", headers=headers, json={**source, "frames": [frame()]})
    ).status_code == 200
    assert (
        await http.post(
            path + "/complete",
            headers=headers,
            json={
                **source,
                "batch_count": 1,
                "dropped_frames": 0,
                "capped_frames": 0,
                "unfinished_frames": 0,
            },
        )
    ).status_code == 200
    result = (await http.get(investigation, headers=headers)).json()
    assert result["analysis"]["status"] == "partial"
    assert result["analysis"]["analyzed_modalities"] == ["text"]
    assert result["analysis"]["pending_modalities"] == []
    assert result["analysis"]["text"]["batches"][0]["body"]["frames"][0]["frame_pts"] == 250
    assert result["analysis"]["gaps"] == [
        {
            "modality": "speech",
            "reason": "NO_AUDIO_TRACK",
            "interval": {"start_ms": 0, "end_ms": 1000, "timebase": "media"},
        }
    ]
    assert result["report"] is None
    assert result["processing_status"] == "checking"
    assert result["coverage"]["status"] == "not_started"


@pytest.mark.parametrize("completed_early", [False, True])
async def test_text_deadline_is_durable_exact_and_does_not_wait_after_completion(
    text_upload, harness, monkeypatch, completed_early
):
    app, http, headers, path, source = text_upload
    investigation = path.removesuffix("/device-text")
    first = (await http.get(investigation, headers=headers)).json()["analysis"]
    deadline = datetime.fromisoformat(first["text_deadline"])
    clock = [deadline - timedelta(microseconds=1)]
    monkeypatch.setattr(analysis, "now", lambda: clock[0])
    completion = {
        **source,
        "batch_count": 0,
        "dropped_frames": 0,
        "capped_frames": 0,
        "unfinished_frames": 0,
    }
    if completed_early:
        assert (
            await http.post(path + "/complete", headers=headers, json=completion)
        ).status_code == 200
    worker = harness.worker(None, stages=default_handlers(settings=app.state.settings))
    await worker.start()
    for _ in range(3):
        before = (await http.get(investigation, headers=headers)).json()["analysis"]
        assert before["text_deadline"] == first["text_deadline"]
        assert before["text_expired"] is False
    clock[0] = deadline
    async with asyncio.timeout(5):
        while True:
            at = (await http.get(investigation, headers=headers)).json()["analysis"]
            if completed_early or at["text_expired"]:
                break
            await asyncio.sleep(0.01)
    assert at["status"] == "no_usable"
    await worker.stop()
    clock[0] += timedelta(days=2)
    restarted = harness.worker(None, stages=default_handlers(settings=app.state.settings))
    await restarted.start()
    await asyncio.sleep(0.05)
    after = (await http.get(investigation, headers=headers)).json()["analysis"]
    await restarted.stop()
    assert after["text_expired"] is (not completed_early)
    assert after["text_deadline"] == first["text_deadline"]
    assert after["pending_modalities"] == []
    assert [gap["reason"] for gap in after["gaps"]] == (
        ["NO_AUDIO_TRACK"] if completed_early else ["NO_AUDIO_TRACK", "DEVICE_TEXT_MISSING"]
    )


@pytest.fixture
async def audiovisual(tmp_path):
    path = tmp_path / "av.mkv"
    result = await run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:rate=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000",
            "-t",
            "1",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert result.returncode == 0
    return path.read_bytes()


def text_source(body, data):
    return {
        "protocol_version": 1,
        "upload_id": body["source"]["upload_id"],
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "timebase": "media",
        "rotation_degrees": 0,
        "box_space": "normalized_10000",
        "recognizer": {"name": "synthetic", "version": "1"},
        "sampling": None,
    }


async def send_text(http, path, headers, source, *, empty=False, failed=False):
    body = {
        **source,
        "frames": [frame(status="failed" if failed else "recognized", text=not empty)],
    }
    batch = await http.put(path + "/batches/0", headers=headers, json=body)
    assert batch.status_code == 200, batch.text
    completion = {
        **source,
        "batch_count": 1,
        "dropped_frames": 0,
        "capped_frames": 0,
        "unfinished_frames": 0,
    }
    result = await http.post(path + "/complete", headers=headers, json=completion)
    assert result.status_code == 200, result.text
    return body, completion


@pytest.mark.parametrize("outcome", ["speech", "quota", "unavailable", "empty"])
async def test_terminal_speech_starts_one_deadline_and_late_text_never_retranscribes(
    harness, tmp_path, audiovisual, monkeypatch, outcome
):
    config = speech_settings(harness, tmp_path)
    clock = [datetime(2026, 10, 6, 12, tzinfo=UTC)]
    monkeypatch.setattr(analysis, "now", lambda: clock[0])
    calls = []

    def replay(request):
        calls.append(request)
        if outcome == "quota":
            return httpx.Response(429, json={"error": {"code": "insufficient_quota"}})
        if outcome == "unavailable":
            return httpx.Response(401, json={})
        if outcome == "empty":
            return httpx.Response(200, json={"text": "", "segments": []})
        return success(request)

    app = create_app(config)
    worker = speech_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await worker.start()
        first = await wait_for_speech(http, identifier, headers)
        deadline = datetime.fromisoformat(first["analysis"]["text_deadline"])
        assert deadline == clock[0] + timedelta(seconds=60)
        clock[0] = deadline
        path = f"/v1/investigations/{identifier}"
        async with asyncio.timeout(5):
            while True:
                expired = (await http.get(path, headers=headers)).json()
                if expired["analysis"]["text_expired"]:
                    break
                await asyncio.sleep(0.01)
        assert expired["analysis"]["status"] == ("partial" if outcome == "speech" else "no_usable")
        assert expired["analysis"]["pending_modalities"] == []
        source = text_source(first, audiovisual)
        batch, completion = await send_text(http, path + "/device-text", headers, source)
        replies = await asyncio.gather(
            http.put(path + "/device-text/batches/0", headers=headers, json=batch),
            http.post(path + "/device-text/complete", headers=headers, json=completion),
        )
        assert all(response.status_code == 200 for response in replies)
        late = (await http.get(path, headers=headers)).json()
        await worker.stop()
        assert late["id"] == identifier
        assert late["speech"] == first["speech"]
        assert late["analysis"]["status"] == (
            "partial" if outcome in {"quota", "unavailable"} else "complete"
        )
        assert late["analysis"]["text_deadline"] == first["analysis"]["text_deadline"]
        assert len(calls) == 1
        assert late["report"] is None


@pytest.mark.parametrize("failed", [False, True])
async def test_checked_empty_frames_are_distinct_from_failed_or_missing_text(text_upload, failed):
    _, http, headers, path, source = text_upload
    await send_text(http, path, headers, source, empty=True, failed=failed)
    body = (await http.get(path.removesuffix("/device-text"), headers=headers)).json()
    assert body["analysis"]["status"] == "no_usable"
    assert body["analysis"]["pending_modalities"] == []
    assert body["analysis"]["analyzed_modalities"] == ([] if failed else ["text"])
    assert [gap["reason"] for gap in body["analysis"]["gaps"]] == (
        ["NO_AUDIO_TRACK", "DEVICE_TEXT_FRAME_FAILED"] if failed else ["NO_AUDIO_TRACK"]
    )


async def test_concurrent_due_resolution_and_late_delivery_cannot_discard_text(
    text_upload, harness, monkeypatch
):
    app, http, headers, path, source = text_upload
    before = (await http.get(path.removesuffix("/device-text"), headers=headers)).json()
    deadline = datetime.fromisoformat(before["analysis"]["text_deadline"])
    monkeypatch.setattr(analysis, "now", lambda: deadline)
    worker = harness.worker(None, stages=default_handlers(settings=app.state.settings))
    await worker.start()
    await send_text(http, path, headers, source)
    for _ in range(4):
        result = (await http.get(path.removesuffix("/device-text"), headers=headers)).json()
        assert result["analysis"]["status"] == "partial"
        assert result["analysis"]["pending_modalities"] == []
        assert len(result["analysis"]["text"]["batches"]) == 1
        assert [gap["reason"] for gap in result["analysis"]["gaps"]] == ["NO_AUDIO_TRACK"]
    await worker.stop()
    text_job = result["analysis"]["text"]["job_id"]
    removed = await http.delete(f"/v1/jobs/{text_job}", headers=headers)
    assert removed.status_code == 200
    late = await http.put(
        path + "/batches/1", headers=headers, json={**source, "frames": [frame(500)]}
    )
    assert late.status_code == 409
    assert (await http.get(path.removesuffix("/device-text"), headers=headers)).json()[
        "analysis"
    ] is None
