"""Synthetic upload-text handoff through authenticated HTTP, PostgreSQL and media worker."""

import asyncio
import copy
import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text, update
from test_account_link import LINK, FakeVerifier, link_body

from services.api.main import create_app
from services.jobs.models import jobs
from services.jobs.queue import JobQueue
from services.media.runner import CommandLimits, run_command
from services.pipeline.intake import intake_stage_key
from services.pipeline.speech import speech_stage_key

from .test_media_validation import audio_bytes, media_settings, submit, wait_for_media
from .test_privacy import expire, sweep

SYNTHETIC_TEXT = (
    Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/device-text/synthetic.json"
)


@pytest.fixture
async def text_upload(harness, tmp_path):
    video = tmp_path / "synthetic.mkv"
    generated = await run_command(
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
            str(video),
        ],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0
    config = media_settings(
        harness,
        embed_worker=True,
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
    )
    app = create_app(config, id_token_verifier=FakeVerifier())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, video.read_bytes())
        media = await wait_for_media(http, identifier, headers)
        assert media["coverage"]["media"]["has_audio"] is False
        await app.state.worker.stop()
        source = {
            "protocol_version": 1,
            "upload_id": media["source"]["upload_id"],
            "source_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "timebase": "media",
            "rotation_degrees": 0,
            "box_space": "normalized_10000",
            "recognizer": {"name": "mlkit-text-recognition-latin-bundled", "version": "16.0.1"},
            "sampling": None,
        }
        yield app, http, headers, f"/v1/investigations/{identifier}/device-text", source


def frame(pts=250, status="recognized", text=True):
    return {
        "frame_pts": pts,
        "status": status,
        "regions": 1,
        "recognition_ms": 12,
        "failed_regions": 1 if status == "failed" else 0,
        "text_observations": [
            {
                "id": str(uuid.uuid4()),
                "text": "Invented screen text",
                "box": [100, 200, 9000, 8000],
                "frame_pts": pts,
            }
        ]
        if text
        else [],
    }


async def test_owned_text_is_persisted_with_original_frame_and_box(text_upload):
    _, http, headers, path, source = text_upload
    pending = await http.get(path, headers=headers)
    assert pending.status_code == 200
    assert pending.json()["status"] == "not_started"
    body = {**source, "frames": [frame()]}
    received = await http.put(path + "/batches/0", headers=headers, json=body)
    assert received.status_code == 200, received.text
    result = (await http.get(path, headers=headers)).json()
    assert result["status"] == "receiving"
    assert result["completion"] is None
    assert result["batches"] == [{"batch_id": 0, "body": body}]
    assert result["job_id"] is not None


async def test_replays_and_out_of_order_batches_have_stable_identity(text_upload):
    _, http, headers, path, source = text_upload
    body = {**source, "frames": [frame()]}
    replies = await asyncio.gather(
        *(http.put(path + "/batches/1", headers=headers, json=body) for _ in range(3))
    )
    assert all(response.status_code == 200 for response in replies)
    assert all(len(response.json()["batches"]) == 1 for response in replies)
    changed = copy.deepcopy(body)
    changed["frames"][0]["text_observations"][0]["text"] = "Different wording"
    assert (await http.put(path + "/batches/1", headers=headers, json=changed)).status_code == 409
    assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 409
    earlier = {**source, "frames": [frame(100, text=False)]}
    assert (await http.put(path + "/batches/0", headers=headers, json=earlier)).status_code == 200
    result = (await http.get(path, headers=headers)).json()
    assert [batch["batch_id"] for batch in result["batches"]] == [0, 1]


@pytest.mark.parametrize("empty", [False, True])
async def test_completion_is_explicit_replayable_and_not_continuous_coverage(text_upload, empty):
    _, http, headers, path, source = text_upload
    frames = [frame(text=False), frame(500, "failed", False)]
    no_regions = {**frame(750, "no_text_regions", False), "regions": 0}
    body = {**source, "frames": frames + [no_regions]}
    completion = {
        **source,
        "batch_count": 0 if empty else 1,
        "dropped_frames": 2,
        "capped_frames": 3,
        "unfinished_frames": 1,
    }
    if not empty:
        assert (
            await http.post(path + "/complete", headers=headers, json=completion)
        ).status_code == 409
        assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 200
    accepted = await http.post(path + "/complete", headers=headers, json=completion)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    assert result["status"] == "completed"
    assert result["completion"] == completion
    assert result["batches"] == ([] if empty else [{"batch_id": 0, "body": body}])
    replay = await http.post(path + "/complete", headers=headers, json=completion)
    assert replay.json() == result
    assert (
        await http.post(
            path + "/complete", headers=headers, json={**completion, "dropped_frames": 99}
        )
    ).status_code == 409
    assert (await http.put(path + "/batches/1", headers=headers, json=body)).status_code == 409
    if not empty:
        assert (await http.put(path + "/batches/0", headers=headers, json=body)).json() == result
    investigation = (await http.get(path.removesuffix("/device-text"), headers=headers)).json()
    assert investigation["coverage"]["status"] == "not_started"
    assert investigation["speech"]["reason"] == "no_audio_track"


@pytest.mark.parametrize("ending", ["cancel", "delete", "delete-media"])
async def test_late_text_cannot_revive_cancelled_or_deleted_work(text_upload, ending):
    _, http, headers, path, source = text_upload
    body = {**source, "frames": [frame()]}
    received = (await http.put(path + "/batches/0", headers=headers, json=body)).json()
    job_id = received["job_id"]
    if ending == "delete-media":
        investigation = (await http.get(path.removesuffix("/device-text"), headers=headers)).json()
        job_id = investigation["job"]["id"]
    if ending == "cancel":
        response = await http.post(f"/v1/jobs/{job_id}/cancel", headers=headers)
    else:
        response = await http.delete(f"/v1/jobs/{job_id}", headers=headers)
    assert response.status_code == 200
    assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 409
    completion = {
        **source,
        "batch_count": 1,
        "dropped_frames": 0,
        "capped_frames": 0,
        "unfinished_frames": 0,
    }
    assert (
        await http.post(path + "/complete", headers=headers, json=completion)
    ).status_code == 409
    read = await http.get(path, headers=headers)
    if ending == "cancel":
        assert read.json()["status"] == "cancelled"
        assert read.json()["batches"] == received["batches"]
    else:
        assert read.status_code == 409


@pytest.mark.parametrize("stage", ["intake", "upload_asr"])
@pytest.mark.parametrize("submitted", [False, True])
async def test_prerequisite_purge_cannot_reopen_account_text(text_upload, stage, submitted):
    app, http, headers, path, source = text_upload
    linked = await http.post(LINK, headers=headers, json=link_body("token-alice"))
    assert linked.status_code == 200
    owner = uuid.UUID(linked.json()["principal_id"])
    if stage == "upload_asr":
        identifier = uuid.UUID(path.split("/")[3])
        async with app.state.database.engine.begin() as connection:
            await JobQueue(app.state.database).enqueue(
                connection,
                speech_stage_key(identifier),
                {"investigation_id": str(identifier)},
                owner_id=owner,
            )
    body = {**source, "frames": [frame()]}
    if submitted:
        assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 200
    async with app.state.database.engine.connect() as connection:
        job_id = await connection.scalar(
            select(jobs.c.id).where(jobs.c.owner_id == owner, jobs.c.stage == stage)
        )
    assert job_id is not None
    assert (await http.delete(f"/v1/jobs/{job_id}", headers=headers)).status_code == 200
    assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 409
    await expire(app, owner)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.id == job_id)
            .values(updated_at=func.now() - timedelta(days=8))
        )
    assert (await sweep(app))["purged"] >= 1
    assert await JobQueue(app.state.database).get(job_id) is None
    assert (await http.get(path.removesuffix("/device-text"), headers=headers)).status_code == 200
    assert (await http.get(path, headers=headers)).status_code == 409
    assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 409
    assert (
        await http.post(
            path + "/complete",
            headers=headers,
            json={
                **source,
                "batch_count": int(submitted),
                "dropped_frames": 0,
                "capped_frames": 0,
                "unfinished_frames": 0,
            },
        )
    ).status_code == 409


async def test_prerequisite_deletion_fences_an_already_waiting_submission(text_upload, harness):
    app, http, headers, path, source = text_upload
    key = intake_stage_key(uuid.UUID(path.split("/")[3]))
    pending = None
    try:
        async with app.state.database.engine.begin() as deletion:
            job_id = await deletion.scalar(
                select(jobs.c.id)
                .where(
                    jobs.c.stage == key.stage,
                    jobs.c.version == key.version,
                    jobs.c.input_hash == key.input_hash,
                )
                .with_for_update()
            )
            deleting_pid = await deletion.scalar(select(func.pg_backend_pid()))
            pending = asyncio.create_task(
                http.put(path + "/batches/0", headers=headers, json={**source, "frames": [frame()]})
            )
            async with asyncio.timeout(5), harness.control.engine.connect() as observer:
                while not await observer.scalar(  # noqa: ASYNC110 - external PostgreSQL lock
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE NOT granted AND :pid = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"pid": deleting_pid},
                ):
                    await asyncio.sleep(0.01)
            assert await JobQueue(app.state.database).delete(job_id, connection=deletion)
        assert (await pending).status_code == 409
        assert (await http.get(path, headers=headers)).status_code == 409
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)


async def test_foreign_missing_and_unauthenticated_reads_and_writes_are_inaccessible(text_upload):
    _, http, headers, path, source = text_upload
    guest = (await http.post("/v1/principals/guest", json={})).json()
    foreign = {"Authorization": "Bearer " + guest["credential"]["token"]}
    body = {**source, "frames": [frame()]}
    for route, auth, code in (
        (path, {}, 401),
        (path, foreign, 404),
        (f"/v1/investigations/{uuid.uuid4()}/device-text", headers, 404),
    ):
        assert (await http.get(route, headers=auth)).status_code == code
        assert (await http.put(route + "/batches/0", headers=auth, json=body)).status_code == code


@pytest.mark.parametrize(
    "change",
    [
        "negative",
        "outside",
        "float",
        "string",
        "mismatched-pts",
        "inverted",
        "out-of-box",
        "short-box",
        "empty-text",
        "blank-text",
        "failed-with-text",
        "empty-heuristic",
        "all-regions-failed",
        "too-many-failures",
        "unknown-status",
        "rotation",
        "capture",
        "version",
        "boolean-version",
        "extra",
        "long-text",
        "duplicate-frame",
        "duplicate-id",
    ],
)
async def test_malformed_frames_fail_without_partial_acceptance(text_upload, change):
    _, http, headers, path, source = text_upload
    body = {**source, "frames": [frame()]}
    sample = body["frames"][0]
    observation = sample["text_observations"][0]
    expected = 422
    if change in {"negative", "outside", "float", "string"}:
        sample["frame_pts"] = {"negative": -1, "outside": 1000, "float": 1.5, "string": "250"}[
            change
        ]
        observation["frame_pts"] = sample["frame_pts"]
    elif change == "mismatched-pts":
        observation["frame_pts"] = 251
    elif change in {"inverted", "out-of-box", "short-box"}:
        observation["box"] = {
            "inverted": [8, 5, 4, 9],
            "out-of-box": [0, 0, 10001, 8],
            "short-box": [0, 0, 8],
        }[change]
    elif change in {"empty-text", "blank-text", "long-text"}:
        observation["text"] = {"empty-text": "", "blank-text": " ", "long-text": "x" * 4097}[change]
    elif change == "failed-with-text":
        sample.update(status="failed", failed_regions=1)
    elif change == "empty-heuristic":
        sample["status"] = "no_text_regions"
    elif change == "all-regions-failed":
        sample["failed_regions"] = 1
    elif change == "too-many-failures":
        sample["failed_regions"] = 2
    elif change == "unknown-status":
        sample["status"] = "success"
    elif change == "rotation":
        body["rotation_degrees"] = 90
    elif change == "capture":
        body["timebase"] = "capture"
    elif change == "version":
        body["protocol_version"] = 2
    elif change == "boolean-version":
        body["protocol_version"] = True
    elif change == "extra":
        body["caption"] = "Not device text"
    elif change == "duplicate-frame":
        body["frames"].append(copy.deepcopy(sample))
        expected = 409
    elif change == "duplicate-id":
        sample["text_observations"].append(copy.deepcopy(observation))
        expected = 409
    response = await http.put(path + "/batches/0", headers=headers, json=body)
    assert response.status_code == expected, response.text
    assert (await http.get(path, headers=headers)).json()["status"] == "not_started"


@pytest.mark.parametrize("case", ["oversized", "bad-json", "duplicate-key", "content-type"])
async def test_bounded_streaming_json_fails_safely(text_upload, case):
    _, http, headers, path, source = text_upload
    body = json.dumps({**source, "frames": []})
    content_type = "application/json"
    if case == "oversized":

        async def chunks():
            for _ in range(5):
                yield b" " * 65536

        content = chunks()
        code = 413
    elif case == "bad-json":
        content, code = b"{", 422
    elif case == "duplicate-key":
        content = body.replace(
            '"protocol_version": 1', '"protocol_version": 2, "protocol_version": 1'
        )
        code = 422
    else:
        content, code, content_type = body, 415, "text/plain"
    response = await http.put(
        path + "/batches/0", headers={**headers, "Content-Type": content_type}, content=content
    )
    assert response.status_code == code, response.text
    assert set(response.json()) == {"code", "message", "retryable", "action", "request_id"}
    assert (await http.get(path, headers=headers)).json()["status"] == "not_started"


@pytest.mark.parametrize("budget", ["bytes", "observations"])
async def test_per_source_budget_is_atomic_and_does_not_block_exact_replay(text_upload, budget):
    _, http, headers, path, source = text_upload
    accepted = None
    for batch in range(15):
        frames = []
        for index in range(100 if budget == "bytes" else 10):
            sample = frame(batch * 100 + index)
            if budget == "bytes":
                sample["text_observations"][0]["text"] = "x" * 2000
            else:
                sample["text_observations"] = [
                    {**sample["text_observations"][0], "id": str(uuid.uuid4())} for _ in range(100)
                ]
            frames.append(sample)
        body = {**source, "frames": frames}
        response = await http.put(path + f"/batches/{batch}", headers=headers, json=body)
        if response.status_code == 413:
            break
        assert response.status_code == 200, response.text
        accepted = (batch, body, response.json())
    else:
        pytest.fail("The bounded source budget was not enforced")
    number, body, expected = accepted
    replay = await http.put(path + f"/batches/{number}", headers=headers, json=body)
    assert replay.json() == expected


async def test_wrong_source_and_unvalidated_media_are_not_admitted(text_upload):
    _, http, headers, path, source = text_upload
    for field, value in (("upload_id", str(uuid.uuid4())), ("source_sha256", "a" * 64)):
        body = {**source, field: value, "frames": []}
        assert (await http.put(path + "/batches/0", headers=headers, json=body)).status_code == 409
    for requested_source, code in (
        ({"kind": "upload", "upload_id": source["upload_id"]}, "DEVICE_TEXT_NOT_READY"),
        ({"kind": "url", "url": "https://example.org/synthetic"}, "DEVICE_TEXT_SOURCE_INVALID"),
    ):
        created = await http.post(
            "/v1/investigations",
            headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
            json={"source": requested_source},
        )
        assert created.status_code == 202
        read = await http.get(
            f"/v1/investigations/{created.json()['id']}/device-text", headers=headers
        )
        assert read.status_code == 409 and read.json()["code"] == code


async def test_sampling_and_recognizer_provenance_are_consistent_and_durable(text_upload):
    app, http, headers, path, source = text_upload
    source["sampling"] = {
        "policy": "change_triggered",
        "probe_interval_ms": 1000,
        "thumbnail_edge": 64,
        "change_threshold": 12,
        "heartbeat_ms": 5000,
        "max_frames_per_minute": 20,
        "frame_long_edge": 720,
    }
    body = {**source, "frames": [frame()]}
    accepted = await http.put(path + "/batches/0", headers=headers, json=body)
    assert accepted.status_code == 200
    changed = {**source, "recognizer": {"name": "different", "version": "2"}, "frames": []}
    assert (await http.put(path + "/batches/1", headers=headers, json=changed)).status_code == 409
    # A new API/database pool reads the persisted handoff without a worker or device.
    restarted = create_app(app.state.settings.model_copy(update={"embed_worker": False}))
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test"
        ) as other,
    ):
        assert (await other.get(path, headers=headers)).json() == accepted.json()


async def test_shared_synthetic_handoff_roundtrips_through_public_api(text_upload):
    from test_contract_roundtrip import validate

    _, http, headers, path, source = text_upload
    body = json.loads(await asyncio.to_thread(SYNTHETIC_TEXT.read_text))["batch"]
    body.update(upload_id=source["upload_id"], source_sha256=source["source_sha256"])
    response = await http.put(path + "/batches/0", headers=headers, json=body)
    assert response.status_code == 200
    validate.Validator().validate(response.json(), "device-text.schema.json")
    assert response.json()["batches"][0]["body"] == body


async def test_audio_only_source_cannot_admit_or_complete_device_text(harness, tmp_path):
    app = create_app(
        media_settings(
            harness,
            embed_worker=True,
            storage_dir=tmp_path / "uploads",
            artifacts_dir=tmp_path / "artifacts",
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        content = audio_bytes()
        identifier, headers = await submit(http, content)
        media = await wait_for_media(http, identifier, headers)
        assert media["coverage"]["media"]["has_video"] is False
        path = f"/v1/investigations/{identifier}/device-text"
        source = {
            "protocol_version": 1,
            "upload_id": media["source"]["upload_id"],
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "timebase": "media",
            "rotation_degrees": 0,
            "box_space": "normalized_10000",
        }
        assert (await http.get(path, headers=headers)).status_code == 409
        assert (
            await http.put(path + "/batches/0", headers=headers, json={**source, "frames": []})
        ).status_code == 409
        assert (
            await http.post(
                path + "/complete",
                headers=headers,
                json={
                    **source,
                    "batch_count": 0,
                    "dropped_frames": 0,
                    "capped_frames": 0,
                    "unfinished_frames": 0,
                },
            )
        ).status_code == 409
