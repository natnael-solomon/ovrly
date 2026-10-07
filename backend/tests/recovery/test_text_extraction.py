"""Device text joins speech in the extraction ledger; disagreement is kept, not resolved."""

import asyncio
import json

import httpx
import pytest

from recovery.test_analysis import audiovisual, send_text, text_source
from recovery.test_capture_processing import finish, guest, package, send, start
from recovery.test_claim_extraction import completion, extraction
from recovery.test_device_text import frame
from recovery.test_media_validation import submit
from recovery.test_speech import speech_settings, wait_for_speech
from recovery.test_speech_extraction import (
    EXTRACTION,
    Pipeline,
    grounded,
    progress_closed,
    read,
    until,
)
from services.api.main import create_app
from services.media.runner import CommandLimits, run_command
from services.pipeline.producers import text_observations

__all__ = ["audiovisual"]


def conflicted(request):
    """One occurrence citing every observation, flagged as a speech/text disagreement."""
    observations = json.loads(json.loads(request.content)["messages"][1]["content"])["observations"]
    output = extraction()
    output["occurrences"][0].update(
        source_refs=[
            {"observation_id": item["id"], "start_char": 0, "end_char": len(item["text"])}
            for item in observations
        ],
        uncertainty_flags=["source-text-conflict"],
    )
    return httpx.Response(200, json=completion(output))


@pytest.fixture
async def silent_video(tmp_path):
    path = tmp_path / "silent.mkv"
    result = await run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:rate=4",
            "-t",
            "1",
            "-c:v",
            "ffv1",
            str(path),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert result.returncode == 0
    return path.read_bytes()


def statuses(body):
    return [
        (item["observation_id"].split(":")[1], item["status"], item["reason"])
        for item in body["extraction_progress"]["observations"]
    ]


def mentioning(pipeline, wording):
    """This test's extraction requests; the shared test database may hold earlier work."""
    return [call for call in pipeline.llm_calls if wording in call["messages"][1]["content"]]


async def test_pending_text_blocks_settlement_and_conflict_keeps_both_sources(
    harness, tmp_path, audiovisual
):
    config = speech_settings(harness, tmp_path, **EXTRACTION)
    pipeline = Pipeline(harness, config, conflicted)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await pipeline.worker.start()
        try:
            first = await wait_for_speech(http, identifier, headers)
            await asyncio.sleep(0.3)
            waiting = await read(http, headers, identifier)
            assert waiting["analysis"]["pending_modalities"] == ["text"]
            assert waiting["extraction_progress"]["closed"] is False
            assert [item["status"] for item in waiting["extraction_progress"]["observations"]] == [
                "pending"
            ]
            assert waiting["report"] is None
            path = f"/v1/investigations/{identifier}/device-text"
            batch, _ = await send_text(http, path, headers, text_source(first, audiovisual))
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
    text_id = batch["frames"][0]["text_observations"][0]["id"]
    assert [
        (item["observation_id"], item["timebase"], item["status"])
        for item in body["extraction_progress"]["observations"]
    ] == [
        ("upload:speech:0", "media", "processed"),
        (f"upload:text:{text_id}", "media", "processed"),
    ]
    [request] = mentioning(pipeline, "Invented screen text")
    observations = json.loads(request["messages"][1]["content"])["observations"]
    assert [(item["modality"], item["start_ms"], item["end_ms"]) for item in observations] == [
        ("speech", 125, 875),
        ("text", 250, 251),
    ]
    assert observations[1]["text"] == "Invented screen text"
    [claim] = body["report"]["claims"]
    assert claim["modality"] == "both"
    assert claim["interpretation"]["uncertainty_flags"] == ["source-text-conflict"]
    assert "Invented screen text" in claim["original_text"]
    assert claim["interval"] == {"start_ms": 125, "end_ms": 875, "timebase": "media"}
    assert len(pipeline.asr_calls) == 1


async def test_text_only_upload_settles_into_a_text_report(harness, tmp_path, silent_video):
    config = speech_settings(harness, tmp_path, **EXTRACTION)
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, silent_video)
        await pipeline.worker.start()
        try:
            pending = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "unavailable",
            )
            await asyncio.sleep(0.3)
            assert (await read(http, headers, identifier)).get("extraction_progress") is None
            path = f"/v1/investigations/{identifier}/device-text"
            await send_text(http, path, headers, text_source(pending, silent_video))
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
    assert statuses(body) == [("text", "processed", None)]
    [claim] = body["report"]["claims"]
    assert claim["modality"] == "text"
    assert body["speech"]["reason"] == "no_audio_track"
    assert pipeline.asr_calls == []


async def test_failed_text_admits_nothing_and_keeps_the_gap(harness, tmp_path, silent_video):
    config = speech_settings(harness, tmp_path, **EXTRACTION)
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, silent_video)
        await pipeline.worker.start()
        try:
            pending = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "unavailable",
            )
            path = f"/v1/investigations/{identifier}/device-text"
            await send_text(
                http, path, headers, text_source(pending, silent_video), failed=True, empty=True
            )
            await asyncio.sleep(0.5)
            body = await read(http, headers, identifier)
        finally:
            await pipeline.stop()
    assert body.get("extraction_progress") is None
    assert body["report"] is None
    assert [gap["reason"] for gap in body["analysis"]["gaps"]] == [
        "NO_AUDIO_TRACK",
        "DEVICE_TEXT_FRAME_FAILED",
    ]


async def test_text_after_settlement_is_reported_closed_not_hidden(harness, tmp_path, audiovisual):
    config = speech_settings(harness, tmp_path, text_grace_seconds=0, **EXTRACTION)
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await pipeline.worker.start()
        try:
            settled = await until(http, headers, identifier, progress_closed)
            path = f"/v1/investigations/{identifier}/device-text"
            source = text_source(settled, audiovisual)
            body = {**source, "frames": [frame()]}
            for _ in range(2):
                response = await http.put(path + "/batches/0", headers=headers, json=body)
                assert response.status_code == 200, response.text
            late = await read(http, headers, identifier)
            await asyncio.sleep(0.3)
        finally:
            await pipeline.stop()
    assert statuses(settled) == [("speech", "processed", None)]
    assert statuses(late) == [
        ("speech", "processed", None),
        ("text", "skipped", "input_closed"),
    ]
    assert mentioning(pipeline, "Invented screen text") == []
    assert late["report"]["claims"] == settled["report"]["claims"]


async def test_text_only_capture_chunks_feed_capture_timed_text(harness, tmp_path):
    config = speech_settings(harness, tmp_path, asr_audio_max_bytes=400_000, **EXTRACTION)
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        await pipeline.worker.start()
        try:
            identifier = await start(http, headers)
            data = package(0, modality="text")
            for _ in range(2):
                response = await send(http, headers, identifier, 0, data, modality="text")
                assert response.status_code == 200, response.text
            await finish(http, headers, identifier)
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
    progress = body["extraction_progress"]["observations"]
    assert [(item["timebase"], item["status"]) for item in progress] == [("capture", "processed")]
    assert progress[0]["observation_id"].startswith("capture:0:text:")
    assert (progress[0]["start_ms"], progress[0]["end_ms"]) == (500, 501)
    [claim] = body["report"]["claims"]
    assert (claim["modality"], claim["original_text"]) == ("text", "Synthetic headline 0")
    assert pipeline.asr_calls == []


def test_only_recognized_wording_becomes_observations_with_stable_identity():
    recognized = frame(400)
    frames = [recognized, frame(500, "failed", False), frame(600, text=False)]
    first = text_observations(frames, "capture:3", "capture")
    assert first == text_observations(frames, "capture:3", "capture")
    [observation] = first
    assert observation.id == f"capture:3:text:{recognized['text_observations'][0]['id']}"
    assert (observation.modality, observation.role, observation.speaker_id) == (
        "text",
        "target",
        None,
    )
    assert (observation.start_ms, observation.end_ms, observation.timebase) == (400, 401, "capture")
