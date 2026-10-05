import asyncio
import hashlib
import json
import uuid
from dataclasses import replace
from datetime import timedelta
from tempfile import SpooledTemporaryFile

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from starlette import formparsers
from starlette.requests import Request
from test_intake_api import CONTRACT_VALIDATOR, assert_error, guest

from services.api.main import create_app
from services.api.routes import captures as capture_routes
from services.captures import CAPTURE_STAGE, CaptureProcessor
from services.jobs.handlers import JobContext, default_handlers
from services.jobs.models import job_results, jobs
from services.jobs.queue import JobQueue, PublishRejected
from services.jobs.retries import NonRetriableInput
from services.models import capture_chunks, capture_sessions, investigations
from services.settings import Settings
from services.worker.runtime import Worker

from .harness import ScriptedFaults
from .test_privacy import expire, sweep

DATA = b"synthetic capture bytes"


@pytest.fixture(params=[1024 * 1024, 1], ids=["memory", "disk"])
def spools(monkeypatch, request):
    opened = []

    def track(*args, **kwargs):
        spool = SpooledTemporaryFile(*args, **kwargs)
        opened.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", track)
    monkeypatch.setattr(formparsers.MultiPartParser, "spool_max_size", request.param)
    return opened


@pytest.fixture
async def capture_app(database_url, tmp_path):
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "captures",
            upload_max_bytes=1024,
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(capture_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=capture_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def create(client, headers, duration=10_000, key=None):
    response = await client.post(
        "/v1/captures",
        json={"chunk_duration_ms": duration},
        headers={**headers, "Idempotency-Key": key or uuid.uuid4().hex},
    )
    assert response.status_code == 201, response.text
    CONTRACT_VALIDATOR.validate(response.json(), "capture-session.schema.json")
    return response.json()


def parts(session, seq=0, data=DATA, modality="both", end=None):
    start = seq * session["chunk_duration_ms"]
    metadata = {
        "chunk": {
            "session_id": session["id"],
            "seq": seq,
            "interval": {
                "start_ms": start,
                "end_ms": end if end is not None else start + session["chunk_duration_ms"],
                "timebase": "capture",
            },
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content_type": "video/mp4",
        },
        "modality": modality,
    }
    return metadata, data


async def put(client, headers, session, seq=0, *, metadata=None, data=DATA, **kwargs):
    if metadata is None:
        metadata, _ = parts(session, seq, data, **kwargs)
    return await client.put(
        f"/v1/captures/{session['id']}/chunks/{seq}",
        files={
            "metadata": (None, json.dumps(metadata), "application/json"),
            "content": ("chunk.bin", data, "application/octet-stream"),
        },
        headers=headers,
    )


async def status(client, headers, session):
    response = await client.get(f"/v1/captures/{session['id']}", headers=headers)
    assert response.status_code == 200, response.text
    CONTRACT_VALIDATOR.validate(response.json(), "capture-status.schema.json")
    return response.json()


async def close(client, headers, session, choice=True, duration=None):
    body = {"continue_research": choice}
    if duration is not None:
        body["duration_ms"] = duration
    return await client.post(f"/v1/captures/{session['id']}/close", json=body, headers=headers)


async def stored_job(app, session, seq=0):
    async with app.state.database.engine.connect() as connection:
        row = (
            await connection.execute(
                select(jobs)
                .join(capture_chunks, jobs.c.id == capture_chunks.c.job_id)
                .where(
                    capture_chunks.c.session_id == uuid.UUID(session["id"]),
                    capture_chunks.c.seq == seq,
                )
            )
        ).one()
    return row


async def test_create_replays_original_after_close_and_source_is_shared(client, capture_app):
    headers = await guest(client)
    key = uuid.uuid4().hex
    session = await create(client, headers, key=key)
    assert await create(client, headers, key=key) == session
    assert (await put(client, headers, session)).status_code == 200
    assert (await close(client, headers, session)).status_code == 200
    assert await create(client, headers, key=key) == session
    conflict = await client.post(
        "/v1/captures",
        json={"chunk_duration_ms": 5000},
        headers={**headers, "Idempotency-Key": key},
    )
    assert_error(conflict, 409, "IDEMPOTENCY_KEY_REUSED")
    other = await guest(client)
    assert (await create(client, other, key=key))["id"] != session["id"]
    investigation = await client.get(f"/v1/investigations/{session['id']}", headers=headers)
    assert investigation.json()["source"] == {
        "kind": "capture",
        "capture_id": session["id"],
        "duration_ms": 10000,
    }
    listing = await client.get("/v1/investigations", headers=headers)
    assert any(item["id"] == session["id"] for item in listing.json()["items"])
    async with capture_app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(capture_sessions)
                .where(capture_sessions.c.id == uuid.UUID(session["id"]))
            )
            == 1
        )


async def test_gaps_duplicate_current_manifest_and_short_final(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    out = await put(client, headers, session, 2, modality="text")
    assert out.status_code == 200, out.text
    CONTRACT_VALIDATOR.validate(out.json(), "capture-chunk.schema.json")
    assert out.json()["disposition"] == "out_of_order"
    assert out.json()["gaps"] == [{"from_seq": 0, "to_seq": 1}]
    await put(client, headers, session, 0, modality="speech")
    mid = await status(client, headers, session)
    assert mid["session"]["received_ms"] == 20000
    assert mid["session"]["gaps"] == [{"from_seq": 1, "to_seq": 1}]
    assert mid["manifest"]["missing_intervals"] == [
        {"start_ms": 10000, "end_ms": 20000, "timebase": "capture"}
    ]
    await put(client, headers, session, 1)
    duplicate = await put(client, headers, session, 2, modality="text")
    assert duplicate.json()["disposition"] == "duplicate"
    assert duplicate.json()["received_at"] == out.json()["received_at"]
    assert duplicate.json()["gaps"] == []
    final = await put(client, headers, session, 3, end=35000)
    assert final.status_code == 200, final.text
    assert_error(await put(client, headers, session, 4), 409, "CAPTURE_FINAL_CHUNK")
    assert_error(await close(client, headers, session, duration=36000), 409, "CAPTURE_FINAL_CHUNK")
    assert_error(await close(client, headers, session, duration=34000), 422, "VALIDATION_FAILED")
    closed = await close(client, headers, session, duration=35000)
    assert closed.status_code == 200, closed.text
    assert closed.json() == (await close(client, headers, session, duration=35000)).json()
    assert_error(await close(client, headers, session, False), 409, "CAPTURE_CLOSE_CONFLICT")
    assert_error(await put(client, headers, session, 4), 409, "CAPTURE_CLOSED")
    duplicate = await put(client, headers, session, 3, end=35000)
    assert duplicate.json()["disposition"] == "duplicate"
    progress = await status(client, headers, session)
    assert progress["session"]["received_ms"] == 35000
    assert len(progress["manifest"]["declared_coverage"]["speech"]) == 3
    assert len(progress["manifest"]["declared_coverage"]["text"]) == 3
    assert len(progress["work"]) == 4
    assert progress["claims"] == [] and progress["claim_extraction_status"] == "not_started"
    assert all(item["processing_status"] == "waiting" for item in progress["work"])


@pytest.mark.parametrize("duration", [1000, 10000, 30000, 7000])
async def test_exact_three_minute_cap_and_trailing_gaps(client, duration):
    headers = await guest(client)
    session = await create(client, headers, duration)
    seq = (180000 - 1) // duration
    response = await put(client, headers, session, seq, end=180000)
    assert response.status_code == 200, response.text
    assert_error(await put(client, headers, session, seq + 1), 422, "VALIDATION_FAILED")
    assert (await close(client, headers, session, duration=180000)).status_code == 200
    assert (await status(client, headers, session))["manifest"]["duration_ms"] == 180000
    another = await create(client, headers)
    await put(client, headers, another)
    assert (await close(client, headers, another, duration=30000)).status_code == 200
    manifest = (await status(client, headers, another))["manifest"]
    assert manifest["missing_intervals"] == [
        {"start_ms": 10000, "end_ms": 30000, "timebase": "capture"}
    ]


@pytest.mark.parametrize("choice", [False, True])
async def test_close_choice_cancels_only_unfinished_jobs(client, capture_app, choice):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    job = await stored_job(capture_app, session)
    assert (await close(client, headers, session, choice)).status_code == 200
    stored = await stored_job(capture_app, session)
    assert stored.state == ("queued" if choice else "cancelled")
    assert stored.id == job.id
    assert (await status(client, headers, session))["continue_research"] is choice
    empty = await create(client, headers)
    assert (await close(client, headers, empty, choice)).json()["received_ms"] == 0


async def test_concurrent_creates_and_chunk_retries_enqueue_once(client, capture_app):
    headers = await guest(client)
    key = uuid.uuid4().hex
    a, b = await asyncio.gather(create(client, headers, key=key), create(client, headers, key=key))
    assert a == b
    replies = await asyncio.gather(*(put(client, headers, a) for _ in range(4)))
    assert all(r.status_code == 200 for r in replies), [r.text for r in replies]
    assert sorted(r.json()["disposition"] for r in replies) == [
        "duplicate",
        "duplicate",
        "duplicate",
        "stored",
    ]
    await stored_job(capture_app, a)
    assert len(list(capture_app.state.settings.storage_dir.iterdir())) == 1


async def test_ownership_auth_and_spoofed_identity(client):
    owner, other = await guest(client), await guest(client)
    session = await create(client, owner)
    missing = {**session, "id": str(uuid.uuid4())}
    for target in [session, missing]:
        for response in [
            await client.get(f"/v1/captures/{target['id']}", headers=other),
            await put(client, other, target),
            await close(client, other, target),
        ]:
            assert_error(response, 404, "NOT_FOUND")
    assert_error(await client.get(f"/v1/captures/{session['id']}"), 401, "AUTHENTICATION_REQUIRED")
    assert_error(
        await client.get(
            f"/v1/captures/{session['id']}", headers=owner, params={"owner_id": "fake"}
        ),
        400,
        "CLIENT_IDENTITY_REJECTED",
    )
    assert_error(
        await client.post("/v1/captures", headers=owner, json={}), 400, "IDEMPOTENCY_KEY_REQUIRED"
    )
    assert_error(
        await client.post(
            "/v1/captures", headers={**owner, "Idempotency-Key": "x"}, json={"owner_id": "fake"}
        ),
        422,
        "CLIENT_IDENTITY_REJECTED",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", -1),
        ("seq", True),
        ("seq", "0"),
        ("session_id", str(uuid.uuid4())),
        ("size_bytes", 0),
        ("size_bytes", "22"),
        ("sha256", "a" * 63),
        ("content_type", "x"),
        ("extra", 0),
    ],
)
async def test_invalid_chunk_metadata(client, field, value):
    headers = await guest(client)
    session = await create(client, headers)
    metadata, _ = parts(session)
    metadata["chunk"][field] = value
    assert_error(await put(client, headers, session, metadata=metadata), 422, "VALIDATION_FAILED")


@pytest.mark.parametrize(
    "changes",
    [
        {"timebase": "media"},
        {"start_ms": 1},
        {"start_ms": -1},
        {"end_ms": 0},
        {"start_ms": 10000, "end_ms": 10000},
        {"end_ms": 10001},
        {"end_ms": 180001},
    ],
)
async def test_invalid_intervals(client, changes):
    headers = await guest(client)
    session = await create(client, headers)
    metadata, _ = parts(session)
    metadata["chunk"]["interval"].update(changes)
    assert_error(await put(client, headers, session, metadata=metadata), 422, "VALIDATION_FAILED")


async def test_conflicts_and_limits(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    assert_error(
        await put(client, headers, session, data=b"different"), 409, "CAPTURE_CHUNK_CONFLICT"
    )
    assert_error(
        await put(client, headers, session, modality="speech"), 409, "CAPTURE_CHUNK_CONFLICT"
    )
    metadata, _ = parts(session)
    assert_error(
        await put(client, headers, session, metadata=metadata, data=b"not declared"),
        409,
        "CAPTURE_CHUNK_CONFLICT",
    )
    assert_error(await put(client, headers, session, 1, data=b"x" * 1025), 413, "CAPTURE_TOO_LARGE")
    assert_error(
        await put(client, headers, session, 1, data=b"x" * 18000), 413, "CAPTURE_TOO_LARGE"
    )
    assert (await put(client, headers, session, 1, data=b"x" * 1000)).status_code == 200
    assert_error(await put(client, headers, session, 2), 413, "CAPTURE_TOO_LARGE")
    other = await create(client, headers)
    await put(client, headers, other, 1)
    assert_error(await put(client, headers, other, 0, end=5000), 409, "CAPTURE_FINAL_CHUNK")
    assert (await status(client, headers, other))["session"]["chunks_received"] == 1


@pytest.mark.parametrize(
    "files",
    [
        {"content": ("x", DATA)},
        {"metadata": (None, "{}")},
        {"metadata": ("x", b"{}"), "content": (None, "not a file")},
        {"metadata": (None, "invalid-json"), "content": ("x", DATA)},
        {"metadata": (None, "x" * 10000), "content": ("x", DATA)},
        {"metadata": (None, "{}"), "content": ("x", DATA), "extra": (None, "x")},
    ],
)
async def test_invalid_multipart(client, files):
    headers = await guest(client)
    session = await create(client, headers)
    response = await client.put(
        f"/v1/captures/{session['id']}/chunks/0", files=files, headers=headers
    )
    assert_error(response, 422, "VALIDATION_FAILED")


async def test_wrong_content_type_and_multipart_boundary(client):
    headers = await guest(client)
    session = await create(client, headers)
    for content_type in ["application/json", "multipart/form-data"]:
        response = await client.put(
            f"/v1/captures/{session['id']}/chunks/0",
            content=b"{}",
            headers={**headers, "Content-Type": content_type},
        )
        assert_error(response, 422, "VALIDATION_FAILED")


async def test_expiry_replays_accepted_and_allows_close(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(
            update(capture_sessions)
            .where(capture_sessions.c.id == uuid.UUID(session["id"]))
            .values(expires_at=func.now() - timedelta(seconds=1))
        )
    progress = await status(client, headers, session)
    assert progress["session"]["state"] == "abandoned"
    assert progress["session"]["closed_at"] == progress["expires_at"]
    assert_error(await put(client, headers, session, 1), 410, "CAPTURE_EXPIRED")
    assert (await put(client, headers, session)).json()["disposition"] == "duplicate"
    assert (await close(client, headers, session, False)).status_code == 200


async def test_enqueue_failure_keeps_tracked_reservation_and_retry(
    client, capture_app, monkeypatch
):
    headers = await guest(client)
    session = await create(client, headers)
    original = JobQueue.enqueue

    async def fail(*args, **kwargs):
        raise OSError("synthetic queue outage")

    monkeypatch.setattr(JobQueue, "enqueue", fail)
    assert_error(await put(client, headers, session), 500, "INTERNAL_ERROR")
    progress = await status(client, headers, session)
    assert progress["session"]["chunks_received"] == 0 and progress["work"] == []
    async with capture_app.state.database.engine.connect() as connection:
        pending = (
            await connection.execute(
                select(capture_chunks).where(
                    capture_chunks.c.session_id == uuid.UUID(session["id"])
                )
            )
        ).one()
    assert pending.received_at is None and pending.job_id is None
    assert await capture_app.state.upload_store.digest(pending.storage_key) is not None
    assert_error(
        await put(client, headers, session, data=b"different"), 409, "CAPTURE_CHUNK_CONFLICT"
    )
    monkeypatch.setattr(JobQueue, "enqueue", original)
    assert (await put(client, headers, session)).status_code == 200
    await stored_job(capture_app, session)
    assert len(list(capture_app.state.settings.storage_dir.iterdir())) == 1


async def test_close_waits_for_writing_chunk_and_fences_publication(
    client, capture_app, monkeypatch
):
    headers = await guest(client)
    session = await create(client, headers)
    entered, release = asyncio.Event(), asyncio.Event()
    store = capture_app.state.upload_store
    original = store.write

    async def blocked(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(store, "write", blocked)
    upload = asyncio.create_task(put(client, headers, session))
    await asyncio.wait_for(entered.wait(), 5)
    stop = asyncio.create_task(close(client, headers, session, False))
    release.set()
    responses = await asyncio.gather(upload, stop)
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert (await stored_job(capture_app, session)).state == "cancelled"


async def test_incremental_worker_restart_status_and_cancel(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    queue = JobQueue(capture_app.state.database)
    job = await stored_job(capture_app, session)
    # Restrict eligibility to this synthetic session; other tests share the disposable database.
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.stage == CAPTURE_STAGE, jobs.c.id != job.id, jobs.c.state == "queued")
            .values(available_at=func.now() + timedelta(days=1))
        )
    claim = await queue.claim("capture-test", [CAPTURE_STAGE], 30)
    assert claim.id == job.id
    await queue.start(claim.lease)
    assert (await status(client, headers, session))["work"][0]["processing_status"] == "checking"
    context = JobContext(queue, claim.lease, 30)
    result = await CaptureProcessor(capture_app.state.upload_store).run(claim, context)
    processor = CaptureProcessor(capture_app.state.upload_store)
    with pytest.raises(NonRetriableInput):
        await processor.run(replace(claim, payload={}), context)
    with pytest.raises(NonRetriableInput):
        await processor.run(replace(claim, payload={"owner_id": str(uuid.uuid4())}), context)
    await queue.release(claim.lease)
    finished = asyncio.Event()

    async def checkpoint(name, current):
        if name == "after_publish" and current.id == job.id:
            finished.set()

    worker = Worker(
        capture_app.state.database,
        2,
        handlers=default_handlers(capture_app.state.upload_store),
        faults=ScriptedFaults(on_checkpoint=checkpoint),
        poll_seconds=0.01,
    )
    await worker.start()
    try:
        await asyncio.wait_for(finished.wait(), 5)
    finally:
        await worker.stop()
    assert (await status(client, headers, session))["session"]["state"] == "open"
    assert (await status(client, headers, session))["work"][0]["processing_status"] == "waiting"
    async with capture_app.state.database.engine.connect() as connection:
        published = (
            await connection.execute(select(job_results).where(job_results.c.job_id == job.id))
        ).one()
    assert published.result == result
    with pytest.raises(PublishRejected):
        await queue.publish(claim.lease, {"late": "rejected"})
    assert (await close(client, headers, session, False)).status_code == 200
    assert (await stored_job(capture_app, session)).state == "published"
    assert (await put(client, headers, session)).json()["disposition"] == "duplicate"


async def test_failed_deleted_and_purged_work_are_not_success(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    job = await stored_job(capture_app, session)
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(update(jobs).where(jobs.c.id == job.id).values(state="failed"))
    progress = await status(client, headers, session)
    assert progress["work"][0]["processing_status"] == "failed"
    assert progress["work"][0]["error"]["code"] == "PROCESSING_FAILED"
    assert (await close(client, headers, session, False)).status_code == 200
    stopped = (await status(client, headers, session))["work"][0]
    assert stopped["processing_status"] == "cancelled"
    assert stopped["error"] is None
    await JobQueue(capture_app.state.database).delete(job.id)
    assert (await status(client, headers, session))["work"][0]["processing_status"] == "cancelled"
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(delete(jobs).where(jobs.c.id == job.id))
    assert (await status(client, headers, session))["work"][0]["job_id"] is None
    assert (await close(client, headers, session, False)).status_code == 200


async def test_retention_removes_capture_bytes_including_failed_reservations(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    job = await stored_job(capture_app, session)
    await expire(capture_app, job.owner_id)
    await sweep(capture_app)
    assert not list(capture_app.state.settings.storage_dir.iterdir())
    async with capture_app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(capture_sessions.c.id).where(
                    capture_sessions.c.id == uuid.UUID(session["id"])
                )
            )
            is None
        )
        assert (
            await connection.scalar(
                select(investigations.c.id).where(investigations.c.id == uuid.UUID(session["id"]))
            )
            is None
        )
    assert_error(
        await client.get(f"/v1/captures/{session['id']}", headers=headers),
        401,
        "INVALID_CREDENTIAL",
    )


async def test_pending_cleanup_for_accounts_and_storage_failure_retry(
    client, capture_app, monkeypatch
):
    headers = await guest(client)
    session = await create(client, headers)
    original_enqueue = JobQueue.enqueue

    async def failed_enqueue(*args, **kwargs):
        raise OSError("synthetic outage after storage")

    monkeypatch.setattr(JobQueue, "enqueue", failed_enqueue)
    assert (await put(client, headers, session)).status_code == 500
    monkeypatch.setattr(JobQueue, "enqueue", original_enqueue)
    assert (await close(client, headers, session)).status_code == 200
    # Pending cleanup is independent of the guest-only workspace policy.
    from services.models import principals

    async with capture_app.state.database.engine.begin() as connection:
        owner = await connection.scalar(
            select(capture_sessions.c.owner_id).where(
                capture_sessions.c.id == uuid.UUID(session["id"])
            )
        )
        await connection.execute(
            update(principals).where(principals.c.id == owner).values(kind="account")
        )
    store = capture_app.state.upload_store
    original_delete = store.delete

    async def fail_delete(key):
        raise OSError("synthetic storage deletion failure")

    monkeypatch.setattr(store, "delete", fail_delete)
    with pytest.raises(OSError):
        await sweep(capture_app)
    async with capture_app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(capture_chunks.c.seq).where(
                    capture_chunks.c.session_id == uuid.UUID(session["id"])
                )
            )
            == 0
        )
    monkeypatch.setattr(store, "delete", original_delete)
    await sweep(capture_app)
    assert not list(capture_app.state.settings.storage_dir.iterdir())
    assert (await status(client, headers, session))["session"]["state"] == "closed"


async def test_stop_and_cleanup_between_reservation_and_commit(client, capture_app, monkeypatch):
    headers = await guest(client)
    session = await create(client, headers)
    original = capture_routes.reserve

    async def reserve_then_stop(*args):
        await original(*args)
        assert (await close(client, headers, session, False)).status_code == 200
        await sweep(capture_app)

    monkeypatch.setattr(capture_routes, "reserve", reserve_then_stop)
    assert_error(await put(client, headers, session), 409, "CAPTURE_CLOSED")
    assert (await status(client, headers, session))["session"]["chunks_received"] == 0


async def test_expiry_during_store_does_not_acknowledge_or_enqueue(
    client, capture_app, monkeypatch
):
    headers = await guest(client)
    session = await create(client, headers)
    expiry = (await status(client, headers, session))["expires_at"]
    from datetime import datetime

    store = capture_app.state.upload_store
    original = store.write

    async def late_clock(connection):
        return datetime.fromisoformat(expiry)

    async def slow_write(*args):
        result = await original(*args)
        monkeypatch.setattr(capture_routes, "database_time", late_clock)
        return result

    monkeypatch.setattr(store, "write", slow_write)
    assert_error(await put(client, headers, session), 410, "CAPTURE_EXPIRED")
    assert (await status(client, headers, session))["work"] == []


async def test_cancel_running_chunk_rejects_late_publication(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    await put(client, headers, session)
    row = await stored_job(capture_app, session)
    queue = JobQueue(capture_app.state.database)
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.stage == CAPTURE_STAGE, jobs.c.id != row.id, jobs.c.state == "queued")
            .values(available_at=func.now() + timedelta(days=1))
        )
    claim = await queue.claim("capture-cancel", [CAPTURE_STAGE], 30)
    assert claim.id == row.id
    await queue.start(claim.lease)
    assert (await close(client, headers, session, False)).status_code == 200
    assert (await status(client, headers, session))["work"][0]["processing_status"] == "cancelled"
    with pytest.raises(PublishRejected):
        await queue.publish(claim.lease, {"late": "result"})
    await queue.release(claim.lease)


@pytest.mark.parametrize(
    "body",
    [
        {"continue_research": "false"},
        {"continue_research": True, "duration_ms": 180001},
        {"continue_research": True, "duration_ms": -1},
        {},
    ],
)
async def test_close_strictness(client, body):
    headers = await guest(client)
    session = await create(client, headers)
    response = await client.post(f"/v1/captures/{session['id']}/close", json=body, headers=headers)
    assert_error(response, 422, "VALIDATION_FAILED")


async def test_api_restart_preserves_chunk_and_close_receipts(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    first = await put(client, headers, session)
    closed = await close(client, headers, session)
    application = create_app(capture_app.state.settings)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as restarted:
            replay = await put(restarted, headers, session)
            assert replay.json() == {**first.json(), "disposition": "duplicate"}
            assert (await close(restarted, headers, session)).json() == closed.json()


async def test_poll_does_not_wait_for_session_writer(client, capture_app):
    headers = await guest(client)
    session = await create(client, headers)
    async with capture_app.state.database.engine.begin() as connection:
        await connection.execute(
            update(capture_sessions)
            .where(capture_sessions.c.id == uuid.UUID(session["id"]))
            .values(state="closed", continue_research=False)
        )
        progress = await asyncio.wait_for(status(client, headers, session), 2)
        assert progress["session"]["state"] == "open"
        assert progress["continue_research"] is None
    assert (await status(client, headers, session))["session"]["state"] == "closed"


async def test_poll_keeps_one_snapshot_while_upload_commits(client, capture_app, monkeypatch):
    headers = await guest(client)
    session = await create(client, headers)
    original_chunks_for = capture_routes.chunks_for
    committed = False

    async def upload_between_reads(connection, capture_id):
        nonlocal committed
        if not committed:
            committed = True
            assert (await asyncio.wait_for(put(client, headers, session), 2)).status_code == 200
        return await original_chunks_for(connection, capture_id)

    monkeypatch.setattr(capture_routes, "chunks_for", upload_between_reads)
    progress = await status(client, headers, session)
    assert progress["session"]["chunks_received"] == 0
    assert progress["work"] == []
    assert progress["manifest"]["duration_ms"] == 0
    latest = await status(client, headers, session)
    assert latest["session"]["chunks_received"] == 1
    assert len(latest["work"]) == 1


@pytest.mark.parametrize("failure", ["none", "metadata", "hash"])
async def test_returned_multipart_form_is_closed(client, spools, failure):
    headers = await guest(client)
    session = await create(client, headers)
    metadata, _ = parts(session)
    if failure == "metadata":
        metadata["unexpected"] = True
    elif failure == "hash":
        metadata["chunk"]["sha256"] = "0" * 64
    response = await put(client, headers, session, metadata=metadata)
    assert response.status_code == {"none": 200, "metadata": 422, "hash": 409}[failure]
    assert spools
    assert all(spool.closed for spool in spools)


async def test_returned_form_is_closed_on_task_cancellation(capture_app, spools):
    metadata, data = parts({"id": str(uuid.uuid4()), "chunk_duration_ms": 10000})
    upload = httpx.Request(
        "PUT",
        "http://test",
        files={
            "metadata": (None, json.dumps(metadata)),
            "content": ("chunk.bin", data),
        },
    )

    async def receive():
        return {"type": "http.request", "body": upload.read(), "more_body": False}

    request = Request(
        {
            "type": "http",
            "app": capture_app,
            "headers": [(key.lower(), value) for key, value in upload.headers.raw],
        },
        receive=receive,
    )
    entered = asyncio.Event()

    async def consume():
        async with capture_routes.chunk_body(request):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert spools
    assert all(spool.closed for spool in spools)


@pytest.mark.parametrize("failure", [asyncio.CancelledError, OSError])
async def test_parser_closes_partial_file_when_request_stream_fails(capture_app, spools, failure):
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "type": "http.request",
                "body": (
                    b"--synthetic\r\n"
                    b'Content-Disposition: form-data; name="content"; filename="chunk.bin"\r\n'
                    b"Content-Type: application/octet-stream\r\n\r\npartial synthetic bytes"
                ),
                "more_body": True,
            }
        raise failure()

    request = Request(
        {
            "type": "http",
            "app": capture_app,
            "headers": [(b"content-type", b"multipart/form-data; boundary=synthetic")],
        },
        receive=receive,
    )
    with pytest.raises(failure):
        async with capture_routes.chunk_body(request):
            pytest.fail("An incomplete failed stream must never yield a form")
    assert spools
    assert all(spool.closed for spool in spools)
