import asyncio
import json

import httpx
import pytest

from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_incremental_budget import policy
from recovery.test_reconciliation import unchanged, wait_reconciliation
from recovery.test_reconciliation_safety import handlers
from services.jobs.handlers import StageResult
from services.jobs.queue import StageKey
from services.pipeline.incremental import submit_observations
from services.pipeline.reconciliation import request_reconciliation


@pytest.mark.parametrize("reverse", [False, True])
async def test_correction_order_uses_exact_spans_inside_one_observation(harness, reverse):
    def provider(request):
        if request.url.path.endswith("feedback"):
            return httpx.Response(204)
        body = json.loads(request.content)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            template = extraction()["occurrences"][0]
            result = {
                "occurrences": [
                    {
                        **template,
                        "proposition": proposition,
                        "source_refs": [
                            {
                                "observation_id": "speech-1",
                                "start_char": start,
                                "end_char": end,
                            }
                        ],
                    }
                    for proposition, start, end in [
                        ("The rate is twenty percent.", 0, 15),
                        ("The rate is two percent.", 16, 40),
                    ]
                ]
            }
        else:
            source["claims"].sort(
                key=lambda claim: claim["interpretation"]["source_refs"][0]["start_char"]
            )
            result = unchanged(source)
            target, correction = (1, 0) if reverse else (0, 1)
            result["updates"][correction]["corrects"] = source["claims"][target]["id"]
        return httpx.Response(200, json=completion(result))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            observation = (
                window()
                .observations[0]
                .model_copy(
                    update={
                        "text": "Twenty percent. Correction: two percent. ",
                        "id": "speech-1",
                    }
                )
            )
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=[observation],
                    closed=True,
                    hosted_processing_approved=True,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            body = await wait_reconciliation(
                client, identifier, "failed" if reverse else "complete"
            )
            first, second = sorted(
                body["report"]["claims"],
                key=lambda claim: claim["interpretation"]["source_refs"][0]["start_char"],
            )
            assert first["interval"] == second["interval"]
            assert first["original_text"] == observation.text[:15]
            assert second["original_text"] == observation.text[16:40]
            if not reverse:
                assert first["superseded_by_occurrence_id"] == second["occurrence_id"]
                assert second["corrects_occurrence_id"] == first["occurrence_id"]
                # A link alone leaves the correcting proposition's meaning unchanged.
                assert second["correction"] is None
                assert body["report"]["reconciliation"]["reassessment_claim_ids"] == []
            else:
                assert "superseded_by_occurrence_id" not in first


async def test_late_upstream_publication_adds_observations_before_quality_reconciliation(harness):
    release = asyncio.Event()
    entered = asyncio.Event()
    quality_sources = []
    identifier = None

    async def upstream(job, context):
        entered.set()
        await release.wait()

        async def publish(connection):
            late = (
                window()
                .observations[0]
                .model_copy(
                    update={
                        "id": "late-context",
                        "text": "The pronoun refers to the original speaker.",
                        "role": "target",
                        "start_ms": 5000,
                        "end_ms": 6000,
                    }
                )
            )
            await submit_observations(
                connection,
                harness.queue,
                identifier,
                harness.owner.id,
                policy=policy(),
                observations=[late],
                closed=True,
                hosted_processing_approved=True,
            )

        return StageResult({"synthetic": True}, publish)

    def provider(request):
        body = json.loads(request.content)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            return httpx.Response(200, json=completion(extraction()))
        quality_sources.append(source)
        result = unchanged(source)
        result["updates"][0]["interpretation"]["context_refs"] = [
            {
                "observation_id": "late-context",
                "start_char": 0,
                "end_char": 11,
            }
        ]
        result["updates"][0]["proposition"] = "The original speaker's rate is twenty percent."
        result["explanation"] = "Late accepted context resolves the attribution."
        return httpx.Response(200, json=completion(result))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        stages = {**handlers(http), "fixture_upstream": upstream}
        app = harness.app(None, stages=stages, job_lease_seconds=2)
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
                    closed=False,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published")
            async with harness.control.engine.begin() as connection:
                accepted = await harness.queue.enqueue(
                    connection,
                    StageKey(1, "fixture_upstream", str(identifier)),
                    {"investigation_id": str(identifier)},
                    owner_id=harness.owner.id,
                )
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[accepted.job_id]
                )
            await asyncio.wait_for(entered.wait(), 5)
            waiting = await wait_reconciliation(client, identifier, "waiting")
            assert waiting["report"]["version"] == 1 and quality_sources == []
            release.set()
            body = await wait_reconciliation(client, identifier, "complete")
            assert body["report"]["version"] == 3
            assert [item["id"] for item in quality_sources[0]["observations"]] == [
                "speech-1",
                "late-context",
            ]
            assert (
                body["report"]["claims"][0]["proposition"]
                == "The original speaker's rate is twenty percent."
            )
            assert body["report"]["reconciliation"]["reassessment_claim_ids"] == [
                body["report"]["claims"][0]["id"]
            ]
