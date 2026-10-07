import asyncio
import hashlib
import io
import os
import sys
import uuid
import wave

import httpx
import pytest

from services.api.main import create_app
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.media.runner import CommandLimits, run_command


def media_settings(harness, **overrides):
    return harness.settings(
        database_timeout_seconds=10, worker_shutdown_seconds=10, job_lease_seconds=2, **overrides
    )


def media_worker(harness, config, **overrides):
    return harness.worker(
        None,
        stages=default_handlers(settings=config),
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
        **overrides,
    )


def executable(tmp_path, code, name="media-tool"):
    tool = tmp_path / name
    tool.write_text(f"#!{sys.executable} -S\n" + code)
    tool.chmod(0o700)
    return str(tool)


def audio_bytes(seconds=1):
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0" * 8000 * 2 * 2 * seconds)
    return target.getvalue()


async def submit(http, content):
    guest = await http.post("/v1/principals/guest", json={})
    headers = {"Authorization": "Bearer " + guest.json()["credential"]["token"]}
    declared = await http.post(
        "/v1/uploads",
        headers=headers,
        json={"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()},
    )
    assert declared.status_code == 201, declared.text
    upload = declared.json()
    stored = await http.put(upload["target"], headers=headers, content=content)
    assert stored.status_code == 204, stored.text
    completed = await http.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    created = await http.post(
        "/v1/investigations",
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={"source": {"kind": "upload", "upload_id": upload["id"]}},
    )
    assert created.status_code == 202, created.text
    return created.json()["id"], headers


async def wait_for_media(http, identifier, headers):
    async with asyncio.timeout(10):
        while True:
            response = await http.get(f"/v1/investigations/{identifier}", headers=headers)
            assert response.status_code == 200
            body = response.json()
            if body["state"] in {"failed", "cancelled"} or "media" in body["coverage"]:
                return body
            await asyncio.sleep(0.02)


async def test_uploaded_audio_is_prepared_and_visible_without_claiming_analysis(harness, tmp_path):
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["stage"] == "media_validation"
        assert result["state"] == "running"
        assert result["error"] is None
        assert result["coverage"] == {
            "status": "not_started",
            "total_ms": 1000,
            "media": {
                "has_audio": True,
                "has_video": False,
                "speech_status": "pending",
                "text_status": "pending",
                "speech_unavailable_reason": None,
            },
        }
        listed = await http.get("/v1/investigations", headers=headers)
        assert listed.json()["items"][0]["coverage"] == result["coverage"]


@pytest.mark.parametrize("maximum", [32043, 32044])
async def test_audio_byte_cap_accepts_exact_fit_without_truncation(harness, tmp_path, maximum):
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        asr_audio_max_bytes=maximum,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        result = await wait_for_media(http, identifier, headers)
        if maximum == 32043:
            assert result["state"] == "failed"
            assert result["error"]["code"] == "MEDIA_SIZE_LIMIT_EXCEEDED"
            assert not list(config.artifacts_dir.rglob("*.wav"))
        else:
            assert result["state"] == "running", result
            [artifact] = config.artifacts_dir.rglob("*.wav")
            assert artifact.stat().st_size == 32044
            with wave.open(str(artifact), "rb") as audio:
                assert audio.getframerate() == 16000
                assert audio.getnchannels() == 1
                assert audio.getsampwidth() == 2
                assert audio.getnframes() == 16000
                assert len(audio.readframes(16001)) == 32000


@pytest.mark.parametrize("ending", ["cancel", "delete", "timeout"])
async def test_running_media_is_visible_and_reaps_tools_on_stop(harness, tmp_path, ending):
    marker = tmp_path / "pid"
    tool = executable(
        tmp_path,
        "import os, pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
    )

    class RecordMedia:
        job = None

        async def checkpoint(self, name, job):
            if name is Checkpoint.CLAIMED and job.key.stage == "media_validation":
                self.job = job

    faults = RecordMedia()
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        ffprobe_path=tool,
        media_probe_timeout_seconds=1,
        job_retry_transient_attempts=0,
    )
    app = create_app(config, faults=faults)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        async with asyncio.timeout(5):
            while not marker.exists():  # noqa: ASYNC110 - observing an external tool's marker
                await asyncio.sleep(0.01)
        running = await http.get(f"/v1/investigations/{identifier}", headers=headers)
        assert running.json()["stage"] == "media_validation"
        assert running.json()["state"] == "running"
        if ending == "cancel":
            assert faults.job is not None
            await harness.queue.request_cancel(faults.job.id)
        elif ending == "delete":
            assert faults.job is not None
            await harness.queue.delete(faults.job.id)
        result = await wait_for_media(http, identifier, headers)
        assert result["coverage"] == {"status": "not_started"}
        if ending in {"cancel", "delete"}:
            assert result["state"] == "cancelled"
        else:
            assert result["state"] == "failed"
            assert result["error"]["code"] == "MEDIA_PROCESSING_TIMEOUT"
        await app.state.worker.stop()
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
        assert list(config.artifacts_dir.iterdir()) == []


@pytest.mark.parametrize("change", ["missing", "different", "directory", "bigger", "new-cap"])
async def test_completed_source_is_checked_again_before_processing(harness, tmp_path, change):
    config = media_settings(
        harness, storage_dir=tmp_path / "uploads", artifacts_dir=tmp_path / "artifacts"
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        [stored] = config.storage_dir.iterdir()
        stored.unlink()
        if change == "different":
            stored.write_bytes(b"changed")
        elif change == "directory":
            stored.mkdir()
        elif change == "bigger":
            stored.write_bytes(audio_bytes() + b"extra")
        elif change == "new-cap":
            stored.write_bytes(audio_bytes())
            config = config.model_copy(update={"upload_max_bytes": 100})
        worker = media_worker(harness, config)
        await worker.start()
        result = await wait_for_media(http, identifier, headers)
        await worker.stop()
        assert result["state"] == "failed"
        assert result["error"]["code"] == (
            "MEDIA_SIZE_LIMIT_EXCEEDED" if change in {"bigger", "new-cap"} else "INVALID_MEDIA"
        )
        assert result["coverage"] == {"status": "not_started"}
        assert not list(config.artifacts_dir.rglob("*.wav"))


async def test_long_decoded_audio_does_not_leave_a_published_artifact(harness, tmp_path):
    probe = executable(
        tmp_path,
        "import json, subprocess, sys\n"
        'data = json.loads(subprocess.check_output(["ffprobe", *sys.argv[1:]]))\n'
        'data["format"]["duration"] = "1"\n'
        "print(json.dumps(data))\n",
    )
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        max_shared_duration_seconds=1,
        ffprobe_path=probe,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes(2))
        result = await wait_for_media(http, identifier, headers)
        assert result["error"]["code"] == "DURATION_LIMIT_EXCEEDED"
        assert not list(config.artifacts_dir.rglob("*.wav"))


async def test_partial_audio_is_not_published_even_if_extractor_exits_successfully(
    harness, tmp_path
):
    tool = executable(
        tmp_path,
        "import pathlib, sys, wave\n"
        "target = pathlib.Path(sys.argv[-1])\n"
        'with wave.open(str(target), "wb") as audio:\n'
        '    audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))\n'
        '    audio.writeframes(b"\\0" * 32000)\n'
        "target.write_bytes(target.read_bytes()[:100])\n",
    )
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        ffmpeg_path=tool,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "failed"
        assert result["error"]["code"] == "INVALID_MEDIA"
        assert not list(config.artifacts_dir.rglob("*.wav"))


@pytest.mark.parametrize("stage", ["intake", "media_validation"])
async def test_cancel_at_publication_cannot_advance_the_pipeline(harness, tmp_path, stage):
    class CancelBeforePublish:
        async def checkpoint(self, name, job):
            if name is Checkpoint.BEFORE_PUBLISH and job.key.stage == stage:
                await harness.queue.request_cancel(job.id)

    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
    )
    app = create_app(config, faults=CancelBeforePublish())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "cancelled"
        assert result["stage"] == stage
        assert result["coverage"] == {"status": "not_started"}
        if stage == "intake":
            assert not config.artifacts_dir.exists()


@pytest.mark.parametrize("stage", ["intake", "media_validation"])
async def test_crashed_preparation_recovers_without_regression_or_artifact_overwrite(
    harness, tmp_path, stage
):
    class CrashBeforePublish:
        async def checkpoint(self, name, job):
            if name is Checkpoint.BEFORE_PUBLISH and job.key.stage == stage:
                raise SimulatedCrash("fixture crash")

    config = media_settings(
        harness, storage_dir=tmp_path / "uploads", artifacts_dir=tmp_path / "artifacts"
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        worker = media_worker(harness, config, faults=CrashBeforePublish())
        await worker.start()
        with pytest.raises(SimulatedCrash):
            await worker.wait()
        during = await http.get(f"/v1/investigations/{identifier}", headers=headers)
        assert during.json()["stage"] == stage
        assert during.json()["coverage"] == {"status": "not_started"}
        artifacts = {
            path: (path.read_bytes(), path.stat().st_ino)
            for path in config.artifacts_dir.rglob("*.wav")
        }
        recovering = media_worker(harness, config)
        await recovering.start()
        result = await wait_for_media(http, identifier, headers)
        await recovering.stop()
        assert result["stage"] == "media_validation" and result["state"] == "running"
        assert len(list(config.artifacts_dir.rglob("*.wav"))) == 1
        for path, (content, inode) in artifacts.items():
            assert path.read_bytes() == content and path.stat().st_ino == inode
        unavailable = config.model_copy(update={"ffprobe_path": "must-not-validate-again"})
        restarted = media_worker(harness, unavailable)
        await restarted.start()
        after = await http.get(f"/v1/investigations/{identifier}", headers=headers)
        await restarted.stop()
        assert after.json() == result


@pytest.mark.parametrize(
    ("content", "overrides", "code"),
    [
        (b"not media", {}, "INVALID_MEDIA"),
        (audio_bytes(2)[:-32000], {}, "INVALID_MEDIA"),
        (audio_bytes(2), {"max_shared_duration_seconds": 1}, "DURATION_LIMIT_EXCEEDED"),
        (audio_bytes(), {"media_output_max_bytes": 16}, "INVALID_MEDIA"),
        (audio_bytes(), {"ffprobe_path": "missing-ovrly-ffprobe"}, "MEDIA_PROCESSING_UNAVAILABLE"),
        (audio_bytes(), {"ffmpeg_path": "missing-ovrly-ffmpeg"}, "MEDIA_PROCESSING_UNAVAILABLE"),
    ],
    ids=[
        "invalid",
        "truncated-source",
        "too-long",
        "probe-output",
        "missing-probe",
        "missing-extractor",
    ],
)
async def test_media_failures_are_safe_and_visible(harness, tmp_path, content, overrides, code):
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        **overrides,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, content)
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "failed"
        assert result["stage"] == "media_validation"
        assert result["error"]["code"] == code
        assert result["coverage"] == {"status": "not_started"}
        assert str(tmp_path) not in str(result)
        assert "ffprobe" not in str(result) and "ffmpeg" not in str(result)
        listed = await http.get("/v1/investigations", headers=headers)
        assert listed.json()["items"][0]["error"] == result["error"]
        assert not list(config.artifacts_dir.rglob("*.wav"))


async def test_silent_video_preserves_pending_text_and_absent_speech(harness, tmp_path):
    video = tmp_path / "silent.avi"
    generated = await run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:rate=1",
            "-t",
            "1",
            "-c:v",
            "mpeg4",
            str(video),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0, generated.stderr
    ffprobe_without_format_duration = executable(
        tmp_path,
        """
import json

print(json.dumps({"format": {}, "streams": [{"codec_type": "video"}]}))
""",
    )
    ffmpeg_with_decoded_duration = executable(
        tmp_path,
        """
print("out_time_us=1000000")
print("progress=end")
""",
        "media-ffmpeg",
    )
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        ffprobe_path=ffprobe_without_format_duration,
        ffmpeg_path=ffmpeg_with_decoded_duration,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, video.read_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "running"
        assert result["error"] is None
        assert result["coverage"] == {
            "status": "not_started",
            "total_ms": 1000,
            "media": {
                "has_audio": False,
                "has_video": True,
                "speech_status": "unavailable",
                "text_status": "pending",
                "speech_unavailable_reason": "no_audio_track",
            },
        }
        assert not list(config.artifacts_dir.rglob("*.wav"))


async def test_video_cannot_hide_excess_duration_in_container_metadata(harness, tmp_path):
    video = tmp_path / "understated.mkv"
    generated = await run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:rate=1",
            "-t",
            "3",
            "-c:v",
            "ffv1",
            str(video),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0, generated.stderr
    ffprobe_with_understated_duration = executable(
        tmp_path,
        """
import json
import subprocess
import sys

completed = subprocess.run(["ffprobe", *sys.argv[1:]], capture_output=True, check=False)
if completed.returncode != 0:
    sys.stdout.buffer.write(completed.stdout)
    sys.stderr.buffer.write(completed.stderr)
    sys.exit(completed.returncode)
payload = json.loads(completed.stdout)
payload.setdefault("format", {})["duration"] = "1.000000"
print(json.dumps(payload))
""",
    )
    ffmpeg_with_decoded_duration = executable(
        tmp_path,
        """
print("out_time_us=2000000")
print("progress=end")
""",
        "media-ffmpeg",
    )
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        ffprobe_path=ffprobe_with_understated_duration,
        ffmpeg_path=ffmpeg_with_decoded_duration,
        max_shared_duration_seconds=1,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, video.read_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "failed"
        assert result["error"]["code"] == "DURATION_LIMIT_EXCEEDED"
        assert not list(config.artifacts_dir.rglob("*.wav"))


@pytest.mark.parametrize(
    ("tool_name", "script"),
    [
        ("ffprobe_path", 'print("{}")\n'),
        (
            "ffprobe_path",
            'print(\'{"format":{"duration":"NaN"},"streams":[{"codec_type":"audio"}]}\')\n',
        ),
        ("ffmpeg_path", "import sys; sys.exit(1)\n"),
        (
            "ffmpeg_path",
            'import pathlib, sys; pathlib.Path(sys.argv[-1]).write_bytes(b"invalid wav")\n',
        ),
        (
            "ffmpeg_path",
            "import sys, wave\n"
            'with wave.open(sys.argv[-1], "wb") as out:\n'
            '    out.setparams((2, 2, 16000, 0, "NONE", "not compressed"))\n'
            '    out.writeframes(b"\\0" * 64000)\n',
        ),
        (
            "ffmpeg_path",
            "import sys, wave\n"
            'with wave.open(sys.argv[-1], "wb") as out:\n'
            '    out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))\n',
        ),
    ],
    ids=["missing-metadata", "nonfinite-duration", "decode-failed", "bad-wave", "stereo", "empty"],
)
async def test_invalid_tool_results_cannot_become_usable_artifacts(
    harness, tmp_path, tool_name, script
):
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        **{tool_name: executable(tmp_path, script)},
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        result = await wait_for_media(http, identifier, headers)
        assert result["state"] == "failed"
        assert result["error"]["code"] == "INVALID_MEDIA"
        assert not list(config.artifacts_dir.rglob("*.wav"))
