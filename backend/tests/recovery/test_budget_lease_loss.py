import httpx
import pytest

from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_incremental_budget import policy
from recovery.test_reconciliation import wait_reconciliation
from recovery.test_reconciliation_safety import handlers
from services.jobs.queue import JobQueue, LeaseLost
from services.pipeline.incremental import submit_observations
from services.pipeline.reconciliation import request_reconciliation

# Fixed fixture: one extraction request reserves 5949 tokens (see the exact-boundary test).
EXTRACTION_TOKENS = 5949


@pytest.mark.parametrize("stage", ["extraction", "reconciliation"])
async def test_refused_reservation_with_lost_checkpoint_never_becomes_unknown_outcome(
    harness, monkeypatch, stage
):
    calls = []
    lost = []
    original = JobQueue.save_stage_data

    async def lose_budget_checkpoint(self, lease, data, *, connection=None):
        if connection is None and data.get("budget_exhausted") and not lost:
            lost.append(lease.job_id)
            raise LeaseLost("Injected loss of the budget-exhaustion checkpoint")
        await original(self, lease, data, connection=connection)

    monkeypatch.setattr(JobQueue, "save_stage_data", lose_budget_checkpoint)

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http))
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            limits = (
                policy(max_tokens=20001, reconciliation_tokens=20000)
                if stage == "extraction"
                else policy(max_tokens=EXTRACTION_TOKENS + 100, reconciliation_tokens=100)
            )
            async with harness.control.engine.begin() as connection:
                queued = await submit_observations(
                    connection,
                    harness.queue,
                    identifier,
                    harness.owner.id,
                    policy=limits,
                    observations=window().observations,
                    closed=True,
                    hosted_processing_approved=True,
                )
                if stage == "reconciliation":
                    await request_reconciliation(
                        connection, identifier, harness.owner.id, accepted_jobs=[]
                    )
            if stage == "extraction":
                await harness.wait_for_state(queued[0].job_id, "failed", seconds=10)
                body = (await client.get(f"/v1/investigations/{identifier}")).json()
                assert body["error"]["code"] == "EXTRACTION_BUDGET_EXHAUSTED"
                assert body["extraction_progress"]["observations"][-1]["status"] == "skipped"
                assert (
                    body["extraction_progress"]["observations"][-1]["reason"] == "budget_exhausted"
                )
                assert calls == []
            else:
                body = await wait_reconciliation(client, identifier, "failed")
                error = body["reconciliation_progress"]["error"]
                assert error["code"] == "EXTRACTION_BUDGET_EXHAUSTED"
                assert body["report"]["version"] == 1
                assert len(calls) == 1
            assert len(lost) == 1
