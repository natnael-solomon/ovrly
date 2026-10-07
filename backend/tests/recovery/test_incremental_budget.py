import asyncio

import httpx
import pytest

from recovery.test_claim_extraction import completion, create_investigation, window
from services.jobs.handlers import default_handlers
from services.pipeline.incremental import ExtractionPolicy, submit_observations
from services.pipeline.llm import ScholarxivAdapter


def policy(**changes):
    return ExtractionPolicy(
        **{
            "max_requests": 10,
            "max_tokens": 200000,
            "reconciliation_requests": 1,
            "reconciliation_tokens": 20000,
            "batch_observations": 1,
            "overlap_observations": 1,
            "max_observations": 20,
            **changes,
        }
    )


@pytest.mark.parametrize("tokens,expected_calls", [(5948, 0), (5949, 1)])
async def test_serialized_prompt_and_output_reservation_exact_boundary(
    harness, tokens, expected_calls
):
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
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_tokens=20000 + tokens),
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert len(calls) == expected_calls
            # Fixed fixture: 3645 ASCII-serialized bytes, 2048 output cap, 256 framing allowance.
            assert body["extraction_progress"]["tokens_reserved"] == 5949 * expected_calls
            assert body["extraction_progress"]["requests_used"] == expected_calls


@pytest.mark.parametrize("mode", ["repair", "gateway"])
async def test_feedback_routing_and_repairs_share_the_cumulative_budget(harness, mode):
    calls = []
    completions = 0

    def provider(request):
        nonlocal completions
        calls.append(request.url.path)
        if request.url.path == "/api/v1/router/feedback":
            return httpx.Response(204)
        if request.url.path == "/api/v1/router":
            return httpx.Response(
                200,
                json={
                    "model": "fixture-free-model",
                    "fallbacks": ["fixture-free-model"],
                    "preset": "cheap",
                    "degraded": False,
                },
            )
        completions += 1
        if completions == 1:
            return (
                httpx.Response(502)
                if mode == "gateway"
                else httpx.Response(200, json=completion({"invalid": True}))
            )
        return httpx.Response(200, json=completion({"occurrences": []}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=2048,
                    recovery_attempts=1,
                )
            ),
            job_retry_backoff_seconds=0.01,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_requests=3),
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
            await harness.wait_for_state(queued[0].job_id, "failed", "published")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert len(calls) == 2
            assert completions == 1
            assert body["error"]["code"] == "EXTRACTION_BUDGET_EXHAUSTED"
            assert body["extraction_progress"]["requests_used"] == 2


async def test_concurrent_workers_cannot_spend_the_same_remaining_request(harness):
    calls = []
    release = asyncio.Event()
    entered = asyncio.Event()

    async def provider(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=completion({"occurrences": []}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        )
        app = harness.app(None, stages=handlers)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_requests=2),
                    observations=window().observations,
                    closed=False,
                    hosted_processing_approved=True,
                )
            await asyncio.wait_for(entered.wait(), 5)
            async with harness.control.engine.begin() as connection:
                second = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(max_requests=2),
                    observations=[window().observations[0].model_copy(update={"id": "second"})],
                    closed=True,
                    hosted_processing_approved=True,
                )
            worker = harness.worker(None, stages=handlers)
            await worker.start()
            try:
                await harness.wait_for_state(second[0].job_id, "failed")
                assert len(calls) == 1
            finally:
                release.set()
            await harness.wait_for_state(queued[0].job_id, "published")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert body["extraction_progress"]["requests_used"] == 1
            assert [item["status"] for item in body["extraction_progress"]["observations"]] == [
                "skipped",
                "processed",
            ]
