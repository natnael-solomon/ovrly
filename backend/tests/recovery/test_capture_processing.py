"""Live capture chunks become timed speech and on-device text on the capture timebase."""

import asyncio
import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from test_contract_roundtrip import validate

from services.api.main import create_app
from services.asr import reservations
from services.asr.groq import GroqAdapter
from services.captures import stage_key
from services.jobs.faults import SimulatedCrash
from services.jobs.handlers import default_handlers
from services.jobs.models import job_results, jobs
from services.storage import LocalFilesystemStore

from .test_speech import speech_settings, success
from .test_speech_retry import exhausted, retry

DURATION = 10_000
SAMPLING = {
    "policy": "change_triggered",
    "probe_interval_ms": 500,
    "thumbnail_edge": 32,
    "change_threshold": 12,
    "heartbeat_ms": 5000,
    "max_frames_per_minute": 30,
    "frame_long_edge": 1280,
}
RECOGNIZER = {"name": "synthetic-recognizer", "version": "1"}


def package(
    index,
    /,
    *,
    end=None,
    modality="both",
    audio_ms=None,
    frames=None,
    observations=None,
    entries=None,
    **overrides,
):
    """A synthetic Android chunk package (``chunk.json`` plus optional raw PCM)."""
    seq = index
    start = seq * DURATION
    end = start + DURATION if end is None else end
    speech = modality in {"speech", "both"}
    pcm = b"\1\0" * (16 * ((end - start) if audio_ms is None else audio_ms)) if speech else None
    if frames is None:
        frames = (
            [
                {
                    "frame_pts": start + 500,
                    "status": "recognized",
                    "regions": 1,
                    "recognition_ms": 40,
                    "failed_regions": 0,
                }
            ]
            if modality in {"text", "both"}
            else []
        )
    if observations is None:
        observations = [
            {
                "text": f"Synthetic headline {seq}",
                "box": [100, 200, 900, 400],
                "frame_pts": f["frame_pts"],
            }
            for f in frames
            if f["status"] == "recognized"
        ]
    manifest = {
        "seq": seq,
        "start_ms": start,
        "end_ms": end,
        "timebase": "capture",
        "modality": modality,
        "audio": (
            {
                "file": "audio-16000-mono-s16le.pcm",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
                "channels": 1,
                "bytes": len(pcm),
            }
            if pcm is not None
            else None
        ),
        "frames_uploaded": False,
        "frames": frames,
        "text_observations": observations,
        "sampling": SAMPLING,
        "recognizer": RECOGNIZER,
    } | overrides
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        if entries is not None:
            for name, data in entries:
                archive.writestr(name, data)
        else:
            archive.writestr("chunk.json", json.dumps(manifest))
            if pcm is not None:
                archive.writestr("audio-16000-mono-s16le.pcm", pcm)
    return target.getvalue()


def capture_worker(harness, config, replay, *, quota_clock=None, faults=None):
    adapter = GroqAdapter(
        api_key="synthetic-offline-key",
        model=config.groq_model,
        max_audio_bytes=config.asr_audio_max_bytes,
        transport=httpx.MockTransport(replay),
    )
    return harness.worker(
        None,
        stages=default_handlers(
            LocalFilesystemStore(config.storage_dir),
            settings=config,
            asr_adapter=adapter,
            quota_clock=quota_clock,
        ),
        faults=faults,
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
    )


def capture_config(harness, tmp_path, **overrides):
    return speech_settings(harness, tmp_path, asr_audio_max_bytes=400_000, **overrides)


async def guest(http):
    response = await http.post("/v1/principals/guest", json={})
    return {"Authorization": "Bearer " + response.json()["credential"]["token"]}


async def start(http, headers):
    response = await http.post(
        "/v1/captures",
        json={"chunk_duration_ms": DURATION},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def send(http, headers, capture, seq, data, *, end=None, modality="both"):
    start_ms = seq * DURATION
    metadata = {
        "chunk": {
            "session_id": capture,
            "seq": seq,
            "interval": {
                "start_ms": start_ms,
                "end_ms": start_ms + DURATION if end is None else end,
                "timebase": "capture",
            },
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_type": "application/zip",
        },
        "modality": modality,
    }
    return await http.put(
        f"/v1/captures/{capture}/chunks/{seq}",
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            "content": ("chunk.zip", data, "application/zip"),
        },
        headers=headers,
    )


async def finish(http, headers, capture, duration=None, choice=True):
    body = {"continue_research": choice}
    if duration is not None:
        body["duration_ms"] = duration
    response = await http.post(f"/v1/captures/{capture}/close", json=body, headers=headers)
    assert response.status_code == 200, response.text


async def read(http, headers, capture):
    response = await http.get(f"/v1/investigations/{capture}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    validate.Validator().validate(body, "investigation.schema.json")
    return body


async def settled(http, headers, capture, chunks):
    async with asyncio.timeout(15):
        while True:
            body = await read(http, headers, capture)
            analysis = body["analysis"]
            if (
                analysis is not None
                and not analysis["pending_modalities"]
                and len(analysis["text"]["chunks"]) + _text_gaps(analysis) >= chunks
            ):
                return body
            await asyncio.sleep(0.02)


def _text_gaps(analysis):
    return sum(
        gap["modality"] == "text"
        and gap["reason"] in {"DEVICE_TEXT_MISSING", "CAPTURE_CHUNK_INVALID"}
        for gap in analysis["gaps"]
    )


def gaps(body):
    return sorted(
        (gap["modality"], gap["reason"], gap["interval"] and gap["interval"]["start_ms"])
        for gap in body["analysis"]["gaps"]
    )


def offsets(seq):
    return {
        "text": "A synthetic chime.",
        "interval": {
            "start_ms": seq * DURATION + 125,
            "end_ms": seq * DURATION + 875,
            "timebase": "capture",
        },
    }


@pytest.fixture
async def running(harness, tmp_path):
    """An API, a guest and a success-replaying worker, all torn down together."""
    config = capture_config(harness, tmp_path)
    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    app = create_app(config)
    worker = capture_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        await worker.start()
        yield http, headers, calls, app
        await worker.stop()


async def test_results_arrive_per_chunk_before_close_with_capture_offsets(running):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    assert (await send(http, headers, capture, 0, package(0))).status_code == 200
    body = await settled(http, headers, capture, 1)
    assert body["speech"]["status"] == "completed"
    assert body["speech"]["segments"] == [offsets(0)]
    validate.Validator().validate(body["speech"], "speech.schema.json")
    analysis = body["analysis"]
    assert analysis["status"] == "complete"
    assert analysis["text_deadline"] is None and analysis["text_expired"] is False
    assert analysis["analyzed_modalities"] == ["speech", "text"]
    [chunk] = analysis["text"]["chunks"]
    assert chunk["seq"] == 0
    assert chunk["interval"] == {"start_ms": 0, "end_ms": DURATION, "timebase": "capture"}
    assert chunk["frames"][0]["text_observations"][0]["text"] == "Synthetic headline 0"
    assert (
        await send(http, headers, capture, 1, package(1, end=DURATION + 4000), end=DURATION + 4000)
    ).status_code == 200
    await finish(http, headers, capture, duration=DURATION + 4000)
    body = await settled(http, headers, capture, 2)
    assert body["speech"]["segments"] == [offsets(0), offsets(1)]
    assert [c["seq"] for c in body["analysis"]["text"]["chunks"]] == [0, 1]
    assert body["analysis"]["gaps"] == []
    assert len(calls) == 2


async def test_duplicate_delivery_is_processed_once_and_conflicts_are_rejected(running):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    data = package(0)
    assert (await send(http, headers, capture, 0, data)).status_code == 200
    await settled(http, headers, capture, 1)
    assert (await send(http, headers, capture, 0, data)).status_code == 200
    conflict = await send(http, headers, capture, 0, package(0, audio_ms=5000))
    assert conflict.status_code == 409
    body = await settled(http, headers, capture, 1)
    assert body["speech"]["segments"] == [offsets(0)]
    assert len(calls) == 1


async def test_out_of_order_gaps_and_short_final_chunk_keep_missing_intervals(running):
    http, headers, _, _ = running
    capture = await start(http, headers)
    assert (
        await send(
            http, headers, capture, 2, package(2, end=2 * DURATION + 3000), end=2 * DURATION + 3000
        )
    ).status_code == 200
    assert (await send(http, headers, capture, 0, package(0))).status_code == 200
    body = await settled(http, headers, capture, 2)
    assert body["speech"]["segments"] == [offsets(0), offsets(2)]
    assert gaps(body) == [
        ("speech", "CAPTURE_CHUNK_MISSING", DURATION),
        ("text", "CAPTURE_CHUNK_MISSING", DURATION),
    ]
    assert body["analysis"]["status"] == "partial"
    await finish(http, headers, capture)
    body = await settled(http, headers, capture, 2)
    assert body["analysis"]["gaps"][0]["interval"] == {
        "start_ms": DURATION,
        "end_ms": 2 * DURATION,
        "timebase": "capture",
    }


async def test_mixed_modalities_report_text_and_speech_gaps_per_chunk(running):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    failed = [
        {
            "frame_pts": DURATION + 100,
            "status": "failed",
            "regions": 2,
            "recognition_ms": 9,
            "failed_regions": 2,
        },
        {
            "frame_pts": DURATION + 900,
            "status": "no_text_regions",
            "regions": 0,
            "recognition_ms": 3,
            "failed_regions": 0,
        },
    ]
    sent = [
        await send(http, headers, capture, 0, package(0, modality="speech"), modality="speech"),
        await send(
            http, headers, capture, 1, package(1, modality="text", frames=failed), modality="text"
        ),
    ]
    assert [response.status_code for response in sent] == [200, 200]
    body = await settled(http, headers, capture, 2)
    assert body["speech"]["segments"] == [offsets(0)]
    assert gaps(body) == [
        ("speech", "NO_AUDIO_TRACK", DURATION),
        ("text", "DEVICE_TEXT_FRAME_FAILED", None),
        ("text", "DEVICE_TEXT_MISSING", 0),
    ]
    assert body["analysis"]["analyzed_modalities"] == ["speech", "text"]
    assert body["analysis"]["status"] == "partial"
    assert len(calls) == 1


async def test_text_only_capture_never_contacts_the_provider(running):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    response = await send(http, headers, capture, 0, package(0, modality="text"), modality="text")
    assert response.status_code == 200
    body = await settled(http, headers, capture, 1)
    assert body["speech"] == {
        "status": "unavailable",
        "reason": "no_audio_track",
        "provider": None,
        "model": None,
        "processing_version": None,
        "source_sha256": None,
        "audio_sha256": None,
        "settings_sha256": None,
        "segments": [],
    }
    assert body["analysis"]["status"] == "partial"
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        {"entries": [("chunk.json", "{}"), ("frame.jpg", b"x")]},
        {"entries": [("other.json", "{}")]},
        {"seq": 4},
        {"start_ms": 1},
        {"modality": "text"},
        {"audio_ms": 12_000},
        {"timebase": "media"},
        {"frames_uploaded": True},
        {"observations": [{"text": "orphan", "box": [0, 0, 10, 10], "frame_pts": 9}]},
        {
            "frames": [
                {
                    "frame_pts": 20_000,
                    "status": "no_text_regions",
                    "regions": 0,
                    "recognition_ms": 1,
                    "failed_regions": 0,
                }
            ]
        },
        {
            "frames": [
                {
                    "frame_pts": 5,
                    "status": "failed",
                    "regions": 1,
                    "recognition_ms": 1,
                    "failed_regions": 0,
                }
            ],
            "observations": [],
        },
    ],
)
async def test_invalid_packages_become_gaps_without_provider_calls(running, change):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    assert (await send(http, headers, capture, 0, package(0, **change))).status_code == 200
    body = await settled(http, headers, capture, 1)
    assert gaps(body) == [
        ("speech", "CAPTURE_CHUNK_INVALID", 0),
        ("text", "CAPTURE_CHUNK_INVALID", 0),
    ]
    assert body["analysis"]["status"] == "no_usable"
    assert body["speech"]["status"] == "unavailable"
    assert calls == []


async def test_corrupt_zip_is_invalid(running):
    http, headers, calls, _ = running
    capture = await start(http, headers)
    assert (await send(http, headers, capture, 0, b"PK not really a zip")).status_code == 200
    body = await settled(http, headers, capture, 1)
    assert {gap["reason"] for gap in body["analysis"]["gaps"]} == {"CAPTURE_CHUNK_INVALID"}
    assert calls == []


async def test_stop_cancels_unprocessed_chunks_and_hides_analysis(harness, tmp_path):
    config = capture_config(harness, tmp_path)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        capture = await start(http, headers)
        assert (await send(http, headers, capture, 0, package(0))).status_code == 200
        await finish(http, headers, capture, choice=False)
        calls = []
        worker = capture_worker(
            harness, config, lambda request: calls.append(request) or success(request)
        )
        await worker.start()
        await asyncio.sleep(0.5)
        await worker.stop()
        body = await read(http, headers, capture)
        assert body["state"] == "cancelled"
        assert body["analysis"] is None
        assert calls == []
        assert (
            await http.post(
                f"/v1/investigations/{capture}/speech/retry",
                headers={**headers, "Idempotency-Key": "stopped"},
                json={"protocol_version": 1},
            )
        ).json()["outcome"] == "not_eligible"


@pytest.mark.parametrize(
    "stage,ending,gap",
    [
        ("asr", "cancel", ("speech", "ASR_UNAVAILABLE", 0)),
        ("asr", "delete", ("speech", "ASR_UNAVAILABLE", 0)),
        ("device_text", "cancel", ("text", "DEVICE_TEXT_MISSING", 0)),
        ("device_text", "delete", ("text", "DEVICE_TEXT_MISSING", 0)),
    ],
)
async def test_cancelled_or_deleted_chunk_job_leaves_a_gap_and_keeps_other_results(
    harness, tmp_path, stage, ending, gap
):
    config = capture_config(harness, tmp_path)
    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        capture = await start(http, headers)
        assert (await send(http, headers, capture, 0, package(0))).status_code == 200
        full = capture_worker(harness, config, replay)
        validating = harness.worker(
            None,
            stages={
                "media_validation": default_handlers(
                    LocalFilesystemStore(config.storage_dir), settings=config
                )["media_validation"]
            },
            database_timeout_seconds=10,
            worker_shutdown_seconds=10,
            job_lease_seconds=2,
        )
        await validating.start()
        digest = stage_key(uuid.UUID(capture), 0).input_hash
        async with asyncio.timeout(10):
            while True:
                async with app.state.database.engine.connect() as connection:
                    job_id = await connection.scalar(
                        select(jobs.c.id).where(jobs.c.stage == stage, jobs.c.input_hash == digest)
                    )
                if job_id is not None:
                    break
                await asyncio.sleep(0.02)
        await validating.stop()
        path = f"/v1/jobs/{job_id}"
        response = await (
            http.post(path + "/cancel", headers=headers)
            if ending == "cancel"
            else http.delete(path, headers=headers)
        )
        assert response.status_code == 200, response.text
        await full.start()
        body = await settled(http, headers, capture, 1)
        await full.stop()
    assert gap in gaps(body)
    assert body["analysis"]["status"] == "partial"
    if stage == "asr":
        assert calls == []
        assert body["analysis"]["text"]["chunks"][0]["seq"] == 0
    else:
        assert body["speech"]["segments"] == [offsets(0)]
        assert body["analysis"]["text"]["chunks"] == []


@pytest.mark.parametrize(
    "point", ["claimed", "before_provider_call", "after_provider_call", "after_artifact_store"]
)
async def test_crash_recovery_never_repeats_an_uncertain_chunk_call(harness, tmp_path, point):
    class CrashOnce:
        async def checkpoint(self, name, job):
            if job.key.stage == "asr" and name == point:
                raise SimulatedCrash

    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    config = capture_config(harness, tmp_path)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        capture = await start(http, headers)
        assert (await send(http, headers, capture, 0, package(0))).status_code == 200
        crashing = capture_worker(harness, config, replay, faults=CrashOnce())
        await crashing.start()
        with pytest.raises(SimulatedCrash):
            await asyncio.wait_for(crashing.wait(), 10)
        recovering = capture_worker(harness, config, replay)
        await recovering.start()
        body = await settled(http, headers, capture, 1)
        await recovering.stop()
    if point in {"before_provider_call", "after_provider_call"}:
        assert body["speech"]["status"] == "unavailable"
        assert body["speech"]["reason"] == "unknown_outcome"
        assert ("speech", "ASR_OUTCOME_UNKNOWN", 0) in gaps(body)
    else:
        assert body["speech"]["segments"] == [offsets(0)]
    assert len(calls) == (0 if point == "before_provider_call" else 1)
    assert body["analysis"]["text"]["chunks"][0]["seq"] == 0


async def test_shared_quota_blocks_then_retry_requeues_only_missing_chunks(
    harness, tmp_path, monkeypatch
):
    config = capture_config(harness, tmp_path, asr_requests_per_day=1)
    clock = [datetime(2026, 10, 6, tzinfo=UTC)]
    monkeypatch.setattr(reservations, "now", lambda: clock[0], raising=False)
    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        capture = await start(http, headers)
        worker = capture_worker(harness, config, replay, quota_clock=lambda: clock[0])
        await worker.start()
        assert (await send(http, headers, capture, 0, package(0))).status_code == 200
        await settled(http, headers, capture, 1)
        assert (await send(http, headers, capture, 1, package(1))).status_code == 200
        blocked = await settled(http, headers, capture, 2)
        await worker.stop()
        assert blocked["speech"]["segments"] == [offsets(0)]
        assert ("speech", "ASR_QUOTA_EXHAUSTED", DURATION) in gaps(blocked)
        assert len(calls) == 1
        assert await retry(http, capture, headers, "too-early") == "quota_exhausted"
        clock[0] += timedelta(days=1)
        assert await retry(http, capture, headers, "after-reset") == "accepted"
        assert await retry(http, capture, headers, "after-reset") == "accepted"
        assert await retry(http, capture, headers, "second") == "in_progress"
        restarted = capture_worker(harness, config, replay, quota_clock=lambda: clock[0])
        await restarted.start()
        async with asyncio.timeout(15):
            while True:
                done = await read(http, headers, capture)
                if done["speech"]["status"] == "completed" and len(done["speech"]["segments"]) > 1:
                    break
                await asyncio.sleep(0.02)
        await restarted.stop()
        assert done["speech"]["segments"] == [offsets(0), offsets(1)]
        assert len(calls) == 2
        assert await retry(http, capture, headers, "third") == "already_complete"
        outsider = await guest(http)
        response = await http.post(
            f"/v1/investigations/{capture}/speech/retry",
            headers={**outsider, "Idempotency-Key": "outsider"},
            json={"protocol_version": 1},
        )
        assert response.status_code == 404


async def test_disabled_asr_reports_a_gap_and_keeps_text(harness, tmp_path):
    config = capture_config(harness, tmp_path, asr_enabled=False)
    calls = []
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        capture = await start(http, headers)
        worker = capture_worker(
            harness, config, lambda request: calls.append(request) or exhausted(request)
        )
        await worker.start()
        assert (await send(http, headers, capture, 0, package(0))).status_code == 200
        body = await settled(http, headers, capture, 1)
        await worker.stop()
    assert body["speech"]["reason"] == "disabled"
    assert gaps(body) == [("speech", "ASR_DISABLED", 0)]
    assert body["analysis"]["analyzed_modalities"] == ["text"]
    assert calls == []


async def test_audio_overrun_is_clamped_inside_the_chunk(running):
    http, headers, _, app = running

    capture = await start(http, headers)
    assert (
        await send(http, headers, capture, 0, package(0, audio_ms=DURATION + 640))
    ).status_code == 200
    body = await settled(http, headers, capture, 1)
    assert body["speech"]["segments"] == [offsets(0)]
    async with app.state.database.engine.connect() as connection:
        prepared = await connection.scalar(
            select(job_results.c.result)
            .join(jobs, jobs.c.id == job_results.c.job_id)
            .where(
                jobs.c.input_hash == stage_key(uuid.UUID(capture), 0).input_hash,
                jobs.c.stage == "media_validation",
            )
        )
    assert prepared["package"] == {
        "version": 1,
        "modality": "both",
        "audio": {"bytes": (DURATION + 640) * 32, "duration_ms": DURATION + 640},
        "frames": 1,
    }
