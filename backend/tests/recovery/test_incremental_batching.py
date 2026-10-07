import json

import httpx

from recovery.test_claim_extraction import completion, create_investigation, window
from services.jobs.handlers import default_handlers
from services.pipeline.incremental import ExtractionPolicy, submit_observations
from services.pipeline.llm import ScholarxivAdapter


async def test_dense_input_is_split_without_discarding_accepted_observations(harness):
    calls = []

    def provider(request):
        source = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(source["observations"])
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
                max_requests=20,
                max_tokens=1000000,
                reconciliation_requests=2,
                reconciliation_tokens=20000,
                batch_observations=8,
                overlap_observations=2,
                max_observations=20,
            )
            observations = [
                window()
                .observations[0]
                .model_copy(
                    update={
                        "id": f"dense-{i}",
                        "text": "x" * 8000,
                        "start_ms": i * 1000,
                        "end_ms": (i + 1) * 1000,
                    }
                )
                for i in range(8)
            ]
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    investigation_id,
                    harness.owner.id,
                    policy=policy,
                    observations=list(reversed(observations)),
                    closed=True,
                    hosted_processing_approved=True,
                )
            for job in queued:
                await harness.wait_for_state(job.job_id, "published")
            progress = (await client.get(f"/v1/investigations/{investigation_id}")).json()[
                "extraction_progress"
            ]
            assert len(progress["observations"]) == 8
            assert all(item["status"] == "processed" for item in progress["observations"])
            assert all(sum(len(item["text"]) for item in batch) <= 24000 for batch in calls)
            assert {item["id"] for batch in calls for item in batch} == {
                item.id for item in observations
            }


async def test_batch_threshold_widens_before_remaining_requests_are_consumed(harness):
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
                max_requests=3,
                max_tokens=200000,
                reconciliation_requests=1,
                reconciliation_tokens=20000,
                batch_observations=1,
                overlap_observations=1,
                max_observations=20,
            )

            async def submit(index, closed=False):
                async with harness.control.engine.begin() as connection:
                    return await submit_observations(
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
                                    "id": f"speech-{index}",
                                    "start_ms": index * 4000,
                                    "end_ms": index * 4000 + 3000,
                                }
                            )
                        ],
                        closed=closed,
                        hosted_processing_approved=True,
                    )

            first = await submit(0)
            await harness.wait_for_state(first[0].job_id, "published")
            assert await submit(1) == []
            partial = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert partial["extraction_progress"]["observations"][-1]["status"] == "pending"
            second = await submit(2, closed=True)
            await harness.wait_for_state(second[0].job_id, "published")
            assert len(calls) == 2
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert all(
                item["status"] == "processed"
                for item in body["extraction_progress"]["observations"]
            )


async def test_context_only_close_is_an_explicit_gap_not_an_empty_finding(harness):
    app = harness.app(None, stages=default_handlers())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        investigation_id = await create_investigation(client)
        policy = ExtractionPolicy(
            max_requests=3,
            max_tokens=200000,
            reconciliation_requests=1,
            reconciliation_tokens=20000,
            batch_observations=2,
            overlap_observations=1,
            max_observations=20,
        )
        async with harness.control.engine.begin() as connection:
            await submit_observations(
                connection,
                harness.queue,
                investigation_id,
                harness.owner.id,
                policy=policy,
                observations=[window().observations[0].model_copy(update={"role": "context"})],
                closed=True,
                hosted_processing_approved=True,
            )
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["report"] is None
        assert body["extraction_progress"]["observations"][0]["status"] == "skipped"
        assert body["extraction_progress"]["observations"][0]["reason"] == "context_without_target"
