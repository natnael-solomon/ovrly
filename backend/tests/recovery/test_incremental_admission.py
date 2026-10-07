import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from recovery.test_claim_extraction import completion, create_investigation, window
from recovery.test_incremental_budget import policy
from services.api.main import create_app
from services.jobs.handlers import default_handlers
from services.jobs.models import jobs
from services.jobs.retries import NonRetriableInput
from services.pipeline.extraction import enqueue_extraction
from services.pipeline.incremental import submit_observations
from services.pipeline.llm import ScholarxivAdapter
from services.pipeline.stub_reports import stub_report
from services.reports import publish_report_version


async def test_concurrent_out_of_order_redelivery_enqueues_only_once(harness):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion({"occurrences": []}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = create_app(harness.settings())
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            first = window().observations[0]
            second = first.model_copy(update={"id": "later", "start_ms": 5000, "end_ms": 6000})

            async def deliver(observations):
                async with harness.control.engine.begin() as connection:
                    return await submit_observations(
                        connection,
                        harness.queue,
                        identifier,
                        harness.owner.id,
                        policy=policy(batch_observations=2),
                        observations=observations,
                        closed=True,
                        hosted_processing_approved=True,
                    )

            deliveries = await asyncio.gather(deliver([second, first]), deliver([first, second]))
            assert sorted(map(len, deliveries)) == [0, 1]
            queued = next(value[0] for value in deliveries if value)
            worker = harness.worker(
                None,
                stages=default_handlers(
                    llm=ScholarxivAdapter(
                        http, allowed_models=["fixture-free-model"], max_tokens=2048
                    )
                ),
            )
            await worker.start()
            await harness.wait_for_state(queued.job_id, "published")
            body = (await client.get(f"/v1/investigations/{identifier}")).json()
            assert len(calls) == 1
            assert [
                item["observation_id"] for item in body["extraction_progress"]["observations"]
            ] == [
                first.id,
                second.id,
            ]
            assert body["report"]["provisional"]


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "authorization",
        "policy",
        "content",
        "closed",
        "bound",
        "timebase",
    ],
)
async def test_conflicting_admission_rolls_back_without_losing_pending_input(harness, change):
    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        identifier = await create_investigation(client)
        source = window().observations[0]
        initial_policy = policy(
            batch_observations=2, max_observations=1 if change == "bound" else 20
        )
        async with harness.control.engine.begin() as connection:
            queued = await submit_observations(
                connection,
                harness.queue,
                identifier,
                harness.owner.id,
                policy=initial_policy,
                observations=[source],
                closed=change == "closed",
                hosted_processing_approved=True,
            )
        for item in queued:
            await harness.queue.request_cancel(item.job_id)
        options = {
            "policy": initial_policy,
            "observations": [source],
            "closed": False,
            "hosted_processing_approved": True,
        }
        if change == "authorization":
            options["hosted_processing_approved"] = False
        elif change == "policy":
            options["policy"] = initial_policy.model_copy(update={"max_requests": 11})
        elif change == "content":
            options["observations"] = [source.model_copy(update={"text": "Changed source"})]
        elif change in {"closed", "bound", "timebase"}:
            options["observations"] = [
                source.model_copy(
                    update={
                        "id": "new",
                        "timebase": "capture" if change == "timebase" else "media",
                    }
                )
            ]
        with pytest.raises(NonRetriableInput):
            async with harness.control.engine.begin() as connection:
                await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    uuid.uuid4() if change == "owner" else harness.owner.id,
                    **options,
                )
        body = (await client.get(f"/v1/investigations/{identifier}")).json()
        assert len(body["extraction_progress"]["observations"]) == 1
        assert body["extraction_progress"]["requests_used"] == 0


async def test_purged_window_is_a_gap_and_does_not_readmit_source(harness):
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
        await harness.queue.delete(queued[0].job_id)
        async with harness.control.engine.begin() as connection:
            await connection.execute(delete(jobs).where(jobs.c.id == queued[0].job_id))
            assert not await connection.scalar(
                select(jobs.c.id).where(jobs.c.id == queued[0].job_id)
            )
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
        body = (await client.get(f"/v1/investigations/{identifier}")).json()
        assert body["extraction_progress"]["observations"][0]["reason"] == "job_unavailable"


async def test_incremental_key_cannot_reuse_an_unbudgeted_legacy_job(harness):
    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        identifier = await create_investigation(client)
        async with harness.control.engine.begin() as connection:
            legacy = await enqueue_extraction(
                connection,
                harness.queue,
                identifier,
                harness.owner.id,
                window().model_copy(update={"window_id": "incremental-v1-1"}),
            )
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
        await harness.queue.delete(legacy.job_id)
        await harness.queue.delete(queued[0].job_id)
        assert legacy.job_id != queued[0].job_id


@pytest.mark.parametrize("finalized", [False, True])
async def test_cumulative_report_preserves_prior_research_and_fixture_identity(harness, finalized):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=completion({"occurrences": []}))
        ),
        base_url="https://router.example",
    ) as http:
        app = create_app(harness.settings())
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                original = await publish_report_version(
                    connection,
                    identifier,
                    lambda identity: stub_report(identity).model_copy(
                        update={"provisional": not finalized}
                    ),
                    fixture=True,
                )

            async def admit():
                async with harness.control.engine.begin() as connection:
                    return await submit_observations(
                        connection,
                        harness.queue,
                        identifier,
                        harness.owner.id,
                        policy=policy(),
                        observations=window().observations,
                        closed=True,
                        hosted_processing_approved=True,
                    )

            if finalized:
                with pytest.raises(NonRetriableInput):
                    await admit()
                return
            queued = await admit()
            worker = harness.worker(
                None,
                stages=default_handlers(
                    llm=ScholarxivAdapter(
                        http, allowed_models=["fixture-free-model"], max_tokens=2048
                    )
                ),
            )
            await worker.start()
            await harness.wait_for_state(queued[0].job_id, "published")
            report = (await client.get(f"/v1/investigations/{identifier}/reports/2")).json()
            assert report["fixture"] is True
            assert report["claims"] == original.model_dump(mode="json")["claims"]
            assert report["evidence"] == original.model_dump(mode="json")["evidence"]
            assert report["assessments"] == [
                {**value, "version": 2} for value in original.model_dump(mode="json")["assessments"]
            ]
