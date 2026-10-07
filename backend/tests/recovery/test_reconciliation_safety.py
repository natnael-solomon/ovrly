import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from recovery.harness import ScriptedFaults
from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_incremental_budget import policy
from recovery.test_reconciliation import unchanged, wait_reconciliation
from services.jobs.faults import Checkpoint
from services.jobs.handlers import default_handlers
from services.jobs.retries import NonRetriableInput
from services.models import extraction_runs
from services.pipeline.incremental import submit_observations
from services.pipeline.llm import ScholarxivAdapter
from services.pipeline.reconciliation import request_reconciliation
from services.reports import latest_reports, publish_report_version


def handlers(http, *, recovery_attempts=0):
    return default_handlers(
        llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
        reconciliation_llm=ScholarxivAdapter(
            http,
            allowed_models=["fixture-free-model"],
            max_tokens=2048,
            task="reconciliation",
            recovery_attempts=recovery_attempts,
        ),
    )


async def admit(harness, identifier, *, capture=False):
    source = window().observations
    if capture:
        source = [item.model_copy(update={"timebase": "capture"}) for item in source]
    async with harness.control.engine.begin() as connection:
        queued = await submit_observations(
            connection,
            harness.queue,
            identifier,
            harness.owner.id,
            policy=policy(),
            observations=source,
            closed=True,
            hosted_processing_approved=True,
        )
        await request_reconciliation(connection, identifier, harness.owner.id, accepted_jobs=[])
    return queued


@pytest.mark.parametrize("action", ["cancel", "delete", "supersede"])
async def test_reconciliation_publication_is_fenced_against_cancel_delete_and_stale_reports(
    harness, action
):
    async def intervene(name, job):
        if name != Checkpoint.BEFORE_PUBLISH or job.key.stage != "reconciliation":
            return
        if action == "cancel":
            await harness.queue.request_cancel(job.id)
        elif action == "delete":
            await harness.queue.delete(job.id)
        else:
            import uuid

            identifier = uuid.UUID(job.payload["investigation_id"])
            async with harness.control.engine.begin() as connection:
                previous = (await latest_reports(connection, [identifier]))[identifier]
                await publish_report_version(
                    connection,
                    identifier,
                    lambda identity: previous.model_copy(
                        update={
                            "id": str(identity.id),
                            "version": identity.version,
                            "created_at": identity.created_at,
                            "supersedes": identity.supersedes,
                            "change_summary": "Newer owner-directed report wins.",
                        }
                    ),
                )

    def provider(request):
        body = json.loads(request.content)
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
        app = harness.app(
            None,
            stages=handlers(http),
            faults=ScriptedFaults(on_checkpoint=intervene),
            job_lease_seconds=2,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            await admit(harness, identifier)
            body = await wait_reconciliation(
                client, identifier, "failed" if action == "supersede" else "cancelled"
            )
            assert body["report"]["version"] == (2 if action == "supersede" else 1)
            assert "reconciliation" not in body["report"]
            if action == "supersede":
                assert body["report"]["change_summary"] == "Newer owner-directed report wins."
            async with harness.client(app, outsider=True) as outsider:
                assert (await outsider.get(f"/v1/investigations/{identifier}")).status_code == 404


@pytest.mark.parametrize("phase", ["open", "before_send", "during_send"])
async def test_keep_available_never_starts_or_publishes_reconciliation(harness, phase):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def intervene(name, job):
        if (
            name == Checkpoint.CLAIMED
            and job.key.stage == "reconciliation"
            and phase == "before_send"
        ):
            entered.set()
            await release.wait()

    async def provider(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        if phase == "during_send":
            entered.set()
            await release.wait()
        return httpx.Response(
            200, json=completion(unchanged(json.loads(body["messages"][1]["content"])))
        )

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(
            None,
            stages=handlers(http),
            faults=ScriptedFaults(on_checkpoint=intervene),
            job_lease_seconds=2,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client, capture=True)
            queued = await admit(harness, identifier, capture=True)
            await harness.wait_for_state(queued[0].job_id, "published")
            if phase == "open":
                await asyncio.sleep(0.1)
                assert calls == ["auto:cheap"]
                assert (
                    await client.post(
                        f"/v1/captures/{identifier}/close",
                        json={
                            "continue_research": False,
                            "duration_ms": 4000,
                        },
                    )
                ).status_code == 200
            else:
                assert (
                    await client.post(
                        f"/v1/captures/{identifier}/close",
                        json={
                            "continue_research": True,
                            "duration_ms": 4000,
                        },
                    )
                ).status_code == 200
                await asyncio.wait_for(entered.wait(), 5)
                # Stop is immutable; an explicit job cancel is the supported post-close action.
                async with harness.control.engine.connect() as connection:
                    run = await connection.scalar(
                        select(extraction_runs.c.data).where(
                            extraction_runs.c.investigation_id == identifier
                        )
                    )
                import uuid

                await harness.queue.request_cancel(uuid.UUID(run["reconciliation"]["job_id"]))
                release.set()
            body = await wait_reconciliation(client, identifier, "cancelled")
            assert body["report"]["version"] == 1
            assert len(calls) == (2 if phase == "during_send" else 1)
            capture = (await client.get(f"/v1/captures/{identifier}")).json()
            assert capture["reconciliation_progress"]["status"] == "cancelled"
            if phase == "open":
                with pytest.raises(NonRetriableInput):
                    async with harness.control.engine.begin() as connection:
                        await request_reconciliation(
                            connection, identifier, harness.owner.id, accepted_jobs=[]
                        )


@pytest.mark.parametrize(
    "failure",
    [
        "rate_limit",
        "gateway",
        "degraded",
        "repair",
        "bad_source",
        "long_summary",
        "routing_failure",
        "no_models",
        "feedback_denied",
    ],
)
async def test_quality_recovery_and_one_repair_stay_within_shared_reservations(harness, failure):
    calls = []
    quality_calls = 0

    def provider(request):
        nonlocal quality_calls
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("feedback"):
            if failure == "feedback_denied":
                return httpx.Response(403)
            return httpx.Response(204)
        if request.url.path == "/api/v1/router":
            if failure == "routing_failure":
                return httpx.Response(502)
            return httpx.Response(
                200,
                json={
                    "degraded": failure == "degraded",
                    "preset": "quality",
                    "model": "fixture-free-model",
                    "fallbacks": [] if failure == "no_models" else ["fixture-free-model"],
                },
            )
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        quality_calls += 1
        if quality_calls == 1 and failure in {
            "gateway",
            "degraded",
            "routing_failure",
            "no_models",
        }:
            return httpx.Response(502)
        if quality_calls == 1 and failure == "rate_limit":
            return httpx.Response(429, headers={"Retry-After": "0"})
        source = json.loads(body["messages"][1]["content"])
        output = unchanged(source)
        if failure in {"repair", "feedback_denied"} and quality_calls == 1:
            output["updates"] = []
        if failure == "bad_source":
            output["updates"][0]["interpretation"]["source_refs"][0]["end_char"] = 999
        if failure == "long_summary":
            output["explanation"] = "x" * 601
        response = completion(output)
        if failure == "repair" and quality_calls == 1:
            response["choices"][0]["message"]["content"] = (
                "<think>private diagnostic</think>\n```json\nnot json\n```"
            )
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http, recovery_attempts=1), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            await admit(harness, identifier)
            failed = failure in {
                "degraded",
                "bad_source",
                "long_summary",
                "routing_failure",
                "no_models",
                "feedback_denied",
            }
            body = await wait_reconciliation(client, identifier, "failed" if failed else "complete")
            assert body["report"]["version"] == (1 if failed else 2)
            assert body["extraction_progress"]["requests_used"] == len(calls)
            if failure in {"gateway", "degraded"}:
                route = next(body for path, body in calls if path == "/api/v1/router")
                assert route["preset"] == "quality"
            if failure in {"repair", "bad_source", "long_summary"}:
                assert quality_calls == 2
                assert any(path.endswith("feedback") for path, _ in calls)
            if failure == "repair":
                invalid = next(
                    item
                    for item in body["report"]["processing_attempts"]
                    if item["task"] == "reconciliation" and item["outcome"] == "invalid"
                )
                assert invalid["thinking_leaked"] is True and invalid["fenced"] is True
            assert body["report"]["provisional"] is True


@pytest.mark.parametrize(
    ("defect", "reason"),
    [
        ("missing_update", "Reconciliation must preserve every occurrence"),
        ("bad_enum", "updates.0.interpretation.taxonomy: use a schema enum value"),
    ],
)
async def test_reconciliation_repair_states_the_bounded_validation_reason(harness, defect, reason):
    prompts = []

    def provider(request):
        body = json.loads(request.content)
        if request.url.path.endswith("feedback"):
            return httpx.Response(204)
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        prompts.append(body["messages"][0]["content"])
        output = unchanged(json.loads(body["messages"][1]["content"]))
        if len(prompts) == 1 and defect == "missing_update":
            output["updates"] = []
        if len(prompts) == 1 and defect == "bad_enum":
            output["updates"][0]["interpretation"]["taxonomy"] = "secret-model-value"
        return httpx.Response(200, json=completion(output))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            await admit(harness, identifier)
            body = await wait_reconciliation(client, identifier, "complete")
            assert body["report"]["version"] == 2
    assert len(prompts) == 2
    assert f"Earlier response invalid: {reason}." in prompts[1]
    assert "secret-model-value" not in prompts[1]


@pytest.mark.parametrize("change", ["supported", "ambiguous", "conflict", "bad_context"])
async def test_whole_input_context_preserves_supported_and_uncertain_readings(harness, change):
    def provider(request):
        body = json.loads(request.content)
        if request.url.path.endswith("feedback"):
            return httpx.Response(204)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        assert source["observations"][1]["text"] == "It refers to the first observation."
        output = unchanged(source)
        interpretation = output["updates"][0]["interpretation"]
        interpretation["context_refs"] = [
            {
                "observation_id": "missing" if change == "bad_context" else "context",
                "start_char": 0,
                "end_char": 10,
            }
        ]
        if change != "supported":
            interpretation["uncertainty_flags"] = [
                "source-text-conflict" if change == "conflict" else "unresolved-reference"
            ]
        return httpx.Response(200, json=completion(output))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            source = window().observations
            source.append(
                source[0].model_copy(
                    update={
                        "id": "context",
                        "text": "It refers to the first observation.",
                        "role": "context",
                        "start_ms": 5000,
                        "end_ms": 6000,
                    }
                )
            )
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(batch_observations=2),
                    observations=source,
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            body = await wait_reconciliation(
                client, identifier, "failed" if change == "bad_context" else "complete"
            )
            if change != "bad_context":
                claim = body["report"]["claims"][0]
                assert claim["interpretation"]["context_refs"][0]["observation_id"] == "context"
                assert bool(claim["interpretation"]["uncertainty_flags"]) is (change != "supported")
                assert body["report"]["reconciliation"]["reassessment_claim_ids"] == [claim["id"]]


async def test_large_whole_context_fails_explicitly_instead_of_truncating(harness):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            source = window().observations
            source += [
                source[0].model_copy(
                    update={
                        "id": f"context-{index}",
                        "role": "context",
                        "text": "x" * 8000,
                        "start_ms": 5000 + index * 1000,
                        "end_ms": 6000 + index * 1000,
                    }
                )
                for index in range(4)
            ]
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_tokens=30000, reconciliation_tokens=20000),
                    observations=source,
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            body = await wait_reconciliation(client, identifier, "failed")
            assert len(calls) == 1
            assert body["reconciliation_progress"]["error"]["code"] == "EXTRACTION_BUDGET_EXHAUSTED"
            assert body["report"]["version"] == 1


async def test_reconciliation_keeps_later_repetition_as_a_distinct_occurrence(harness):
    def provider(request):
        body = json.loads(request.content)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            template = extraction()["occurrences"][0]
            result = {
                "occurrences": [
                    {
                        **template,
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
        else:
            result = unchanged(source)
        return httpx.Response(200, json=completion(result))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            source = window().observations
            source.append(
                source[0].model_copy(update={"id": "later", "start_ms": 6000, "end_ms": 7000})
            )
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(batch_observations=2),
                    observations=source,
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            body = await wait_reconciliation(client, identifier, "complete")
            first, later = body["report"]["claims"]
            assert first["id"] != later["id"]
            assert first["proposition"] == later["proposition"]
            assert "corrects_occurrence_id" not in later
            assert body["report"]["reconciliation"]["reassessment_claim_ids"] == []
