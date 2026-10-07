import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import delete, func, select, update
from test_intake_api import guest

from services.api.main import create_app
from services.captures import CAPTURE_STAGES, CaptureProcessor, stage_key
from services.jobs.handlers import JobContext, default_handlers
from services.jobs.models import jobs
from services.jobs.queue import JobQueue, PublishRejected
from services.jobs.retries import NonRetriableInput
from services.models import capture_chunks, capture_sessions
from services.pipeline.capture import publish_capture_stage
from services.pipeline.stub_reports import _CaptureStage, capture_report, enable_stub_reports
from services.reports import publish_report_version
from services.settings import Settings
from services.worker.runtime import Worker

from .test_captures import close, create, put, status


@pytest.fixture
async def pipeline(database_url, tmp_path):
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "captures",
            stub_reports=True,
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        async with app.state.database.engine.begin() as connection:
            await connection.execute(
                update(jobs)
                .where(jobs.c.state == "queued")
                .values(available_at=func.now() + timedelta(days=1))
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = await guest(client)
            session = await create(client, headers)
            assert (await put(client, headers, session)).status_code == 200
            try:
                yield app, client, headers, session
            finally:
                queue = JobQueue(app.state.database)
                async with app.state.database.engine.begin() as connection:
                    identifiers = (
                        (
                            await connection.execute(
                                select(jobs.c.id).where(
                                    jobs.c.payload["capture_id"].as_string() == session["id"]
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    for identifier in identifiers:
                        await queue.delete(identifier, connection=connection)


async def stage_rows(app, session):
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(
                select(jobs)
                .where(jobs.c.input_hash == stage_key(uuid.UUID(session["id"]), 0).input_hash)
                .order_by(jobs.c.created_at, jobs.c.stage)
            )
        ).all()


async def claim_stage(app, stage):
    queue = JobQueue(app.state.database)
    claim = await queue.claim("capture-pipeline", [stage], 30)
    assert claim is not None
    assert await queue.start(claim.lease)
    return queue, claim


async def validate(app):
    queue, claim = await claim_stage(app, "media_validation")
    handlers = dict(default_handlers(app.state.upload_store))
    if app.state.settings.stub_reports:
        enable_stub_reports(handlers)
    result = await handlers["media_validation"](claim, JobContext(queue, claim.lease, 30))
    await publish_capture_stage(queue, claim, result)
    return queue, claim


@pytest.mark.parametrize("first", ["asr", "device_text"])
async def test_fan_out_then_fan_in_is_per_chunk_owned_and_idempotent(pipeline, first):
    app, client, headers, session = pipeline
    queue, validation = await validate(app)
    rows = await stage_rows(app, session)
    assert {row.stage for row in rows} == {"media_validation", "asr", "device_text"}
    assert all(row.owner_id == rows[0].owner_id for row in rows)
    assert validation.payload == {"capture_id": session["id"], "seq": 0}
    assert all(
        {key: value for key, value in row.payload.items() if key != "stub_validated"}
        == validation.payload
        for row in rows
    )
    assert all(
        row.stage in CAPTURE_STAGES and row.input_hash == validation.key.input_hash for row in rows
    )
    assert (await status(client, headers, session))["session"]["state"] == "open"
    assert (await put(client, headers, session)).json()["disposition"] == "duplicate"
    with pytest.raises(PublishRejected):
        await publish_capture_stage(queue, validation, {})
    assert len(await stage_rows(app, session)) == 3
    _, one = await claim_stage(app, first)
    await publish_capture_stage(queue, one, {"synthetic": True})
    assert len(await stage_rows(app, session)) == 3
    other = "device_text" if first == "asr" else "asr"
    _, two = await claim_stage(app, other)
    await publish_capture_stage(queue, two, {"synthetic": True})
    rows = await stage_rows(app, session)
    assert len(rows) == 4
    extraction = next(row for row in rows if row.stage == "claim_extraction")
    assert extraction.payload == validation.payload and extraction.state == "queued"
    with pytest.raises(PublishRejected):
        await publish_capture_stage(queue, two, {})
    _, final = await claim_stage(app, "claim_extraction")
    await publish_capture_stage(queue, final, {"synthetic": True})
    assert {row.state for row in await stage_rows(app, session)} == {"published"}
    assert set(default_handlers(app.state.upload_store)) == {"intake", "media_validation"}


async def test_concurrent_observation_publication_enqueues_extraction_once(pipeline):
    app, _, _, session = pipeline
    queue, _ = await validate(app)
    _, asr = await claim_stage(app, "asr")
    _, text = await claim_stage(app, "device_text")
    await asyncio.gather(
        publish_capture_stage(queue, asr, {"synthetic": True}),
        publish_capture_stage(queue, text, {"synthetic": True}),
    )
    assert [row.stage for row in await stage_rows(app, session)].count("claim_extraction") == 1


async def test_uploaded_media_registry_preserves_capture_fanout_and_registers_chunk_stages(
    pipeline,
):
    app, _, _, session = pipeline
    handlers = default_handlers(app.state.upload_store, settings=app.state.settings)
    worker = Worker(app.state.database, 2, handlers=handlers)
    claim = await worker.queue.claim("mixed-pipeline", list(handlers), 30)
    assert claim is not None and claim.key.stage == "media_validation"
    await worker._execute(claim)
    rows = {row.stage: row for row in await stage_rows(app, session)}
    assert rows["media_validation"].state == "published"
    assert rows["asr"].state == rows["device_text"].state == "queued"
    assert {"asr", "device_text", "upload_asr"} <= set(handlers)


async def test_enqueue_failure_rolls_back_publication_and_worker_retries(pipeline, monkeypatch):
    app, _, _, session = pipeline
    worker = Worker(app.state.database, 2, handlers=default_handlers(app.state.upload_store))
    claim = await worker.queue.claim("rollback-worker", ["media_validation"], 30)
    original = worker.queue.enqueue

    async def interrupted(connection, key, payload, **kwargs):
        if key.stage == "device_text":
            raise OSError("synthetic connection failure")
        return await original(connection, key, payload, **kwargs)

    monkeypatch.setattr(worker.queue, "enqueue", interrupted)
    await worker._execute(claim)
    rows = await stage_rows(app, session)
    assert len(rows) == 1 and rows[0].state == "queued"
    assert await worker.queue.published(claim.key) == []
    monkeypatch.setattr(worker.queue, "enqueue", original)
    retry = await worker.queue.claim("restarted-worker", ["media_validation"], 30)
    await worker._execute(retry)
    assert len(await stage_rows(app, session)) == 3
    assert len(await worker.queue.published(claim.key)) == 1


async def test_stop_cancels_all_stages_and_fences_delayed_publication(pipeline):
    app, client, headers, session = pipeline
    queue, _ = await validate(app)
    _, asr = await claim_stage(app, "asr")
    assert (await status(client, headers, session))["work"][0]["processing_status"] == "checking"
    assert (await close(client, headers, session, False)).status_code == 200
    assert await publish_capture_stage(queue, asr, {"late": True}) is None
    rows = {row.stage: row for row in await stage_rows(app, session)}
    assert rows["asr"].cancel_requested and rows["device_text"].state == "cancelled"
    assert "claim_extraction" not in rows
    assert await queue.published(asr.key) == []
    assert (await status(client, headers, session))["claims"][0]["processing_status"] == "cancelled"


async def test_stop_with_continuation_allows_pipeline_and_failure_is_visible(pipeline):
    app, client, headers, session = pipeline
    queue, _ = await validate(app)
    assert (await close(client, headers, session, True)).status_code == 200
    _, asr = await claim_stage(app, "asr")
    assert await queue.fail(asr.lease, "SyntheticFailure")
    _, text = await claim_stage(app, "device_text")
    await publish_capture_stage(queue, text, {"synthetic": True})
    polled = await status(client, headers, session)
    assert polled["work"][0]["processing_status"] == "failed"
    assert polled["work"][0]["error"]["code"] == "PROCESSING_FAILED"
    assert len(await stage_rows(app, session)) == 3


async def test_legacy_payload_is_supported_and_cross_owner_payload_is_rejected(pipeline):
    app, _, _, session = pipeline
    queue, claim = await claim_stage(app, "media_validation")
    row = (await stage_rows(app, session))[0]
    legacy = replace(
        claim, payload={"session_id": session["id"], "seq": 0, "owner_id": str(row.owner_id)}
    )
    result = await CaptureProcessor(app.state.upload_store).run(
        legacy, JobContext(queue, legacy.lease, 30)
    )
    await publish_capture_stage(queue, legacy, result)
    _, asr = await claim_stage(app, "asr")
    with pytest.raises(PublishRejected, match="stage key"):
        await publish_capture_stage(
            queue, replace(asr, payload={"capture_id": str(uuid.uuid4()), "seq": 0}), {}
        )
    assert len(await stage_rows(app, session)) == 3


async def test_stale_lease_and_deleted_validation_cannot_spawn_successors(pipeline):
    app, _, _, session = pipeline
    queue, claim = await claim_stage(app, "media_validation")
    await queue.release(claim.lease)
    _, fresh = await claim_stage(app, "media_validation")
    with pytest.raises(PublishRejected):
        await publish_capture_stage(queue, claim, {})
    assert len(await stage_rows(app, session)) == 1
    await publish_capture_stage(queue, fresh, {})
    _, asr = await claim_stage(app, "asr")
    await queue.delete(fresh.id)
    assert await publish_capture_stage(queue, asr, {}) is None
    assert await queue.published(asr.key) == []
    assert len(await stage_rows(app, session)) == 3


async def test_report_polling_uses_latest_real_version_without_stub_flag(pipeline):
    app, client, headers, session = pipeline
    app.state.settings.stub_reports = False
    identifier = uuid.UUID(session["id"])
    for assessed, final in [(0, False), (1, False), (2, True)]:
        async with app.state.database.engine.begin() as connection:
            report = await publish_report_version(
                connection,
                identifier,
                lambda identity, assessed=assessed, final=final: capture_report(
                    _CaptureStage([(0, 10000), (10000, 20000)], assessed, final), identity
                ),
                fixture=False,
            )
        polled = await status(client, headers, session)
        assert [claim["claim_id"] for claim in polled["claims"]] == [
            claim.id for claim in report.claims
        ]
        assert [claim["processing_status"] for claim in polled["claims"]] == (
            ["complete", "complete"]
            if final
            else ["partial"] * assessed + ["checking"] * (2 - assessed)
        )
        assert polled["claim_extraction_status"] == ("complete" if final else "partial")
    other = await guest(client)
    assert (await client.get(f"/v1/captures/{session['id']}", headers=other)).status_code == 404


async def test_stub_worker_publication_flows_through_orchestration(pipeline):
    app, client, headers, session = pipeline
    handlers = dict(default_handlers(app.state.upload_store))
    enable_stub_reports(handlers)
    worker = Worker(app.state.database, 2, handlers=handlers)
    claim = await worker.queue.claim("stub-worker", ["media_validation"], 30)
    await worker._execute(claim)
    polled = await status(client, headers, session)
    assert polled["claims"][0]["processing_status"] == "checking"
    assert len(await stage_rows(app, session)) == 3
    response = await client.get(f"/v1/investigations/{session['id']}", headers=headers)
    assert response.json()["report"]["fixture"] is True
    assert response.json()["job"]["stage"] in {"asr", "device_text"}
    assert (await close(client, headers, session, True)).status_code == 200
    assert (await status(client, headers, session))["claims"][0]["processing_status"] == "complete"


async def test_two_chunks_cannot_satisfy_each_others_fan_in(pipeline):
    app, client, headers, session = pipeline
    assert (await put(client, headers, session, 1)).status_code == 200
    queue, _ = await validate(app)
    await validate(app)
    _, asr_zero = await claim_stage(app, "asr")
    _, asr_one = await claim_stage(app, "asr")
    _, text_zero = await claim_stage(app, "device_text")
    _, text_one = await claim_stage(app, "device_text")
    assert asr_zero.payload["seq"] == text_zero.payload["seq"] == 0
    assert asr_one.payload["seq"] == text_one.payload["seq"] == 1
    await publish_capture_stage(queue, asr_zero, {})
    await publish_capture_stage(queue, text_one, {})
    assert await queue.claim("no-cross-chunk", ["claim_extraction"], 30) is None
    await publish_capture_stage(queue, text_zero, {})
    _, extraction_zero = await claim_stage(app, "claim_extraction")
    assert extraction_zero.payload == {"capture_id": session["id"], "seq": 0}
    await publish_capture_stage(queue, asr_one, {})
    _, extraction_one = await claim_stage(app, "claim_extraction")
    assert extraction_one.payload == {"capture_id": session["id"], "seq": 1}


async def test_stop_racing_with_publication_never_leaves_runnable_children(pipeline):
    app, client, headers, session = pipeline
    queue, _ = await validate(app)
    _, asr = await claim_stage(app, "asr")
    _, text = await claim_stage(app, "device_text")
    await publish_capture_stage(queue, asr, {})
    await asyncio.gather(
        publish_capture_stage(queue, text, {}),
        close(client, headers, session, False),
    )
    rows = await stage_rows(app, session)
    assert all(row.state in {"published", "cancelled"} or row.cancel_requested for row in rows)
    assert await queue.claim("after-stop", ["claim_extraction"], 30) is None


@pytest.mark.parametrize("missing", ["session", "chunk"])
async def test_missing_capture_cannot_publish_or_enqueue(pipeline, missing):
    app, _, _, session = pipeline
    queue, claim = await claim_stage(app, "media_validation")
    async with app.state.database.engine.begin() as connection:
        if missing == "session":
            await connection.execute(
                delete(capture_sessions).where(capture_sessions.c.id == uuid.UUID(session["id"]))
            )
        else:
            await connection.execute(
                delete(capture_chunks).where(
                    capture_chunks.c.session_id == uuid.UUID(session["id"])
                )
            )
    assert await publish_capture_stage(queue, claim, {}) is None
    assert await queue.published(claim.key) == []
    assert len(await stage_rows(app, session)) == 1


async def test_inconsistent_ownership_and_unpublished_prerequisite_are_rejected(pipeline):
    app, client, _, session = pipeline
    queue, root = await validate(app)
    _, asr = await claim_stage(app, "asr")
    other = await guest(client)
    other_session = await create(client, other)
    async with app.state.database.engine.begin() as connection:
        owner = await connection.scalar(
            select(capture_sessions.c.owner_id).where(
                capture_sessions.c.id == uuid.UUID(other_session["id"])
            )
        )
        await connection.execute(
            update(jobs)
            .where(jobs.c.input_hash == root.key.input_hash, jobs.c.stage == "device_text")
            .values(owner_id=owner)
        )
    with pytest.raises(PublishRejected, match="ownership"):
        await publish_capture_stage(queue, asr, {})
    async with app.state.database.engine.begin() as connection:
        owner = await connection.scalar(select(jobs.c.owner_id).where(jobs.c.id == root.id))
        await connection.execute(
            update(jobs).where(jobs.c.input_hash == root.key.input_hash).values(owner_id=owner)
        )
        await connection.execute(update(jobs).where(jobs.c.id == root.id).values(state="running"))
    with pytest.raises(PublishRejected, match="prerequisites"):
        await publish_capture_stage(queue, asr, {})
    assert await queue.published(asr.key) == []


@pytest.mark.parametrize("seq", [True, -1, "0", None])
async def test_invalid_capture_sequence_is_an_explicit_failure(pipeline, seq):
    app, _, _, _ = pipeline
    queue, claim = await claim_stage(app, "media_validation")
    with pytest.raises(NonRetriableInput):
        await CaptureProcessor(app.state.upload_store).run(
            replace(claim, payload={**claim.payload, "seq": seq}),
            JobContext(queue, claim.lease, 30),
        )


async def test_completed_no_claims_report_is_distinct_from_not_started(pipeline):
    app, client, headers, session = pipeline
    app.state.settings.stub_reports = False
    assert (await status(client, headers, session))["claim_extraction_status"] == "not_started"
    async with app.state.database.engine.begin() as connection:
        await publish_report_version(
            connection,
            uuid.UUID(session["id"]),
            lambda identity: capture_report(_CaptureStage([], 0, True), identity),
        )
    polled = await status(client, headers, session)
    assert polled["claims"] == [] and polled["claim_extraction_status"] == "complete"
