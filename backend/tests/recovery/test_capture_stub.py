"""Development-only capture stub (OVRLY_STUB_REPORTS): fixture versions progress per chunk and
close, never without the opt-in, and the capture status reports their claims."""

import uuid

import httpx
import pytest
from test_intake_api import CONTRACT_VALIDATOR, guest

from services.api.main import create_app
from services.captures import CAPTURE_STAGE
from services.jobs.handlers import JobContext, default_handlers
from services.jobs.queue import JobQueue
from services.pipeline.stub_reports import (
    capture_with_stub_report,
    enable_stub_reports,
    sync_capture_stub,
)
from services.settings import Settings

from .test_captures import close, create, put, status

PREFIX = "Development fixture, not a check of this media"


def app_with(database_url, tmp_path, stub):
    return create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "captures",
            upload_max_bytes=1024,
            stub_reports=stub,
            _env_file=None,
        )
    )


@pytest.fixture(params=[True, False], ids=["stub-on", "stub-off"])
async def stub_app(request, database_url, tmp_path):
    app = app_with(database_url, tmp_path, request.param)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(stub_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def process_next(app):
    """Run one queued capture chunk job through the stage table the worker would use."""
    handlers = dict(default_handlers(app.state.upload_store))
    if app.state.settings.stub_reports:
        enable_stub_reports(handlers)
    queue = JobQueue(app.state.database)
    claim = await queue.claim("capture-stub-test", [CAPTURE_STAGE], 30)
    assert claim is not None
    await queue.start(claim.lease)
    result = await handlers[CAPTURE_STAGE](claim, JobContext(queue, claim.lease, 30))
    await queue.publish(claim.lease, result)


async def reports(client, headers, session):
    response = await client.get(f"/v1/investigations/{session['id']}/reports", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def read(client, headers, session):
    response = await client.get(f"/v1/investigations/{session['id']}", headers=headers)
    assert response.status_code == 200, response.text
    CONTRACT_VALIDATOR.validate(response.json(), "investigation.schema.json")
    return response.json()


async def test_capture_reports_progress_per_chunk_and_close_only_with_the_stub(client, stub_app):
    stub = stub_app.state.settings.stub_reports
    headers = await guest(client)
    session = await create(client, headers, duration=1000)
    for seq in range(3):
        assert (await put(client, headers, session, seq)).status_code == 200
    seen = []
    for expected_claims, expected_assessed in ((1, 0), (2, 1), (2, 2)):
        await process_next(stub_app)
        body = await read(client, headers, session)
        polled = await status(client, headers, session)
        if not stub:
            assert body["report"] is None and polled["claims"] == []
            assert polled["claim_extraction_status"] == "not_started"
            continue
        report = body["report"]
        seen.append(report["version"])
        assert report["fixture"] is True and report["provisional"] is True
        assert report["change_summary"].startswith(PREFIX)
        assert len(report["claims"]) == expected_claims
        assert len(report["assessments"]) == expected_assessed
        assert all(claim["interval"]["timebase"] == "capture" for claim in report["claims"])
        assert all(claim["correction"] is None for claim in report["claims"])
        assessed = {item["claim_id"] for item in report["assessments"]}
        assert {item["claim_id"] for item in report["evidence"]} == assessed
        assert body["processing_status"] == "partial"
        assert polled["claim_extraction_status"] == "partial"
        assert [claim["claim_id"] for claim in polled["claims"]] == [
            claim["id"] for claim in report["claims"]
        ]
        assert [claim["processing_status"] for claim in polled["claims"]] == [
            "partial" if claim["id"] in assessed else "checking" for claim in report["claims"]
        ]
    if stub:
        assert seen == [1, 2, 3]
        # Re-running a chunk job's sync without new content publishes nothing.
        before = await reports(client, headers, session)
        async with stub_app.state.database.engine.begin() as connection:
            assert await sync_capture_stub(connection, uuid.UUID(session["id"])) is None
        assert await reports(client, headers, session) == before

    closed = await close(client, headers, session, True)
    assert closed.status_code == 200, closed.text
    body = await read(client, headers, session)
    polled = await status(client, headers, session)
    if not stub:
        assert body["report"] is None and polled["claims"] == []
        assert await reports(client, headers, session) == []
        return
    final = body["report"]
    assert final["version"] == 4 and final["provisional"] is False and final["fixture"] is True
    assert all(item["provisional"] is False for item in final["assessments"])
    assert len(final["assessments"]) == len(final["claims"]) == 2
    assert body["processing_status"] == "complete" and body["state"] == "completed"
    assert polled["claim_extraction_status"] == "complete"
    assert {claim["processing_status"] for claim in polled["claims"]} == {"complete"}
    listing = await reports(client, headers, session)
    assert [item["fixture"] for item in listing] == [True] * 4
    assert all(item["change_summary"].startswith(PREFIX) for item in listing)
    # Closing again replays the receipt and publishes nothing new.
    assert (await close(client, headers, session, True)).status_code == 200
    assert len(await reports(client, headers, session)) == 4


async def test_final_waits_for_chunks_still_in_the_queue(database_url, tmp_path):
    app = app_with(database_url, tmp_path, True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            headers = await guest(client)
            session = await create(client, headers, duration=1000)
            for seq in range(2):
                assert (await put(client, headers, session, seq)).status_code == 200
            await process_next(app)
            assert (await close(client, headers, session, True)).status_code == 200
            # One chunk is still queued, so close publishes no final version.
            assert (await read(client, headers, session))["report"]["provisional"] is True
            await process_next(app)
            final = (await read(client, headers, session))["report"]
            assert final["provisional"] is False and final["fixture"] is True
            assert [item["version"] for item in await reports(client, headers, session)] == [
                1,
                2,
            ]


async def test_stop_without_continuing_keeps_a_provisional_fixture(database_url, tmp_path):
    app = app_with(database_url, tmp_path, True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            headers = await guest(client)
            session = await create(client, headers, duration=1000)
            for seq in range(2):
                assert (await put(client, headers, session, seq)).status_code == 200
            await process_next(app)
            assert (await close(client, headers, session, False)).status_code == 200
            body = await read(client, headers, session)
            assert body["processing_status"] == "cancelled"
            assert body["report"]["provisional"] is True and body["report"]["fixture"] is True
            polled = await status(client, headers, session)
            assert [claim["processing_status"] for claim in polled["claims"]] == ["cancelled"]


def test_the_wrapper_is_installed_only_with_the_opt_in():
    plain = dict(default_handlers(object()))
    wrapped = dict(plain)
    enable_stub_reports(wrapped)
    assert wrapped[CAPTURE_STAGE] is not plain[CAPTURE_STAGE]
    assert (
        wrapped[CAPTURE_STAGE].__qualname__
        == capture_with_stub_report(plain[CAPTURE_STAGE]).__qualname__
    )
    assert dict(default_handlers(object()))[CAPTURE_STAGE].__qualname__ == "CaptureProcessor.run"
