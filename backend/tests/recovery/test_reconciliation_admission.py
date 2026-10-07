import json
import uuid

import httpx
import pytest
from sqlalchemy import delete, select, update

from recovery.test_claim_extraction import completion, create_investigation, extraction
from recovery.test_reconciliation import unchanged, wait_reconciliation
from recovery.test_reconciliation_safety import admit, handlers
from services.jobs.models import jobs
from services.jobs.retries import NonRetriableInput
from services.models import extraction_runs
from services.pipeline.reconciliation import request_reconciliation


@pytest.mark.parametrize("reason", ["owner", "no_run", "missing_job", "bound", "changed_seal"])
async def test_registration_rejects_unowned_missing_and_mutated_input(harness, reason):
    from services.api.main import create_app

    app = create_app(harness.settings())
    async with app.router.lifespan_context(app), harness.client(app) as client:
        identifier = await create_investigation(client)
        if reason not in {"no_run", "owner"}:
            queued = await admit(harness, identifier)
            for item in queued:
                await harness.queue.request_cancel(item.job_id)
        accepted = []
        if reason == "missing_job":
            accepted = [uuid.uuid4()]
        elif reason == "bound":
            accepted = [uuid.uuid4() for _ in range(4097)]
        elif reason == "changed_seal":
            accepted = [queued[0].job_id]
        with pytest.raises(NonRetriableInput):
            async with harness.control.engine.begin() as connection:
                await request_reconciliation(
                    connection,
                    identifier,
                    harness.outsider.id if reason == "owner" else harness.owner.id,
                    accepted_jobs=accepted,
                )


@pytest.mark.parametrize("mode", ["empty", "no_report", "purged", "repeat"])
async def test_reconciliation_handles_empty_failed_purged_and_repeated_work(harness, mode):
    def provider(request):
        body = json.loads(request.content)
        source = json.loads(body["messages"][1]["content"])
        if body["model"] == "auto:cheap":
            if mode == "no_report":
                return httpx.Response(403)
            result = {"occurrences": []} if mode == "empty" else extraction()
        else:
            result = unchanged(source)
        return httpx.Response(200, json=completion(result))

    async with httpx.AsyncClient(
        base_url="https://router.example", transport=httpx.MockTransport(provider)
    ) as http:
        app = harness.app(None, stages=handlers(http), job_lease_seconds=2)
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            await admit(harness, identifier)
            body = await wait_reconciliation(
                client, identifier, "failed" if mode == "no_report" else "complete"
            )
            if mode == "empty":
                assert body["report"]["claims"] == []
                assert body["report"]["provisional"] is True
            elif mode == "no_report":
                assert body["report"] is None
                assert body["reconciliation_progress"]["error"]["code"] == "PROCESSING_FAILED"
            elif mode == "purged":
                async with harness.control.engine.begin() as connection:
                    data = await connection.scalar(
                        select(extraction_runs.c.data).where(
                            extraction_runs.c.investigation_id == identifier
                        )
                    )
                    job_id = uuid.UUID(data["reconciliation"]["job_id"])
                await harness.queue.delete(job_id)
                async with harness.control.engine.begin() as connection:
                    await connection.execute(delete(jobs).where(jobs.c.id == job_id))
                body = await wait_reconciliation(client, identifier, "failed")
                assert body["report"]["version"] == 2
            else:
                async with harness.control.engine.begin() as connection:
                    data = await connection.scalar(
                        select(extraction_runs.c.data).where(
                            extraction_runs.c.investigation_id == identifier
                        )
                    )
                    # A finalized snapshot cannot publish again on a duplicate wake.
                    await connection.execute(
                        update(jobs)
                        .where(jobs.c.id == uuid.UUID(data["reconciliation"]["job_id"]))
                        .values(state="queued")
                    )
                body = await wait_reconciliation(client, identifier, "failed")
                assert body["report"]["version"] == 2
