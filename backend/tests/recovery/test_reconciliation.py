import asyncio
import json

import httpx
import pytest

from recovery.harness import ScriptedFaults
from recovery.test_claim_extraction import (
    completion,
    create_investigation,
    extraction,
    validate,
    window,
)
from recovery.test_incremental_budget import policy
from services.api.main import create_app
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.jobs.queue import StageKey
from services.pipeline.incremental import submit_observations
from services.pipeline.llm import ScholarxivAdapter
from services.pipeline.stub_reports import stub_report
from services.reports import latest_reports, publish_report_version


async def test_settled_input_reconciles_without_claiming_assessment_completion(harness):
    from services.pipeline.reconciliation import request_reconciliation

    routes = []

    def provider(request):
        body = json.loads(request.content)
        routes.append(body["model"])
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        source = json.loads(body["messages"][1]["content"])
        claim = source["claims"][0]
        return httpx.Response(
            200,
            json=completion(
                {
                    "updates": [
                        {
                            "claim_id": claim["id"],
                            "proposition": claim["proposition"],
                            "interpretation": claim["interpretation"],
                            "corrects": None,
                        }
                    ],
                    "explanation": "Available context preserves the original meaning.",
                }
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            job_lease_seconds=2,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
                reconciliation_llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=2048,
                    task="reconciliation",
                ),
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )

            async def reconciled():
                response = (await client.get(f"/v1/investigations/{identifier}")).json()
                return (
                    response.get("report", {})
                    and response["report"].get("reconciliation", {}).get("status") == "complete"
                )

            async with asyncio.timeout(10):
                while True:
                    if await reconciled():
                        break
                    await asyncio.sleep(0.02)
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert routes == ["auto:cheap", "auto:quality"]
            assert body["processing_status"] == "partial"
            assert body["report"]["provisional"] is True
            assert body["report"]["version"] == 2
            assert body["report"]["assessments"] == []
            assert body["report"]["reconciliation"]["reassessment_claim_ids"] == []
            assert body["report"]["reconciliation"]["coverage_limited"] is False
            assert body["extraction_progress"]["requests_used"] == 2


async def test_correction_links_preserve_appearances_and_remove_only_stale_verdicts(harness):
    from services.pipeline.reconciliation import request_reconciliation

    async with httpx.AsyncClient(
        base_url="https://router.example",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(correction_response(request)))
        ),
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
            reconciliation_llm=ScholarxivAdapter(
                http, allowed_models=["fixture-free-model"], max_tokens=2048, task="reconciliation"
            ),
        )
        app = harness.app(None, stages=handlers, job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            source = [
                window()
                .observations[0]
                .model_copy(
                    update={
                        "id": f"speech-{index}",
                        "text": text,
                        "start_ms": index * 1000,
                        "end_ms": index * 1000 + 900,
                    }
                )
                for index, text in enumerate(
                    [
                        "The rate is twenty percent.",
                        "Correction: the rate is two percent.",
                        "Water freezes at zero degrees Celsius.",
                    ]
                )
            ]
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(batch_observations=3),
                    observations=source,
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published")
            async with harness.control.engine.begin() as connection:
                original = (await latest_reports(connection, [identifier]))[identifier]

                def assessed(identity):
                    template = stub_report(identity)
                    evidence = [
                        template.evidence[0].model_copy(
                            update={
                                "id": f"synthetic-evidence-{index}",
                                "claim_id": claim.id,
                            }
                        )
                        for index, claim in enumerate(original.claims)
                    ]
                    assessments = [
                        template.assessments[0].model_copy(
                            update={
                                "id": f"synthetic-assessment-{index}",
                                "claim_id": claim.id,
                                "relations": [
                                    template.assessments[0]
                                    .relations[0]
                                    .model_copy(update={"evidence_id": evidence[index].id})
                                ],
                            }
                        )
                        for index, claim in enumerate(original.claims)
                    ]
                    return original.model_copy(
                        update={
                            "id": str(identity.id),
                            "version": identity.version,
                            "created_at": identity.created_at,
                            "supersedes": identity.supersedes,
                            "evidence": evidence,
                            "assessments": assessments,
                        }
                    )

                prior = await publish_report_version(connection, identifier, assessed)
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            async with asyncio.timeout(10):
                while True:
                    body = (await client.get(f"/v1/investigations/{identifier}")).json()
                    if body.get("report") and body["report"]["version"] == 3:
                        break
                    await asyncio.sleep(0.02)
            report = body["report"]
            first, correction, unchanged = report["claims"]
            assert [claim["original_text"] for claim in report["claims"]] == [
                item.text for item in source
            ]
            assert correction["corrects_occurrence_id"] == first["occurrence_id"]
            assert first["superseded_by_occurrence_id"] == correction["occurrence_id"]
            assert first["proposition"] == "The rate is twenty percent."
            assert correction["proposition"] == "The rate is two percent."
            assert report["reconciliation"]["reassessment_claim_ids"] == [correction["id"]]
            assert [value["claim_id"] for value in report["assessments"]] == [unchanged["id"]]
            assert report["assessments"][0]["version"] == 3
            assert [value["claim_id"] for value in report["evidence"]] == [unchanged["id"]]
            assert (
                await client.get(f"/v1/investigations/{identifier}/reports/2")
            ).json() == prior.model_dump(mode="json")
            assert "twenty" in report["change_summary"] and "two" in report["change_summary"]


def correction_response(request):
    body = json.loads(request.content)
    source = json.loads(body["messages"][1]["content"])
    if body["model"] == "auto:cheap":
        template = extraction()["occurrences"][0]
        return {
            "occurrences": [
                {
                    **template,
                    "proposition": item["text"],
                    "source_refs": [
                        {
                            "observation_id": item["id"],
                            "start_char": 0,
                            "end_char": len(item["text"]),
                        }
                    ],
                }
                for item in source["observations"]
            ]
        }
    return {
        "updates": [
            {
                "claim_id": claim["id"],
                "proposition": "The rate is two percent." if index == 1 else claim["proposition"],
                "interpretation": claim["interpretation"],
                "corrects": source["claims"][0]["id"] if index == 1 else None,
            }
            for index, claim in enumerate(source["claims"])
        ],
        "explanation": (
            "The later two-percent correction supersedes the earlier twenty-percent interpretation."
        ),
    }


async def wait_reconciliation(client, identifier, status):
    async with asyncio.timeout(10):
        while True:
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            if body.get("reconciliation_progress", {}).get("status") == status:
                validate.Validator().validate(body, "investigation.schema.json")
                return body
            await asyncio.sleep(0.02)


def unchanged(source):
    return {
        "updates": [
            {
                "claim_id": claim["id"],
                "proposition": claim["proposition"],
                "interpretation": claim["interpretation"],
                "corrects": None,
            }
            for claim in source["claims"]
        ],
        "explanation": "Available context preserves the original meaning.",
    }


@pytest.mark.parametrize("outcome", ["quality_failure", "invalid", "budget", "unknown"])
async def test_quality_failure_retains_provisional_report_with_explicit_reason(harness, outcome):
    from services.pipeline.reconciliation import request_reconciliation

    calls = []

    def provider(request):
        calls.append(request)
        body = json.loads(request.content)
        if body.get("model") == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        if request.url.path.endswith("feedback"):
            return httpx.Response(204)
        if outcome == "quality_failure":
            return httpx.Response(502)
        if outcome == "unknown":
            raise httpx.ReadTimeout("unknown outcome")
        return httpx.Response(200, json=completion({"updates": [], "explanation": "Lost claims"}))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(
            None,
            job_lease_seconds=2,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
                reconciliation_llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=8192,
                    task="reconciliation",
                ),
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_tokens=9000, reconciliation_tokens=3000)
                    if outcome == "budget"
                    else policy(),
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            body = await wait_reconciliation(client, identifier, "failed")
            assert body["report"]["version"] == 1
            assert body["report"]["provisional"] is True
            assert body["processing_status"] == "partial"
            assert body["error"] is None
            assert (
                body["reconciliation_progress"]["error"]["code"]
                == {
                    "quality_failure": "EXTRACTION_UNAVAILABLE",
                    "invalid": "EXTRACTION_INVALID",
                    "budget": "EXTRACTION_BUDGET_EXHAUSTED",
                    "unknown": "EXTRACTION_OUTCOME_UNKNOWN",
                }[outcome]
            )
            assert all(
                json.loads(call.content).get("model") != "openai/gpt-oss-20b" for call in calls
            )
            if outcome == "budget":
                assert len(calls) == 1


@pytest.mark.parametrize("terminal", ["published", "failed", "cancelled"])
async def test_reconciliation_waits_for_sealed_input_and_accepted_upstream(harness, terminal):
    from services.pipeline.reconciliation import request_reconciliation

    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        source = json.loads(body["messages"][1]["content"])
        return httpx.Response(
            200,
            json=completion(extraction() if body["model"] == "auto:cheap" else unchanged(source)),
        )

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(
            None,
            job_lease_seconds=2,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
                reconciliation_llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=2048,
                    task="reconciliation",
                ),
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                upstream = await harness.queue.enqueue(
                    connection,
                    StageKey(1, "fixture_upstream", str(identifier)),
                    {"investigation_id": str(identifier)},
                    owner_id=harness.owner.id,
                )
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=window().observations,
                    closed=False,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[upstream.job_id]
                )
            await harness.wait_for_state(queued[0].job_id, "published")
            assert (await wait_reconciliation(client, identifier, "waiting"))["report"][
                "version"
            ] == 1
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=[],
                    closed=True,
                    hosted_processing_approved=True,
                )
            await asyncio.sleep(0.1)
            assert calls == ["auto:cheap"]
            if terminal == "cancelled":
                await harness.queue.request_cancel(upstream.job_id)
            else:
                from sqlalchemy import update

                from services.jobs.models import jobs

                async with harness.control.engine.begin() as connection:
                    await connection.execute(
                        update(jobs).where(jobs.c.id == upstream.job_id).values(state=terminal)
                    )
            body = await wait_reconciliation(client, identifier, "complete")
            assert body["report"]["reconciliation"]["coverage_limited"] is (terminal != "published")
            assert calls == ["auto:cheap", "auto:quality"]
            async with harness.control.engine.begin() as connection:
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[upstream.job_id]
                )
            await asyncio.sleep(0.05)
            assert len(calls) == 2


@pytest.mark.parametrize(
    "checkpoint", [Checkpoint.AFTER_PROVIDER_CALL, Checkpoint.AFTER_ARTIFACT_STORE]
)
async def test_reconciliation_restart_does_not_repeat_or_lose_the_durable_artifact(
    harness, checkpoint
):
    from services.pipeline.reconciliation import request_reconciliation

    calls = []

    def provider(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        return httpx.Response(
            200,
            json=completion(
                extraction()
                if body["model"] == "auto:cheap"
                else unchanged(json.loads(body["messages"][1]["content"]))
            ),
        )

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
            reconciliation_llm=ScholarxivAdapter(
                http, allowed_models=["fixture-free-model"], max_tokens=2048, task="reconciliation"
            ),
        )
        app = create_app(harness.settings())
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
            first = harness.worker(None, stages=handlers)
            await first.start()
            await harness.wait_for_state(queued[0].job_id, "published")
            await first.stop()
            async with harness.control.engine.begin() as connection:
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            crashing = harness.worker(
                None, stages=handlers, faults=ScriptedFaults(crash_at=checkpoint)
            )
            await crashing.start()
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(crashing.wait(), 5)
            restored = harness.worker(None, stages=handlers)
            await restored.start()
            body = await wait_reconciliation(
                client,
                identifier,
                "complete" if checkpoint == Checkpoint.AFTER_ARTIFACT_STORE else "failed",
            )
            assert calls == ["auto:cheap", "auto:quality"]
            assert body["report"]["version"] == (
                2 if checkpoint == Checkpoint.AFTER_ARTIFACT_STORE else 1
            )
            assert body["extraction_progress"]["requests_used"] == 2
