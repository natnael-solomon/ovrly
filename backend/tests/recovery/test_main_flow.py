"""The default-on main flow (#127): upload or capture to a published, assessed report.

Production defaults run every stage: media validation, Groq speech, device text, claim
extraction, whole-input reconciliation, then BE-09 retrieval and assessment. Every provider
answer is a committed synthetic cassette (``tests/cassettes``); no request leaves the test.
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from evidence_cassettes import KEY, Providers

from recovery.test_analysis import audiovisual, send_text, text_source
from recovery.test_capture_processing import finish, guest, package, send, start
from recovery.test_media_validation import submit
from services.api.main import create_app
from services.asr.groq import GroqAdapter
from services.evidence.stages import EvidenceStages
from services.jobs.handlers import default_handlers
from services.pipeline.llm import ScholarxivAdapter, configured_llm
from services.storage import LocalFilesystemStore

__all__ = ["audiovisual"]

CASSETTES = Path(__file__).resolve().parent.parent / "cassettes" / "main_flow"
CHEAP = "synthetic-cheap-model"
QUALITY = "synthetic-quality-model"


def cassette(name):
    return json.loads((CASSETTES / name).read_text())


@pytest.fixture
def production_defaults(monkeypatch):
    """Drop the suite-wide stage opt-outs (conftest) so the shipped defaults apply."""
    for name in ("OVRLY_ASR_ENABLED", "OVRLY_EXTRACTION_ENABLED", "OVRLY_RECONCILIATION_ENABLED"):
        monkeypatch.delenv(name, raising=False)


def configured(harness, tmp_path, **overrides):
    """Provider configuration only; every stage switch keeps its production default."""
    values = {
        "storage_dir": tmp_path / "uploads",
        "artifacts_dir": tmp_path / "artifacts",
        "database_timeout_seconds": 10,
        "worker_shutdown_seconds": 10,
        "job_lease_seconds": 2,
        "groq_api_key": "synthetic-offline-key",
        "groq_model": "synthetic-model",
        "groq_account_id": "synthetic-main-flow",
        "asr_limits_verified_on": date(2026, 10, 7),
        "asr_audio_max_bytes": 400_000,
        "asr_requests_per_minute": 100,
        "asr_requests_per_day": 100,
        "asr_audio_seconds_per_hour": 1000,
        "asr_audio_seconds_per_day": 1000,
        "asr_minimum_billable_seconds": 1,
        "extraction_free_routes_verified": True,
        "extraction_models": [CHEAP],
        "reconciliation_free_routes_verified": True,
        "reconciliation_models": [QUALITY],
        "scholarxiv_api_key": KEY,
    }
    return harness.settings(**(values | overrides))


class Replay:
    """Answers Groq, the extraction/reconciliation router and every evidence provider.

    ``extraction`` names the recorded extraction cassette; ``correct`` makes reconciliation
    link the later of two claims as the correction of the earlier one.
    """

    def __init__(self, extraction="scholarxiv_extraction.json", correct=False):
        self.asr = []
        self.llm = []
        self.evidence = Providers()
        self.extraction = extraction
        self.correct = correct

    def groq(self, request):
        self.asr.append(request)
        return httpx.Response(200, json=cassette("groq_transcription.json"))

    def router(self, request):
        body = json.loads(request.content)
        self.llm.append(body)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            recorded = cassette(self.extraction)
            speech = next(
                item["id"] for item in source["observations"] if item["modality"] == "speech"
            )
            message = recorded["choices"][0]["message"]
            message["content"] = message["content"].replace("{speech_observation}", speech)
            return httpx.Response(200, json=recorded)
        recorded = cassette("scholarxiv_reconciliation.json")
        message = recorded["choices"][0]["message"]
        content = json.loads(message["content"])
        claims = sorted(
            source["claims"],
            key=lambda claim: claim["interpretation"]["source_refs"][0]["start_char"],
        )
        content["updates"] = [
            {
                "claim_id": claim["id"],
                "proposition": claim["proposition"],
                "interpretation": claim["interpretation"],
                "corrects": claims[0]["id"] if self.correct and index == 1 else None,
            }
            for index, claim in enumerate(claims)
        ]
        message["content"] = json.dumps(content)
        return httpx.Response(200, json=recorded)


@pytest.fixture
async def replays():
    """Build a :class:`Replay` per test. Each recovery case has its own database (#131),
    so provider buckets and queued jobs never carry over from another case."""
    clients = []

    def build(**options):
        replay = Replay(**options)
        replay.http = httpx.AsyncClient(
            transport=httpx.MockTransport(replay.router), base_url="https://router.example"
        )
        clients.append(replay.http)
        return replay

    yield build
    for client in clients:
        await client.aclose()


@pytest.fixture
def replay(replays):
    return replays()


def main_flow_worker(harness, config, replay):
    """The production stage table with only the provider transports replaced."""
    assert isinstance(configured_llm(config), ScholarxivAdapter)
    assert isinstance(configured_llm(config, task="reconciliation"), ScholarxivAdapter)
    stages = dict(
        default_handlers(
            LocalFilesystemStore(config.storage_dir),
            settings=config,
            asr_adapter=GroqAdapter(
                api_key="synthetic-offline-key",
                model=config.groq_model,
                max_audio_bytes=config.asr_audio_max_bytes,
                transport=httpx.MockTransport(replay.groq),
            ),
            llm=ScholarxivAdapter(replay.http, allowed_models=[CHEAP], max_tokens=2048),
            reconciliation_llm=ScholarxivAdapter(
                replay.http, allowed_models=[QUALITY], max_tokens=8192, task="reconciliation"
            ),
        )
    )
    stages.update(
        EvidenceStages(harness.control, config, transport=replay.evidence.transport()).handlers()
    )
    return harness.worker(
        None,
        stages=stages,
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
    )


async def read(http, headers, identifier):
    response = await http.get(f"/v1/investigations/{identifier}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def until(http, headers, identifier, done, seconds=60):
    async with asyncio.timeout(seconds):
        while True:
            body = await read(http, headers, identifier)
            if done(body):
                return body
            await asyncio.sleep(0.1)


def published(body):
    # The assessment job is marked published just after its report version commits.
    job = body.get("job") or {}
    return body["processing_status"] in {"complete", "failed", "cancelled"} and job.get(
        "state"
    ) not in {"queued", "leased", "running"}


async def run_upload(harness, tmp_path, replay, media, **overrides):
    """One upload with device text through the production stage table; the final read."""
    config = configured(harness, tmp_path, **overrides)
    worker = main_flow_worker(harness, config, replay)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, media)
        await worker.start()
        try:
            transcribed = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "completed",
            )
            path = f"/v1/investigations/{identifier}/device-text"
            await send_text(http, path, headers, text_source(transcribed, media))
            return await until(http, headers, identifier, published)
        finally:
            await worker.stop()


def assert_published_report(body, replay):
    assert body["processing_status"] == "complete", body.get("error")
    report = body["report"]
    assert report["provisional"] is False and report["fixture"] is False
    [claim] = report["claims"]
    assert claim["proposition"] == "Over half of the city's buses are electric."
    [assessment] = report["assessments"]
    assert assessment["claim_id"] == claim["id"]
    assert report["evidence"]
    # Extraction (v1), reconciliation (v2), assessment (v3).
    assert report["version"] == 3
    assert body["reconciliation_progress"] == {"status": "complete", "error": None}
    assert body["job"]["stage"] == "assessment" and body["job"]["state"] == "published"
    routes = [call["model"] for call in replay.llm]
    assert routes == ["auto:cheap", "auto:quality"]
    assert replay.evidence.calls("/api/v1/papers/search")
    assert replay.evidence.calls("/api/v1/router/chat/completions")


async def test_upload_reaches_a_published_report_with_production_defaults(
    harness, tmp_path, production_defaults, replay, audiovisual
):
    config = configured(harness, tmp_path)
    assert (config.asr_enabled, config.extraction_enabled, config.reconciliation_enabled) == (
        True,
        True,
        True,
    )
    assert config.asr_configured and config.reconciliation_configured
    worker = main_flow_worker(harness, config, replay)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await worker.start()
        try:
            transcribed = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "completed",
            )
            path = f"/v1/investigations/{identifier}/device-text"
            await send_text(http, path, headers, text_source(transcribed, audiovisual))
            body = await until(http, headers, identifier, published)
        finally:
            await worker.stop()
    assert_published_report(body, replay)
    assert len(replay.asr) == 1
    assert body["speech"]["segments"][0]["text"] == "Over half of the city's buses are electric."
    assert body["analysis"]["analyzed_modalities"] == ["speech", "text"]
    observed = [
        (item["observation_id"].split(":")[1], item["status"])
        for item in body["extraction_progress"]["observations"]
    ]
    assert observed == [("speech", "processed"), ("text", "processed")]
    [extraction] = [call for call in replay.llm if call["model"] == "auto:cheap"]
    window = json.loads(extraction["messages"][1]["content"])
    assert [item["modality"] for item in window["observations"]] == ["speech", "text"]


async def test_capture_reaches_a_published_report_with_production_defaults(
    harness, tmp_path, production_defaults, replay
):
    config = configured(harness, tmp_path)
    worker = main_flow_worker(harness, config, replay)
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        headers = await guest(http)
        await worker.start()
        try:
            identifier = await start(http, headers)
            assert (await send(http, headers, identifier, 0, package(0))).status_code == 200
            await until(
                http,
                headers,
                identifier,
                lambda body: (
                    len((body.get("extraction_progress") or {}).get("observations", [])) == 2
                ),
            )
            await finish(http, headers, identifier)
            body = await until(http, headers, identifier, published)
        finally:
            await worker.stop()
    assert_published_report(body, replay)
    assert len(replay.asr) == 1
    assert [
        (item["observation_id"].rsplit(":", 1)[0], item["timebase"], item["status"])
        for item in body["extraction_progress"]["observations"]
    ] == [("capture:0:speech", "capture", "processed"), ("capture:0:text", "capture", "processed")]


async def test_defaults_without_provider_keys_fail_visibly_instead_of_skipping(
    harness, tmp_path, production_defaults, audiovisual
):
    config = harness.settings(
        storage_dir=tmp_path / "uploads",
        artifacts_dir=tmp_path / "artifacts",
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
    )
    assert config.asr_enabled and not config.asr_configured
    assert config.extraction_enabled and not config.extraction_configured
    worker = harness.worker(
        None,
        stages=default_handlers(LocalFilesystemStore(config.storage_dir), settings=config),
        database_timeout_seconds=10,
        worker_shutdown_seconds=10,
        job_lease_seconds=2,
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
    ):
        identifier, headers = await submit(http, audiovisual)
        await worker.start()
        try:
            unavailable = await until(
                http,
                headers,
                identifier,
                lambda body: (body.get("speech") or {}).get("status") == "unavailable",
                seconds=20,
            )
            path = f"/v1/investigations/{identifier}/device-text"
            await send_text(http, path, headers, text_source(unavailable, audiovisual))
            body = await until(
                http,
                headers,
                identifier,
                lambda body: any(
                    item["status"] == "failed"
                    for item in (body.get("extraction_progress") or {}).get("observations", [])
                ),
                seconds=20,
            )
        finally:
            await worker.stop()
    assert body["speech"]["reason"] == "provider_unavailable"
    speech_gaps = [gap["reason"] for gap in body["analysis"]["gaps"] if gap["modality"] == "speech"]
    assert speech_gaps == ["ASR_UNAVAILABLE"]
    [text] = body["extraction_progress"]["observations"]
    assert (text["status"], text["reason"]) == ("failed", "EXTRACTION_UNAVAILABLE")


async def test_a_correction_finishes_with_the_superseded_appearance_unassessed(
    harness, tmp_path, production_defaults, replays, audiovisual
):
    replay = replays(extraction="scholarxiv_extraction_two_claims.json", correct=True)
    body = await run_upload(harness, tmp_path, replay, audiovisual)
    assert body["processing_status"] == "complete", body.get("error")
    report = body["report"]
    assert report["provisional"] is False and report["version"] == 3
    earlier, later = sorted(
        report["claims"],
        key=lambda claim: claim["interpretation"]["source_refs"][0]["start_char"],
    )
    assert earlier["superseded_by_occurrence_id"] == later["id"]
    assert later["corrects_occurrence_id"] == earlier["id"]
    assert [item["claim_id"] for item in report["assessments"]] == [later["id"]]


async def test_claims_over_the_run_quota_stay_visible_and_the_report_finishes(
    harness, tmp_path, production_defaults, replays, audiovisual
):
    replay = replays(extraction="scholarxiv_extraction_two_claims.json")
    body = await run_upload(
        harness, tmp_path, replay, audiovisual, quotas_enabled=True, quota_claims_per_run=1
    )
    assert body["processing_status"] == "complete", body.get("error")
    report = body["report"]
    assert report["provisional"] is False and len(report["claims"]) == 2
    assert len(report["assessments"]) == 1
    assert "1 claim budget" in report["change_summary"]


async def test_a_failed_evidence_stage_fails_the_investigation_visibly(
    harness, tmp_path, production_defaults, replay, audiovisual
):
    # Scholarxiv refuses the search plan: retrieval fails terminally without retries.
    replay.evidence.papers_status = 401
    body = await run_upload(harness, tmp_path, replay, audiovisual)
    assert body["processing_status"] == "failed" and body["state"] == "failed"
    assert body["report"] is None
    assert body["error"]["code"] == "EVIDENCE_FAILED"
    assert body["job"]["stage"] == "retrieval" and body["job"]["state"] == "failed"
