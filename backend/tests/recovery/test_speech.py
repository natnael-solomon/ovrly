"""Public investigation reads, real PostgreSQL/worker/media, invented provider HTTP."""

import asyncio
import io
import sys
import uuid
import wave
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from test_contract_roundtrip import validate

from recovery.test_media_validation import audio_bytes, media_settings, submit, wait_for_media
from recovery.test_privacy import expire, sweep
from services.api.main import create_app
from services.asr.groq import GroqAdapter
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.jobs.retries import RetryPolicy
from services.media.runner import CommandLimits, run_command
from services.models import asr_requests, investigations


def speech_settings(harness, tmp_path, **overrides):
    values = {
        "storage_dir": tmp_path / "uploads",
        "artifacts_dir": tmp_path / "artifacts",
        "asr_enabled": True,
        "groq_api_key": "synthetic-offline-key",
        "groq_model": "synthetic-model",
        "groq_fallback_model": "synthetic-fallback",
        "groq_account_id": "synthetic-" + uuid.uuid4().hex,
        "asr_limits_verified_on": date(2026, 10, 5),
        "asr_audio_max_bytes": 100000,
        "asr_requests_per_minute": 100,
        "asr_requests_per_day": 100,
        "asr_audio_seconds_per_hour": 100,
        "asr_audio_seconds_per_day": 100,
        "asr_minimum_billable_seconds": 1,
    }
    return media_settings(harness, **(values | overrides))


def speech_worker(harness, config, replay, *, quota_clock=None, **overrides):
    adapter = GroqAdapter(
        api_key="synthetic-offline-key",
        model=config.groq_model,
        max_audio_bytes=config.asr_audio_max_bytes,
        transport=httpx.MockTransport(replay),
    )
    return harness.worker(
        None,
        stages=default_handlers(settings=config, asr_adapter=adapter, quota_clock=quota_clock),
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
        **overrides,
    )


def success(_):
    return httpx.Response(
        200,
        json={
            "text": "A synthetic chime.",
            "segments": [{"start": 0.125, "end": 0.875, "text": "A synthetic chime."}],
        },
    )


def dump_tasks():
    """Write every task's stack so a timed-out wait shows where the worker is busy."""
    for task in asyncio.all_tasks():
        out = io.StringIO()
        task.print_stack(file=out)
        sys.stderr.write(f"task {task.get_name()}:\n{out.getvalue()}\n")


async def wait_for_speech(http, identifier, headers):
    # One wait covers intake, ffprobe/ffmpeg preparation and every speech attempt. Hosted CI
    # showed this stalling past 10 s on slow runners (test_known_transient_outcomes on main
    # bf7c1cd, test_repeated_quota_failure in #126's baseline): both timed out at the first
    # wait, before any retry, with the worker still busy inside a stage. A local repro with
    # real PostgreSQL ruled out cancelling the in-flight heartbeat (350 runs, CPU-starved
    # included), so the wait is widened and the task stacks are dumped if it still expires.
    try:
        async with asyncio.timeout(30):
            while True:
                response = await http.get(f"/v1/investigations/{identifier}", headers=headers)
                assert response.status_code == 200, response.text
                body = response.json()
                if (body.get("speech") or {}).get("status") in {"completed", "unavailable"}:
                    return body
                await asyncio.sleep(0.02)
    except TimeoutError:
        dump_tasks()
        raise


def model_of(request):
    """The model named in a multipart transcription request."""
    content = request.content
    marker = b'name="model"\r\n\r\n'
    start = content.index(marker) + len(marker)
    return content[start : content.index(b"\r\n", start)].decode()


def speech_error(body):
    """A terminal speech gap no longer fails the independent text modality."""
    if body["analysis"] is None:
        return body["error"]["code"]
    reasons = {gap["reason"] for gap in body["analysis"]["gaps"]}
    return "ASR_QUOTA_EXHAUSTED" if "ASR_QUOTA_EXHAUSTED" in reasons else "ASR_UNAVAILABLE"


async def test_uploaded_speech_is_readable_only_to_owner_and_keeps_media_coverage(
    harness, tmp_path
):
    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, success)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        body = await wait_for_speech(http, identifier, headers)
        await worker.stop()
        assert body["stage"] == "asr"
        assert body["state"] == "running"
        assert body["coverage"]["total_ms"] == 1000
        assert body["coverage"]["media"]["text_status"] == "pending"
        assert body["speech"]["status"] == "completed"
        assert body["speech"]["provider"] == "groq"
        assert body["speech"]["model"] == "synthetic-model"
        assert body["speech"]["processing_version"] == 1
        validate.Validator().validate(body["speech"], "speech.schema.json")
        assert body["speech"]["segments"] == [
            {
                "text": "A synthetic chime.",
                "interval": {"start_ms": 125, "end_ms": 875, "timebase": "media"},
            }
        ]
        listed = await http.get("/v1/investigations", headers=headers)
        assert listed.json()["items"][0]["speech"] == body["speech"]
        other = (await http.post("/v1/principals/guest", json={})).json()
        denied = await http.get(
            f"/v1/investigations/{identifier}",
            headers={"Authorization": "Bearer " + other["credential"]["token"]},
        )
        assert denied.status_code == 404


async def test_workspace_retention_erases_audio_and_saved_speech_outcome(harness, tmp_path):
    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, success)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        body = await wait_for_speech(http, identifier, headers)
        await worker.stop()
        assert body["speech"]["status"] == "completed"
        assert list(config.artifacts_dir.rglob("*.wav"))
        async with app.state.database.engine.connect() as connection:
            owner = await connection.scalar(
                select(investigations.c.owner_id).where(
                    investigations.c.id == uuid.UUID(identifier)
                )
            )
        await expire(app, owner)
        await sweep(app)
        assert not list(config.artifacts_dir.rglob("*.wav"))
        async with app.state.database.engine.connect() as connection:
            outcomes = (
                (
                    await connection.execute(
                        select(asr_requests.c.result).where(
                            asr_requests.c.account_id == config.groq_account_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert outcomes == [None]
        assert (
            await http.get(f"/v1/investigations/{identifier}", headers=headers)
        ).status_code == 401


async def test_stub_report_mode_does_not_launch_hosted_uploaded_speech(harness, tmp_path):
    from services.pipeline.registry import worker_handlers

    config = speech_settings(harness, tmp_path, stub_reports=True)
    app = create_app(config)
    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    worker = speech_worker(harness, config, replay)
    worker.handlers = worker_handlers(worker.database, config, worker.handlers)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        async with asyncio.timeout(10):
            while True:
                body = (await http.get(f"/v1/investigations/{identifier}", headers=headers)).json()
                if body["job"]["state"] == "published":
                    break
                await asyncio.sleep(0.02)
        await worker.stop()
        assert body["report"]["fixture"] is True
        assert body["stage"] == body["job"]["stage"] == "intake"
        assert calls == []


@pytest.mark.parametrize(
    "limit",
    [
        "asr_requests_per_minute",
        "asr_requests_per_day",
        "asr_audio_seconds_per_hour",
        "asr_audio_seconds_per_day",
    ],
)
async def test_concurrent_workers_and_restart_cannot_bypass_account_model_quota(
    harness, tmp_path, limit
):
    config = speech_settings(harness, tmp_path, **{limit: 1})
    app = create_app(config)
    workers = [speech_worker(harness, config, success) for _ in range(2)]
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        submissions = [await submit(http, audio_bytes()) for _ in range(3)]
        await asyncio.gather(*(worker.start() for worker in workers))
        results = await asyncio.gather(
            *(wait_for_speech(http, identifier, headers) for identifier, headers in submissions)
        )
        await asyncio.gather(*(worker.stop() for worker in workers))
        statuses = sorted(body["speech"]["status"] for body in results)
        assert statuses == ["completed", "completed", "unavailable"]
        completed = [body for body in results if body["speech"]["status"] == "completed"]
        # Each model has its own windows: one chunk each, then neither has room.
        assert sorted(body["speech"]["model"] for body in completed) == [
            "synthetic-fallback",
            "synthetic-model",
        ]
        [blocked] = [body for body in results if body["speech"]["status"] == "unavailable"]
        assert speech_error(blocked) == "ASR_QUOTA_EXHAUSTED"
        assert blocked["speech"]["reason"] == "quota_exhausted"
        identifier, headers = await submit(http, audio_bytes())
        restarted = speech_worker(harness, config, success)
        await restarted.start()
        blocked_again = await wait_for_speech(http, identifier, headers)
        await restarted.stop()
        assert speech_error(blocked_again) == "ASR_QUOTA_EXHAUSTED"


async def test_disabled_hosted_speech_is_explicit_without_contacting_provider(harness, tmp_path):
    config = media_settings(
        harness,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
    )
    app = create_app(config)
    worker = speech_worker(
        harness, config, lambda _: pytest.fail("Disabled speech contacted the provider")
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        body = await wait_for_media(http, identifier, headers)
        await worker.stop()
        assert body["stage"] == "media_validation"
        assert body["speech"]["status"] == "unavailable"
        assert body["speech"]["reason"] == "disabled"


@pytest.mark.parametrize(
    "point",
    [
        "claimed",
        "before_provider_call",
        "after_provider_call",
        "after_artifact_store",
        "before_publish",
    ],
)
async def test_crash_recovery_never_repeats_uncertain_or_completed_speech(harness, tmp_path, point):
    class CrashOnce:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == point:
                raise SimulatedCrash

    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        worker = speech_worker(harness, config, replay, faults=CrashOnce())
        await worker.start()
        with pytest.raises(SimulatedCrash):
            await asyncio.wait_for(worker.wait(), 10)
        recovering = speech_worker(harness, config, replay)
        await recovering.start()
        body = await wait_for_speech(http, identifier, headers)
        await recovering.stop()
        assert body["speech"]["status"] == "completed"
        if point in {"before_provider_call", "after_provider_call"}:
            # The primary may have run: it is never resent, and the fallback is tried once.
            assert body["speech"]["model"] == "synthetic-fallback"
            assert [model_of(call) for call in calls] == (
                ["synthetic-fallback"]
                if point == "before_provider_call"
                else ["synthetic-model", "synthetic-fallback"]
            )
        else:
            assert body["speech"]["model"] == "synthetic-model"
            assert [model_of(call) for call in calls] == ["synthetic-model"]


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("crash", [False, True])
async def test_primary_rate_limit_or_outage_falls_back_once_and_survives_a_crash(
    harness, tmp_path, status, crash
):
    class CrashAfterOutcome:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == Checkpoint.AFTER_ARTIFACT_STORE:
                raise SimulatedCrash

    calls = []

    def replay(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, json={}, headers={"Retry-After": "0"})
        return success(request)

    config = speech_settings(harness, tmp_path)
    policy = RetryPolicy(backoff_seconds=0.01, max_backoff_seconds=0.02)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        worker = speech_worker(
            harness,
            config,
            replay,
            retry_policy=policy,
            faults=CrashAfterOutcome() if crash else None,
        )
        await worker.start()
        if crash:
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(worker.wait(), 10)
            worker = speech_worker(harness, config, replay, retry_policy=policy)
            await worker.start()
        body = await wait_for_speech(http, identifier, headers)
        await worker.stop()
        assert body["speech"]["status"] == "completed", body
        assert body["speech"]["model"] == "synthetic-fallback"
        assert [model_of(call) for call in calls] == ["synthetic-model", "synthetic-fallback"]


async def test_quota_reset_only_admits_new_work_never_resumes_blocked_speech(harness, tmp_path):
    now = datetime.now(UTC)
    config = speech_settings(harness, tmp_path, asr_requests_per_day=1)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        worker = speech_worker(harness, config, success, quota_clock=lambda: now)
        await worker.start()
        first = await submit(http, audio_bytes())
        assert (await wait_for_speech(http, *first))["speech"]["model"] == "synthetic-model"
        second = await submit(http, audio_bytes())
        assert (await wait_for_speech(http, *second))["speech"]["model"] == "synthetic-fallback"
        blocked = await submit(http, audio_bytes())
        before = await wait_for_speech(http, *blocked)
        assert speech_error(before) == "ASR_QUOTA_EXHAUSTED"
        await worker.stop()
        now += timedelta(days=1, seconds=1)
        restarted = speech_worker(harness, config, success, quota_clock=lambda: now)
        await restarted.start()
        new = await submit(http, audio_bytes())
        assert (await wait_for_speech(http, *new))["speech"]["status"] == "completed"
        assert await wait_for_speech(http, *blocked) == before
        await restarted.stop()


@pytest.mark.parametrize("ending", ["cancel", "delete"])
@pytest.mark.parametrize(
    "point", ["before_provider_call", "after_artifact_store", "before_publish"]
)
async def test_cancelled_speech_cannot_publish_and_keeps_reservation(
    harness, tmp_path, ending, point
):
    class StopSpeech:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == point:
                if ending == "cancel":
                    await harness.queue.request_cancel(job.id)
                else:
                    await harness.queue.delete(job.id)

    calls = []

    def replay(request):
        calls.append(request)
        return success(request)

    config = speech_settings(harness, tmp_path, asr_requests_per_day=1)
    app = create_app(config)
    worker = speech_worker(harness, config, replay, faults=StopSpeech())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        async with asyncio.timeout(10):
            while True:
                body = (await http.get(f"/v1/investigations/{identifier}", headers=headers)).json()
                if body["state"] == "cancelled":
                    break
                await asyncio.sleep(0.02)
        await worker.stop()
        assert not body["speech"]["segments"]
        assert body["coverage"]["total_ms"] == 1000
        assert len(calls) == (0 if point == "before_provider_call" else 1)
        restarted = speech_worker(harness, config, replay)
        await restarted.start()
        # The cancelled primary reservation is kept, so only the fallback has room.
        new = await wait_for_speech(http, *(await submit(http, audio_bytes())))
        assert new["speech"]["model"] == "synthetic-fallback"
        blocked = await submit(http, audio_bytes())
        assert speech_error(await wait_for_speech(http, *blocked)) == "ASR_QUOTA_EXHAUSTED"
        await restarted.stop()


@pytest.mark.parametrize(
    "status", [200, 401, 404, 408, 413, 429, 500, 502, 503, 504, "timeout", "offline"]
)
@pytest.mark.parametrize("crash", [False, True])
async def test_terminal_provider_outcomes_are_safe_and_charge_conservatively(
    harness, tmp_path, status, crash
):
    class CrashAfterOutcome:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == Checkpoint.AFTER_ARTIFACT_STORE:
                raise SimulatedCrash

    calls = []

    def replay(request):
        calls.append(request)
        if status == "timeout":
            raise httpx.ReadTimeout("private detail", request=request)
        if status == "offline":
            raise httpx.ConnectError("private detail", request=request)
        return httpx.Response(status, json={"private": "not exposed"})

    config = speech_settings(harness, tmp_path, asr_requests_per_day=1)
    ambiguous = status in {408, 500, 502, 504, "timeout"}
    policy = RetryPolicy(
        transient_attempts=2 if ambiguous else 0,
        rate_limited_attempts=0,
        backoff_seconds=0.01,
        max_backoff_seconds=0.02,
    )
    app = create_app(config)
    worker = speech_worker(
        harness,
        config,
        replay,
        retry_policy=policy,
        faults=CrashAfterOutcome() if crash else None,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await worker.start()
        first = await submit(http, audio_bytes())
        if crash:
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(worker.wait(), 10)
            worker = speech_worker(harness, config, replay, retry_policy=policy)
            await worker.start()
        failed = await wait_for_speech(http, *first)
        assert failed["speech"]["status"] == "unavailable"
        assert speech_error(failed) == (
            "ASR_QUOTA_EXHAUSTED" if status == 429 else "ASR_UNAVAILABLE"
        )
        assert failed["speech"]["reason"] == {
            200: "invalid_response",
            404: "model_unavailable",
            413: "chunk_exceeds_file_cap",
            429: "quota_exhausted",
            "offline": "offline",
        }.get(status, "unknown_outcome" if ambiguous else "provider_unavailable")
        assert "private" not in str(failed)
        new = await submit(http, audio_bytes())
        assert speech_error(await wait_for_speech(http, *new)) == "ASR_QUOTA_EXHAUSTED"
        await worker.stop()
        # Both models were tried once each and both reservations are kept.
        assert [model_of(call) for call in calls] == ["synthetic-model", "synthetic-fallback"]


async def test_video_without_audio_skips_groq_even_when_enabled(harness, tmp_path):
    video = tmp_path / "silent.mkv"
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
            "ffv1",
            str(video),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0
    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, lambda _: pytest.fail("No-audio source reached Groq"))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await worker.start()
        submitted = await submit(http, video.read_bytes())
        body = await wait_for_speech(http, *submitted)
        await worker.stop()
        assert body["stage"] == "media_validation"
        assert body["speech"]["reason"] == "no_audio_track"
        assert body["coverage"]["media"]["text_status"] == "pending"


@pytest.mark.parametrize("ending", ["finish", "cancel", "stale"])
async def test_provider_wait_keeps_heartbeats_and_stops_on_cancel_or_lease_loss(
    harness, tmp_path, ending
):
    entered, finish, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Observe:
        job = None

        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr":
                self.job = job

    observed = Observe()
    calls = []

    async def replay(request):
        calls.append(request)
        entered.set()
        try:
            await finish.wait()
            return success(request)
        finally:
            stopped.set()

    config = speech_settings(harness, tmp_path)
    app = create_app(config)
    worker = speech_worker(harness, config, replay, faults=observed)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await worker.start()
        await asyncio.wait_for(entered.wait(), 10)
        await asyncio.sleep(2.1)
        running = (await http.get(f"/v1/investigations/{identifier}", headers=headers)).json()
        assert running["state"] == "running"
        assert running["speech"]["status"] == "running"
        assert running["coverage"]["total_ms"] == 1000
        if ending == "finish":
            finish.set()
        elif ending == "cancel":
            await harness.queue.request_cancel(observed.job.id)
        else:
            await harness.queue.release(observed.job.lease)
        await asyncio.wait_for(stopped.wait(), 5)
        if ending == "stale":
            finish.set()
        body = await wait_for_speech(http, identifier, headers)
        await worker.stop()
        if ending == "cancel":
            assert body["speech"]["status"] == "unavailable"
            assert body["state"] == "cancelled"
            assert len(calls) == 1
        elif ending == "stale":
            # The interrupted primary is never resent; the fallback answers once.
            assert body["speech"]["status"] == "completed"
            assert body["speech"]["model"] == "synthetic-fallback"
            assert [model_of(call) for call in calls] == ["synthetic-model", "synthetic-fallback"]
        else:
            assert body["speech"]["status"] == "completed"
            assert len(calls) == 1


@pytest.mark.parametrize(
    "field",
    [
        "groq_api_key",
        "groq_account_id",
        "asr_limits_verified_on",
        "asr_requests_per_minute",
        "asr_requests_per_day",
        "asr_audio_seconds_per_hour",
        "asr_audio_seconds_per_day",
        "asr_minimum_billable_seconds",
        "asr_audio_max_bytes",
    ],
)
async def test_api_cannot_activate_hosted_speech_with_incomplete_settings(harness, tmp_path, field):
    config = speech_settings(harness, tmp_path)
    values = config.model_dump()
    del values[field]
    with pytest.raises(ValueError, match="Hosted speech requires"):
        create_app(type(config)(**values, _env_file=None))


@pytest.mark.parametrize("change", ["missing", "changed", "outside", "preparation-deleted"])
async def test_unusable_prepared_source_never_reaches_provider(harness, tmp_path, change):
    config = speech_settings(harness, tmp_path)

    class ChangeArtifact:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == Checkpoint.CLAIMED:
                if change == "preparation-deleted":
                    await harness.queue.delete(uuid.UUID(job.payload["media_job_id"]))
                    return
                [audio] = config.artifacts_dir.rglob("*.wav")
                audio.unlink()
                if change == "changed":
                    audio.write_bytes(b"changed")
                elif change == "outside":
                    other = tmp_path / "outside.wav"
                    other.write_bytes(b"outside")
                    audio.symlink_to(other)

    app = create_app(config)
    worker = speech_worker(
        harness,
        config,
        lambda _: pytest.fail("Unusable artifact reached Groq"),
        faults=ChangeArtifact(),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await worker.start()
        submitted = await submit(http, audio_bytes())
        body = await wait_for_speech(http, *submitted)
        await worker.stop()
        assert speech_error(body) == "ASR_UNAVAILABLE"
        # Missing or changed audio is a gap before any reservation, never silence.
        assert body["speech"]["reason"] == (
            "provider_unavailable" if change == "preparation-deleted" else "missing_audio"
        )
        async with app.state.database.engine.connect() as connection:
            reserved = await connection.scalar(
                select(func.count())
                .select_from(asr_requests)
                .where(asr_requests.c.account_id == config.groq_account_id)
            )
        assert reserved == 0


@pytest.mark.parametrize(
    "change",
    [
        {"groq_model": "different-model"},
        {"groq_fallback_model": "different-fallback"},
        {"asr_enabled": False},
    ],
)
async def test_queued_speech_cannot_silently_change_model_or_enablement(harness, tmp_path, change):
    config = speech_settings(harness, tmp_path)

    class CrashAtSpeech:
        async def checkpoint(self, name, job):
            if job.key.stage == "upload_asr" and name == Checkpoint.CLAIMED:
                raise SimulatedCrash

    app = create_app(config)
    worker = speech_worker(harness, config, success, faults=CrashAtSpeech())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        submitted = await submit(http, audio_bytes())
        await worker.start()
        with pytest.raises(SimulatedCrash):
            await asyncio.wait_for(worker.wait(), 10)
        restarted = speech_worker(
            harness,
            config.model_copy(update=change),
            lambda _: pytest.fail("Changed settings must not contact Groq"),
        )
        await restarted.start()
        body = await wait_for_speech(http, *submitted)
        await restarted.stop()
        assert speech_error(body) == "ASR_UNAVAILABLE"


async def test_delayed_audio_track_keeps_original_video_timebase(harness, tmp_path):
    video = tmp_path / "delayed-audio.mkv"
    generated = await run_command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=16x16:rate=10:duration=2",
            "-itsoffset",
            "0.5",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=16000:duration=1",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(video),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0
    config = speech_settings(harness, tmp_path)
    app = create_app(config)

    def replay(request):
        content = request.content
        with wave.open(io.BytesIO(content[content.index(b"RIFF") :]), "rb") as audio:
            assert audio.getnframes() == 24000
            assert audio.readframes(8000) == bytes(16000)
            assert any(audio.readframes(16000))
        return httpx.Response(
            200,
            json={
                "text": "A synthetic chime.",
                "segments": [{"start": 0.625, "end": 1.375, "text": "A synthetic chime."}],
            },
        )

    worker = speech_worker(harness, config, replay)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        await worker.start()
        submitted = await submit(http, video.read_bytes())
        body = await wait_for_speech(http, *submitted)
        await worker.stop()
        assert body["speech"]["segments"] == [
            {
                "text": "A synthetic chime.",
                "interval": {
                    "start_ms": 625,
                    "end_ms": 1375,
                    "timebase": "media",
                },
            }
        ]
