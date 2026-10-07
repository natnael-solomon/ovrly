import asyncio

import httpx
import pytest

from recovery.harness import ScriptedFaults
from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_incremental_budget import policy
from services.api.main import create_app
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.jobs.retries import NonRetriableInput
from services.pipeline.incremental import submit_observations
from services.pipeline.llm import ScholarxivAdapter


@pytest.mark.parametrize(
    "checkpoint", [Checkpoint.AFTER_PROVIDER_CALL, Checkpoint.AFTER_ARTIFACT_STORE]
)
async def test_restart_never_refunds_reservation_or_repeats_committed_work(harness, checkpoint):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
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
            first = harness.worker(
                None, stages=handlers, faults=ScriptedFaults(crash_at=checkpoint)
            )
            await first.start()
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(first.wait(), 5)
            second = harness.worker(None, stages=handlers)
            await second.start()
            await harness.wait_for_state(queued[0].job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert len(calls) == 1
            assert body["extraction_progress"]["requests_used"] == 1
            assert body["extraction_progress"]["tokens_reserved"] == 5949
            assert body["extraction_progress"]["observations"][0]["status"] == (
                "processed" if checkpoint == Checkpoint.AFTER_ARTIFACT_STORE else "failed"
            )
            if checkpoint == Checkpoint.AFTER_ARTIFACT_STORE:
                assert len(body["report"]["claims"]) == 1
            else:
                assert body["error"]["code"] == "EXTRACTION_OUTCOME_UNKNOWN"
            async with harness.control.engine.begin() as connection:
                assert (
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
                    == []
                )


@pytest.mark.parametrize("action", ["cancel", "delete"])
async def test_cancellation_and_deletion_cannot_publish_or_refund_spending(harness, action):
    async def intervene(name, job):
        if name == Checkpoint.BEFORE_PUBLISH and job.key.stage == "claim_extraction":
            if action == "cancel":
                await harness.queue.request_cancel(job.id)
            else:
                await harness.queue.delete(job.id)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion(extraction()))
        ),
        base_url="https://router.example",
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
            faults=ScriptedFaults(on_checkpoint=intervene),
        )
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
            await harness.wait_for_state(queued[0].job_id, "cancelled", "deleted")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert body["report"] is None
            assert body["extraction_progress"]["requests_used"] == 1
            assert body["extraction_progress"]["observations"][0]["status"] == "skipped"
            async with harness.client(app, outsider=True) as outsider:
                assert (await outsider.get(f"/v1/investigations/{identifier}")).status_code == 404


@pytest.mark.parametrize("phase", ["before_send", "during_send"])
async def test_capture_keep_available_stops_incremental_work(harness, phase):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def before_send(name, job):
        if (
            phase == "before_send"
            and name == Checkpoint.CLAIMED
            and job.key.stage == "claim_extraction"
        ):
            entered.set()
            await release.wait()

    async def provider(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            faults=ScriptedFaults(on_checkpoint=before_send),
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client, capture=True)
            source = [window().observations[0].model_copy(update={"timebase": "capture"})]
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=policy(),
                    observations=source,
                    closed=False,
                    hosted_processing_approved=True,
                )
            await asyncio.wait_for(entered.wait(), 5)
            assert (
                await client.post(
                    f"/v1/captures/{identifier}/close",
                    json={
                        "continue_research": False,
                        "duration_ms": 4000,
                    },
                )
            ).status_code == 200
            release.set()
            record = await harness.wait_for_state(queued[0].job_id, "cancelled", "published")
            assert record.status.state == "cancelled"
            body = (await client.get(f"/v1/captures/{identifier}")).json()
            assert body["claims"] == []
            assert body["extraction_progress"]["observations"][0]["status"] == "skipped"
            assert len(calls) == (0 if phase == "before_send" else 1)
            with pytest.raises(NonRetriableInput):
                async with harness.control.engine.begin() as connection:
                    await submit_observations(
                        connection,
                        harness.queue,
                        identifier,
                        harness.owner.id,
                        policy=policy(),
                        observations=source,
                        closed=True,
                        hosted_processing_approved=True,
                    )
