"""Evidence stages against PostgreSQL (BE-09, #27): the shared token bucket, retrieval and
assessment publishing a citation-checked version, supersession, failures, reanalysis
(correction, deeper, expansion) and stage registration. Providers are synthetic cassettes."""

import asyncio
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from evidence_cassettes import KEY, Providers
from sqlalchemy import func, select, update
from test_evidence_units import claims_only
from test_intake_api import CONTRACT_VALIDATOR, guest
from test_reports_api import create_investigation

from services.api.main import create_app
from services.api.schemas import ReportVersion
from services.evidence.assessment import validate_citations
from services.evidence.stages import (
    ASSESSMENT_STAGE,
    RETRIEVAL_STAGE,
    EvidenceStages,
    enqueue_retrieval,
)
from services.jobs.handlers import JobContext, default_handlers
from services.jobs.models import job_results, jobs
from services.jobs.queue import JobQueue, PublishRejected
from services.jobs.retries import NonRetriableInput, RateLimited, Transient
from services.models import investigations, provider_buckets, reanalysis_requests
from services.pipeline.publish import publish_stage
from services.pipeline.registry import worker_handlers
from services.providers.budget import TokenBucket
from services.reports import REANALYSIS_STAGE, NextVersion, publish_report_version
from services.settings import Settings

REPORT_SCHEMA = "report-version.schema.json"


def settings_for(database_url, tmp_path, **extra):
    return Settings(
        database_url=database_url,
        storage_dir=tmp_path / "uploads",
        scholarxiv_api_key=KEY,
        _env_file=None,
        **extra,
    )


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(settings_for(database_url, tmp_path))
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture
async def providers(app):
    # Each test gets a fresh Scholarxiv bucket so the shared limit never leaks between tests.
    async with app.state.database.engine.begin() as connection:
        await connection.execute(provider_buckets.delete())
    return Providers()


def stages(app, providers, **extra):
    settings = app.state.settings.model_copy(update=extra) if extra else app.state.settings
    return EvidenceStages(app.state.database, settings, transport=providers.transport())


async def owner_of(app, investigation_id):
    async with app.state.database.engine.connect() as connection:
        return await connection.scalar(
            select(investigations.c.owner_id).where(
                investigations.c.id == uuid.UUID(investigation_id)
            )
        )


async def publish_claims(app, investigation_id, fixture=False):
    """What the claim extraction stage (#25) will do: a version with claims only."""

    def build(identity: NextVersion) -> ReportVersion:
        return claims_only().model_copy(
            update={
                "id": str(identity.id),
                "investigation_id": identity.investigation_id,
                "version": identity.version,
                "created_at": identity.created_at,
                "supersedes": identity.supersedes,
                "change_summary": "Claims extracted; evidence not checked yet.",
            }
        )

    async with app.state.database.engine.begin() as connection:
        return await publish_report_version(
            connection, uuid.UUID(investigation_id), build, fixture=fixture
        )


async def queue_retrieval(app, investigation_id, version, **extra):
    owner = await owner_of(app, investigation_id)
    async with app.state.database.engine.begin() as connection:
        return await enqueue_retrieval(
            connection,
            JobQueue(app.state.database),
            investigation_id=uuid.UUID(investigation_id),
            owner_id=owner,
            base_version=version,
            **extra,
        )


async def run(app, handler, stage, investigation_id, *, publish=True, on_error="release"):
    """Claim and run the queued ``stage`` job of one investigation only.

    The test database is shared by the session, so other queued jobs of the same stage
    (leftovers of other tests) are pushed into the future first; they are never run here.
    """
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(
                jobs.c.stage == stage,
                jobs.c.state == "queued",
                jobs.c.payload["investigation_id"].astext != str(investigation_id),
            )
            .values(available_at=func.now() + timedelta(days=1))
        )
    queue = JobQueue(app.state.database)
    claim = await queue.claim("evidence-test", [stage], 30)
    assert claim is not None, f"no {stage} job queued"
    assert claim.payload["investigation_id"] == str(investigation_id)
    await queue.start(claim.lease)
    try:
        result = await handler(claim, JobContext(queue, claim.lease, 30))
    except BaseException:
        if on_error == "fail":
            await queue.fail(claim.lease, "test")
        else:
            await queue.release(claim.lease)
        raise
    if publish:
        # The worker's publish path: fenced, with successors in the same transaction.
        await publish_stage(queue, claim, result)
    return claim, result


async def queued_assessments(app, investigation_id):
    async with app.state.database.engine.connect() as connection:
        return await connection.scalar(
            select(func.count())
            .select_from(jobs)
            .where(
                jobs.c.stage == ASSESSMENT_STAGE,
                jobs.c.state == "queued",
                jobs.c.payload["investigation_id"].astext == investigation_id,
            )
        )


async def read(client, headers, investigation_id, version):
    response = await client.get(
        f"/v1/investigations/{investigation_id}/reports/{version}", headers=headers
    )
    assert response.status_code == 200, response.text
    CONTRACT_VALIDATOR.validate(response.json(), REPORT_SCHEMA)
    return response.json()


async def pipeline(app, providers, investigation_id, version, **extra):
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, version, **extra)
    await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    return await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)


async def test_token_bucket_takes_refills_and_blocks(app):
    bucket = TokenBucket(app.state.database, f"test-{uuid.uuid4().hex[:8]}", 2)
    await bucket.acquire()
    await bucket.acquire()
    with pytest.raises(RateLimited) as limited:
        await bucket.acquire()
    assert 1700 < limited.value.retry_after_seconds <= 1800
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(provider_buckets)
            .where(provider_buckets.c.name == bucket.name)
            .values(updated_at=provider_buckets.c.updated_at - timedelta(hours=1))
        )
    await bucket.acquire()
    await bucket.block(60)
    with pytest.raises(RateLimited):
        await bucket.acquire()
    # A short wait is waited out instead of failing the stage: 10 tokens per second, held
    # for 0.2 s by a Retry-After, so the next token comes about 0.3 s later.
    fast = TokenBucket(app.state.database, f"test-{uuid.uuid4().hex[:8]}", 36000)
    await fast.acquire()
    await fast.block(0.2)
    started = asyncio.get_running_loop().time()
    await fast.acquire()
    assert asyncio.get_running_loop().time() - started >= 0.25
    # Concurrent workers share one bucket: exactly the capacity is granted.
    shared = TokenBucket(app.state.database, f"test-{uuid.uuid4().hex[:8]}", 5)
    outcomes = await asyncio.gather(*(shared.acquire() for _ in range(8)), return_exceptions=True)
    assert sum(1 for o in outcomes if o is None) == 5
    assert all(isinstance(o, RateLimited) for o in outcomes if o is not None)


async def test_retrieval_then_assessment_publishes_a_checked_version(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "evidence")
    base = await publish_claims(app, investigation_id)
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, base.version)
    retrieval, artifact = await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    assert [c["status"] for c in artifact["claims"]] == ["retrieved", "retrieved"]
    assert KEY not in json.dumps(artifact)
    async with app.state.database.engine.connect() as connection:
        queued = (
            await connection.execute(select(jobs).where(jobs.c.stage == ASSESSMENT_STAGE))
        ).all()
    assessment_job = next(j for j in queued if j.payload["retrieval_job_id"] == str(retrieval.id))
    assert assessment_job.owner_id == await owner_of(app, investigation_id)
    _, result = await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    assert result == {"version": base.version + 1, "assessed": 2}
    published = await read(client, headers, investigation_id, base.version + 1)
    report = ReportVersion.model_validate(published)
    validate_citations(report)
    assert report.supersedes == base.id and not report.provisional and not report.fixture
    assert len(report.assessments) == 2 and report.evidence
    assert report.change_summary.startswith("Evidence check: 2 of 2 claims")
    assert {a.overall for a in report.assessments} <= {
        "supported",
        "challenged",
        "qualified",
        "mixed",
        "insufficient_evidence",
    }
    body = (await client.get(f"/v1/investigations/{investigation_id}", headers=headers)).json()
    assert body["report"]["version"] == base.version + 1
    assert body["processing_status"] in {"complete", "waiting", "checking", "partial"}
    # Retrieval is not redone by assessment: provider calls only came from retrieval and
    # the relation step.
    assert len(providers.calls("/papers/search")) == 4
    export = await client.get(
        f"/v1/investigations/{investigation_id}/reports/{base.version + 1}/export",
        headers=headers,
    )
    assert export.status_code == 200 and export.json()["sources"]


async def test_superseded_and_unpublished_retrievals_publish_nothing(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "superseded")
    base = await publish_claims(app, investigation_id)
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, base.version)
    retrieval, result = await run(
        app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id, publish=False
    )
    # Assessment is only enqueued when the retrieval result is published.
    assert await queued_assessments(app, investigation_id) == 0
    await publish_stage(JobQueue(app.state.database), retrieval, result)
    assert await queued_assessments(app, investigation_id) == 1
    await publish_claims(app, investigation_id)  # a newer version, e.g. after a correction
    _, skipped = await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    assert skipped == {"skipped": "superseded"}
    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert [item["version"] for item in listing.json()["items"]] == [1, 2]
    # A retrieval for a version that is no longer the latest stores a superseded artifact.
    await queue_retrieval(app, investigation_id, base.version, depth="deeper")
    _, stale = await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    assert stale["superseded"] is True and stale["claims"] == []
    # A scope naming no claim of the latest version stops without queueing assessment.
    await queue_retrieval(app, investigation_id, 2, claim_ids=["clm_not_there"])
    _, empty = await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    assert empty["claims"] == [] and empty["superseded"] is False
    assert await queued_assessments(app, investigation_id) == 0


async def test_cancelled_retrieval_queues_no_assessment(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "cancel")
    base = await publish_claims(app, investigation_id)
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, base.version)
    retrieval, result = await run(
        app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id, publish=False
    )
    queue = JobQueue(app.state.database)
    await queue.request_cancel(retrieval.id)
    # The fenced publish is refused, and the assessment enqueue rolls back with it.
    with pytest.raises(PublishRejected):
        await publish_stage(queue, retrieval, result)
    assert await queued_assessments(app, investigation_id) == 0
    # An assessment whose retrieval was cancelled after it was queued skips.
    other = await create_investigation(client, headers, "cancel-late")
    version = await publish_claims(app, other)
    await queue_retrieval(app, other, version.version)
    late, _ = await run(app, evidence.retrieval, RETRIEVAL_STAGE, other)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(update(jobs).where(jobs.c.id == late.id).values(state="cancelled"))
    _, skipped = await run(app, evidence.assessment, ASSESSMENT_STAGE, other)
    assert skipped == {"skipped": "retrieval_unavailable"}


async def test_retrieval_failures_are_typed(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "failures")
    base = await publish_claims(app, investigation_id)
    providers.papers_failures = 999
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, base.version)
    with pytest.raises(Transient):
        await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    providers.papers_failures = 0
    providers.papers_status = 401
    with pytest.raises(NonRetriableInput):
        await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    providers.papers_status = 429
    with pytest.raises(RateLimited):
        await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id, on_error="fail")
    # A job naming another owner's investigation never reaches a provider.
    other = await create_investigation(client, await guest(client), "foreign")
    owner = await owner_of(app, investigation_id)
    async with app.state.database.engine.begin() as connection:
        await enqueue_retrieval(
            connection,
            JobQueue(app.state.database),
            investigation_id=uuid.UUID(other),
            owner_id=owner,
            base_version=1,
        )
    before = len(providers.requests)
    with pytest.raises(NonRetriableInput):
        await run(app, evidence.retrieval, RETRIEVAL_STAGE, other, on_error="fail")
    no_report = await create_investigation(client, headers, "no-report")
    await queue_retrieval(app, no_report, 1)
    with pytest.raises(NonRetriableInput):
        await run(app, evidence.retrieval, RETRIEVAL_STAGE, no_report, on_error="fail")
    assert len(providers.requests) == before


async def test_claim_budget_keeps_extra_claims_visible_and_provisional(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "budget")
    base = await publish_claims(app, investigation_id)
    evidence = stages(app, providers, evidence_max_claims=1)
    await queue_retrieval(app, investigation_id, base.version)
    await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    report = await read(client, headers, investigation_id, base.version + 1)
    assert report["provisional"] is True and len(report["claims"]) == 2
    assert len(report["assessments"]) == 1
    assert "1 claim budget" in report["change_summary"]
    body = (await client.get(f"/v1/investigations/{investigation_id}", headers=headers)).json()
    assert body["processing_status"] == "partial"


async def test_reanalysis_correction_and_deeper(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "reanalysis")
    base = await publish_claims(app, investigation_id)
    await pipeline(app, providers, investigation_id, base.version)
    claim = base.claims[0]
    receipt = await client.post(
        f"/v1/investigations/{investigation_id}/reanalyze",
        json={
            "reason": "correction",
            "base_version": base.version + 1,
            "claim_id": claim.id,
            "proposition": "Over half of the city's buses are electric.",
        },
        headers={**headers, "Idempotency-Key": "fix"},
    )
    assert receipt.status_code == 202, receipt.text
    corrected = receipt.json()["published_version"]
    evidence = stages(app, providers)
    _, queued = await run(app, evidence.reanalysis, REANALYSIS_STAGE, investigation_id)
    async with app.state.database.engine.connect() as connection:
        payload = await connection.scalar(
            select(jobs.c.payload).where(jobs.c.id == uuid.UUID(queued["retrieval_job_id"]))
        )
    assert payload["claim_ids"] == [claim.id] and payload["base_version"] == corrected
    await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    _, result = await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    report = await read(client, headers, investigation_id, result["version"])
    assert report["change_summary"].startswith("Evidence check: 1 of 1 claims")
    assert len(report["assessments"]) == 2 and not report["provisional"]
    async with app.state.database.engine.connect() as connection:
        stored = await connection.scalar(
            select(reanalysis_requests.c.result_version).where(
                reanalysis_requests.c.id == uuid.UUID(receipt.json()["id"])
            )
        )
    assert stored == result["version"]
    deeper = await client.post(
        f"/v1/investigations/{investigation_id}/reanalyze",
        json={"reason": "deeper", "base_version": result["version"]},
        headers={**headers, "Idempotency-Key": "deeper"},
    )
    assert deeper.status_code == 202, deeper.text
    _, queued = await run(app, evidence.reanalysis, REANALYSIS_STAGE, investigation_id)
    async with app.state.database.engine.connect() as connection:
        payload = await connection.scalar(
            select(jobs.c.payload).where(jobs.c.id == uuid.UUID(queued["retrieval_job_id"]))
        )
    assert payload["claim_ids"] is None and payload["depth"] == "deeper"
    await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    _, deeper_result = await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    deeper_report = await read(client, headers, investigation_id, deeper_result["version"])
    assert "(deeper search)" in deeper_report["change_summary"]


async def test_reanalysis_expansion_merges_the_full_video(client, app, providers):
    headers = await guest(client)
    clip = await create_investigation(client, headers, "clip")
    full = await create_investigation(client, headers, "full")
    clip_base = await publish_claims(app, clip)
    expand = {
        "reason": "expansion",
        "base_version": clip_base.version,
        "match_confirmed": True,
        "source_investigation_id": full,
    }
    first = await client.post(
        f"/v1/investigations/{clip}/reanalyze",
        json=expand,
        headers={**headers, "Idempotency-Key": "x1"},
    )
    assert first.status_code == 202, first.text
    evidence = stages(app, providers)
    request_id = uuid.UUID(first.json()["id"])
    # The full video has no report yet: the job publishes "waiting" and schedules a later
    # check, which the request now points at so it stays visible and cancellable.
    _, waiting = await run(app, evidence.reanalysis, REANALYSIS_STAGE, clip)
    assert waiting == {"waiting_for_source": True, "poll": 0}
    async with app.state.database.engine.connect() as connection:
        follow_up = (
            await connection.execute(
                select(jobs, (jobs.c.available_at - func.now()).label("delay"))
                .join(reanalysis_requests, reanalysis_requests.c.job_id == jobs.c.id)
                .where(reanalysis_requests.c.id == request_id)
            )
        ).one()
    assert follow_up.state == "queued" and follow_up.payload["poll"] == 1
    assert timedelta(seconds=25) < follow_up.delay <= timedelta(seconds=30)
    # A full video that never publishes ends the request instead of polling forever.
    with pytest.raises(NonRetriableInput):
        await evidence.reanalysis(SimpleNamespace(payload={**follow_up.payload, "poll": 12}), None)
    full_base = await publish_claims(app, full)
    await pipeline(app, providers, full, full_base.version)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs).where(jobs.c.id == follow_up.id).values(available_at=func.now())
        )
    _, result = await run(app, evidence.reanalysis, REANALYSIS_STAGE, clip)
    report = await read(client, headers, clip, result["version"])
    prefix = f"fv{full_base.version + 1}_"
    assert len(report["claims"]) == 4
    assert [c["id"].startswith(prefix) for c in report["claims"]] == [False, False, True, True]
    assert full in report["change_summary"]
    async with app.state.database.engine.connect() as connection:
        stored = await connection.scalar(
            select(reanalysis_requests.c.result_version).where(
                reanalysis_requests.c.id == uuid.UUID(first.json()["id"])
            )
        )
    assert stored == result["version"]


async def test_stages_are_registered_only_with_the_key(app, database_url, tmp_path):
    database = app.state.database
    base = default_handlers(app.state.upload_store)
    with_key = worker_handlers(database, settings_for(database_url, tmp_path), base)
    assert {RETRIEVAL_STAGE, ASSESSMENT_STAGE, REANALYSIS_STAGE} <= set(with_key)
    plain = Settings(database_url=database_url, _env_file=None)
    without = worker_handlers(database, plain, base)
    assert not {RETRIEVAL_STAGE, ASSESSMENT_STAGE, REANALYSIS_STAGE} & set(without)
    # A test-supplied table (create_app(handlers=...)) never gains the evidence stages.
    explicit = worker_handlers(database, settings_for(database_url, tmp_path), {}, evidence=False)
    assert explicit == {}
    stub = worker_handlers(database, settings_for(database_url, tmp_path, stub_reports=True), base)
    # The development stub wins for reanalysis when both are configured.
    assert stub[REANALYSIS_STAGE].__name__ == "stub_reanalysis"
    assert stub[RETRIEVAL_STAGE].__name__ == "retrieval"
    keyless = EvidenceStages(database, plain, transport=Providers().transport())
    async with httpx.AsyncClient() as unused:
        with pytest.raises(NonRetriableInput):
            keyless._clients(unused)


async def test_artifact_is_stored_only_as_the_job_result(client, app, providers):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers, "artifact")
    base = await publish_claims(app, investigation_id)
    evidence = stages(app, providers)
    await queue_retrieval(app, investigation_id, base.version)
    retrieval, _ = await run(app, evidence.retrieval, RETRIEVAL_STAGE, investigation_id)
    async with app.state.database.engine.connect() as connection:
        stored = await connection.scalar(
            select(job_results.c.result).where(job_results.c.job_id == retrieval.id)
        )
    assert stored["base_version"] == base.version and len(stored["claims"]) == 2
    # Deleting the retrieval job removes its artifact, and assessment then skips.
    await JobQueue(app.state.database).delete(retrieval.id)
    _, result = await run(app, evidence.assessment, ASSESSMENT_STAGE, investigation_id)
    assert result == {"skipped": "retrieval_unavailable"}
