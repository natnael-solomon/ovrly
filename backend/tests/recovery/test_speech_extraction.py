"""Committed uploaded and captured speech feeds the incremental extraction ledger."""

import asyncio
import json
import uuid

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from recovery.test_capture_processing import finish, guest, package, send, start
from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_media_validation import audio_bytes, submit
from recovery.test_speech import speech_settings, success
from services.api.main import create_app
from services.asr.groq import GroqAdapter
from services.captures import stage_key
from services.jobs.handlers import default_handlers
from services.jobs.models import job_results, jobs
from services.models import asr_requests
from services.pipeline.incremental import ExtractionPolicy, submit_observations
from services.pipeline.llm import ScholarxivAdapter
from services.storage import LocalFilesystemStore

EXTRACTION = {
    "extraction_enabled": True,
    "extraction_free_routes_verified": True,
    "extraction_models": ["fixture-free-model"],
    "scholarxiv_api_key": "synthetic-offline-key",
}


def grounded(request):
    """Echo one occurrence per observation, each grounded in that observation's text."""
    observations = json.loads(json.loads(request.content)["messages"][1]["content"])["observations"]
    output = extraction()
    template = output["occurrences"][0]
    output["occurrences"] = [
        {
            **template,
            "source_refs": [
                {"observation_id": item["id"], "start_char": 0, "end_char": len(item["text"])}
            ],
        }
        for item in observations
    ]
    return httpx.Response(200, json=completion(output))


class Pipeline:
    """A worker running real media, ASR, capture and extraction handlers with fakes."""

    def __init__(self, harness, config, llm_replay):
        self.asr_calls = []
        self.llm_calls = []

        def asr(request):
            self.asr_calls.append(request)
            return success(request)

        def llm(request):
            self.llm_calls.append(json.loads(request.content))
            return llm_replay(request)

        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(llm), base_url="https://router.example"
        )
        groq = GroqAdapter(
            api_key="synthetic-offline-key",
            model=config.groq_model,
            max_audio_bytes=config.asr_audio_max_bytes,
            transport=httpx.MockTransport(asr),
        )
        self.worker = harness.worker(
            None,
            stages=default_handlers(
                LocalFilesystemStore(config.storage_dir),
                settings=config,
                asr_adapter=groq,
                llm=ScholarxivAdapter(
                    self.http, allowed_models=["fixture-free-model"], max_tokens=2048
                ),
            ),
            database_timeout_seconds=10,
            worker_shutdown_seconds=10,
            job_lease_seconds=2,
        )

    async def stop(self):
        await self.worker.stop()
        await self.http.aclose()


async def scalar(harness, statement):
    async with harness.control.engine.connect() as connection:
        return await connection.scalar(statement)


async def published(harness, key):
    async with asyncio.timeout(15):
        while True:
            if await harness.job_id_for(key) is not None:
                break
            await asyncio.sleep(0.05)
    await harness.wait_for_state_by_key(key, "published", seconds=15)


async def read(http, headers, identifier):
    response = await http.get(f"/v1/investigations/{identifier}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def until(http, headers, identifier, done, seconds=15):
    async with asyncio.timeout(seconds):
        while True:
            body = await read(http, headers, identifier)
            if done(body):
                return body
            await asyncio.sleep(0.05)


def progress_closed(body):
    progress = body.get("extraction_progress")
    return (
        progress is not None
        and progress["closed"]
        and all(item["status"] != "pending" for item in progress["observations"])
    )


async def test_uploaded_speech_settles_into_a_media_timed_provisional_report(harness, tmp_path):
    config = speech_settings(harness, tmp_path, text_grace_seconds=0, **EXTRACTION)
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await pipeline.worker.start()
        try:
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
    progress = body["extraction_progress"]
    assert [
        (item["observation_id"], item["timebase"], item["status"])
        for item in progress["observations"]
    ] == [("upload:speech:0", "media", "processed")]
    [request] = pipeline.llm_calls
    [observation] = json.loads(request["messages"][1]["content"])["observations"]
    assert observation["speaker_id"] is None
    assert (observation["start_ms"], observation["end_ms"]) == (125, 875)
    assert body["report"]["provisional"] is True
    assert len(body["report"]["claims"]) == 1
    assert body["speech"]["status"] == "completed"
    assert body["analysis"] is not None
    assert len(pipeline.asr_calls) == 1


async def test_failed_extraction_never_repeats_hosted_speech(harness, tmp_path):
    config = speech_settings(harness, tmp_path, text_grace_seconds=0, **EXTRACTION)
    pipeline = Pipeline(harness, config, lambda _: httpx.Response(400, json={}))
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await pipeline.worker.start()
        try:
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
        reservations = await scalar(harness, select(func.count()).select_from(asr_requests))
        restarted = Pipeline(harness, config, grounded)
        await restarted.worker.start()
        await asyncio.sleep(0.5)
        await restarted.stop()
        after = await read(http, headers, identifier)
    assert [item["status"] for item in body["extraction_progress"]["observations"]] == ["failed"]
    assert len(pipeline.asr_calls) == 1
    assert restarted.asr_calls == [] and restarted.llm_calls == []
    assert await scalar(harness, select(func.count()).select_from(asr_requests)) == (reservations)
    assert after["extraction_progress"] == body["extraction_progress"]


async def test_unavailable_speech_admits_nothing_and_keeps_its_gap(harness, tmp_path):
    config = speech_settings(harness, tmp_path, text_grace_seconds=0, asr_enabled=False)
    # model_copy skips validation, so the secret is wrapped as Settings would wrap it.
    key = SecretStr(EXTRACTION["scholarxiv_api_key"])
    config = config.model_copy(update={**EXTRACTION, "scholarxiv_api_key": key})
    pipeline = Pipeline(harness, config, grounded)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audio_bytes())
        await pipeline.worker.start()
        try:
            body = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "unavailable",
            )
            await asyncio.sleep(0.3)
            body = await read(http, headers, identifier)
        finally:
            await pipeline.stop()
    assert body["speech"]["reason"] == "disabled"
    assert body.get("extraction_progress") is None
    assert pipeline.llm_calls == [] and pipeline.asr_calls == []


@pytest.fixture
async def capture(harness, tmp_path):
    def build(**overrides):
        return speech_settings(
            harness, tmp_path, asr_audio_max_bytes=400_000, **(EXTRACTION | overrides)
        )

    return build


async def test_captured_chunks_feed_one_capture_timed_ledger_after_continue(harness, capture):
    config = capture()
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
            data = package(1)
            assert (await send(http, headers, identifier, 1, data)).status_code == 200
            assert (await send(http, headers, identifier, 1, data)).status_code == 200
            assert (await send(http, headers, identifier, 0, package(0))).status_code == 200
            await until(
                http,
                headers,
                identifier,
                lambda body: (
                    len((body.get("extraction_progress") or {}).get("observations", [])) == 4
                ),
            )
            assert pipeline.llm_calls == []
            await finish(http, headers, identifier)
            body = await until(http, headers, identifier, progress_closed)
        finally:
            await pipeline.stop()
    observed = [
        (item["observation_id"].rsplit(":", 1)[0], item["timebase"], item["status"])
        for item in body["extraction_progress"]["observations"]
    ]
    assert observed == [
        ("capture:0:speech", "capture", "processed"),
        ("capture:0:text", "capture", "processed"),
        ("capture:1:speech", "capture", "processed"),
        ("capture:1:text", "capture", "processed"),
    ]
    [request] = pipeline.llm_calls
    observations = json.loads(request["messages"][1]["content"])["observations"]
    assert [(item["modality"], item["start_ms"]) for item in observations] == [
        ("speech", 125),
        ("text", 500),
        ("speech", 10125),
        ("text", 10500),
    ]
    assert len(pipeline.asr_calls) == 2


async def test_keep_only_results_never_settles_or_starts_inference(harness, capture):
    config = capture()
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
            assert (await send(http, headers, identifier, 0, package(0))).status_code == 200
            await until(
                http, headers, identifier, lambda body: body.get("extraction_progress") is not None
            )
            await finish(http, headers, identifier, choice=False)
            await asyncio.sleep(0.5)
            body = await read(http, headers, identifier)
        finally:
            await pipeline.stop()
    assert pipeline.llm_calls == []
    assert all(
        item["status"] != "processed" for item in body["extraction_progress"]["observations"]
    )


async def test_disabled_extraction_publishes_the_chunk_bridge_without_a_ledger(harness, capture):
    config = capture(extraction_enabled=False)
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
            assert (await send(http, headers, identifier, 0, package(0))).status_code == 200

            key = stage_key(uuid.UUID(identifier), 0, "claim_extraction")
            await published(harness, key)
            body = await read(http, headers, identifier)
        finally:
            await pipeline.stop()
    result = await scalar(
        harness,
        select(job_results.c.result)
        .join(jobs, jobs.c.id == job_results.c.job_id)
        .where(jobs.c.stage == key.stage, jobs.c.input_hash == key.input_hash),
    )
    assert result == {"capture_id": identifier, "seq": 0, "admitted": "disabled"}
    assert body.get("extraction_progress") is None
    assert pipeline.llm_calls == []


async def test_producer_input_reports_late_and_over_bound_observations(harness):
    app = harness.app(None, stages={})
    async with app.router.lifespan_context(app), harness.client(app) as client:
        investigation_id = await create_investigation(client)
        policy = ExtractionPolicy(
            max_requests=10,
            max_tokens=200000,
            reconciliation_requests=2,
            reconciliation_tokens=20000,
            batch_observations=4,
            overlap_observations=1,
            max_observations=1,
        )
        first = window().observations[0]
        second = first.model_copy(update={"id": "speech-2", "start_ms": 5000, "end_ms": 8000})
        third = first.model_copy(update={"id": "speech-3", "start_ms": 9000, "end_ms": 12000})
        producer = {"source": "upload", "reconcile": False}

        async def admit(observations, closed):
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=observations,
                    closed=closed,
                    hosted_processing_approved=True,
                    producer=producer,
                )

        await admit([first, second], False)
        await admit([], True)
        await admit([third], False)
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
    reasons = {
        item["observation_id"]: (item["status"], item["reason"])
        for item in body["extraction_progress"]["observations"]
    }
    assert reasons["speech-2"] == ("skipped", "budget_exhausted")
    assert reasons["speech-3"] == ("skipped", "input_closed")
    assert reasons["speech-1"][0] != "skipped"
