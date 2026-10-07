import json

import httpx
import pytest

from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from services.jobs.handlers import default_handlers
from services.pipeline.llm import ScholarxivAdapter


async def test_threshold_updates_overlap_without_collapsing_later_repetition(harness):
    from services.pipeline.incremental import ExtractionPolicy, submit_observations

    requests = []

    def provider(request):
        payload = json.loads(request.content)
        observations = json.loads(payload["messages"][1]["content"])["observations"]
        requests.append([item["id"] for item in observations])
        output = extraction()
        template = output["occurrences"][0]
        output["occurrences"] = [
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
            for item in observations
        ]
        return httpx.Response(200, json=completion(output))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            policy = ExtractionPolicy(
                max_requests=10,
                max_tokens=200000,
                reconciliation_requests=2,
                reconciliation_tokens=20000,
                batch_observations=2,
                overlap_observations=1,
                max_observations=20,
            )
            first = window().observations[0]
            second = first.model_copy(update={"id": "speech-2", "start_ms": 5000, "end_ms": 8000})
            third = first.model_copy(update={"id": "speech-3", "start_ms": 9000, "end_ms": 12000})
            async with harness.control.engine.begin() as connection:
                assert (
                    await submit_observations(
                        connection,
                        harness.queue,
                        investigation_id,
                        harness.owner.id,
                        policy=policy,
                        observations=[first],
                        closed=False,
                        hosted_processing_approved=True,
                    )
                    == []
                )
            pending = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert pending["extraction_progress"]["observations"][0]["status"] == "pending"
            assert requests == []
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=[second],
                    closed=False,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published")
            original = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
            assert original["provisional"] is True
            assert len(original["claims"]) == 2
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=[third, first],
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published")
            updated = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert requests == [["speech-1", "speech-2"], ["speech-2", "speech-3"]]
            assert len(updated["report"]["claims"]) == 3
            assert updated["report"]["claims"][:2] == original["claims"]
            assert updated["report"]["version"] == 2
            assert updated["report"]["provisional"] is True
            progress = updated["extraction_progress"]
            assert progress["requests_used"] == 2
            assert progress["reconciliation_requests"] == 2
            assert all(item["status"] == "processed" for item in progress["observations"])


async def test_successful_overlap_repairs_earlier_failed_coverage(harness):
    from recovery.test_incremental_budget import policy
    from services.pipeline.incremental import submit_observations

    calls = []

    def provider(request):
        calls.append(request)
        return (
            httpx.Response(502)
            if len(calls) == 1
            else httpx.Response(200, json=completion(extraction()))
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                first = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=window().observations,
                    closed=False,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(first[0].job_id, "failed")
            failed = (await client.get(f"/v1/investigations/{identifier}")).json()
            gap = failed["extraction_progress"]["observations"][0]
            assert gap["status"] == "failed"
            assert gap["reason"] == "EXTRACTION_UNAVAILABLE"
            async with harness.control.engine.begin() as connection:
                second = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=[
                        window()
                        .observations[0]
                        .model_copy(update={"id": "speech-2", "start_ms": 5000, "end_ms": 8000})
                    ],
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(second[0].job_id, "published")
            updated = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert len(updated["report"]["claims"]) == 1
            assert all(
                value["status"] == "processed" and value["reason"] is None
                for value in updated["extraction_progress"]["observations"]
            )
            assert updated["extraction_progress"]["requests_used"] == 2


@pytest.mark.parametrize("limit", ["requests", "tokens"])
async def test_budget_exhaustion_preserves_reserve_and_marks_unprocessed_input(harness, limit):
    from services.pipeline.incremental import ExtractionPolicy, submit_observations

    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion({"occurrences": []}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            policy = ExtractionPolicy(
                max_requests=2 if limit == "requests" else 10,
                max_tokens=200000 if limit == "requests" else 20001,
                reconciliation_requests=1,
                reconciliation_tokens=20000,
                batch_observations=1,
                overlap_observations=1,
                max_observations=20,
            )
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=[window().observations[0]],
                    closed=False,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published", "failed")
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=[
                        window()
                        .observations[0]
                        .model_copy(
                            update={
                                "id": "later",
                                "start_ms": 5000,
                                "end_ms": 8000,
                            }
                        )
                    ],
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            progress = body["extraction_progress"]
            assert len(calls) == (1 if limit == "requests" else 0)
            assert progress["requests_used"] == len(calls)
            assert progress["tokens_reserved"] <= policy.max_tokens - policy.reconciliation_tokens
            assert progress["observations"][-1]["status"] == "skipped"
            assert progress["observations"][-1]["reason"] == "budget_exhausted"
            assert body["error"]["code"] == "EXTRACTION_BUDGET_EXHAUSTED"
            if limit == "requests":
                assert (
                    await client.get(f"/v1/investigations/{investigation_id}/reports/1")
                ).status_code == 200
