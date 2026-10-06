import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from recovery.test_captures import DATA, close, create, put
from sqlalchemy import delete, func, select, update
from test_intake_api import URL_BODY, assert_error, guest
from test_reports_api import publish

from services.api.auth import Principal
from services.api.errors import ApiError
from services.api.main import create_app
from services.database import Database
from services.jobs.models import jobs
from services.jobs.queue import JobQueue, StageKey
from services.jobs.retries import RateLimited
from services.models import (
    capture_chunks,
    capture_sessions,
    investigations,
    principals,
    provider_buckets,
    provider_slots,
    quota_usage,
    report_versions,
)
from services.providers.budget import TokenBucket
from services.providers.http import ProviderError
from services.quota_summary import summary
from services.quotas import active_checks, lock_owner
from services.settings import Settings


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            quotas_enabled=True,
            quota_active_checks=2,
            quota_daily_checks=3,
            quota_daily_upload_bytes=100,
            _env_file=None,
        )
    )
    async with application.router.lifespan_context(application):
        yield application
        # Do not leave queued capture work for another test's worker/storage directory.
        async with application.state.database.engine.begin() as connection:
            for identifier in application.state.quota_test_owners:
                rows = await connection.execute(
                    select(jobs.c.id).where(jobs.c.owner_id == identifier)
                )
                for job_id in rows.scalars():
                    await JobQueue(application.state.database).delete(job_id, connection=connection)


@pytest.fixture
async def client(app):
    app.state.quota_test_owners = set()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        yield client


async def owner(client, app):
    headers = await guest(client)
    # The tests read only the new principal, not another test's jobs.
    response = await client.post(
        "/v1/investigations",
        json=URL_BODY,
        headers={**headers, "Idempotency-Key": "first"},
    )
    assert response.status_code == 202, response.text
    identifier = uuid.UUID(response.json()["id"])
    async with app.state.database.engine.connect() as connection:
        owner_id = await connection.scalar(
            select(investigations.c.owner_id).where(investigations.c.id == identifier)
        )
    app.state.quota_test_owners.add(owner_id)
    return headers, owner_id, response


async def check(client, headers, key):
    return await client.post(
        "/v1/investigations",
        json=URL_BODY,
        headers={**headers, "Idempotency-Key": key},
    )


async def clear_active(app, owner_id):
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs).where(jobs.c.owner_id == owner_id).values(state="cancelled")
        )


async def usage(app, owner_id):
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(select(quota_usage).where(quota_usage.c.owner_id == owner_id))
        ).one()


async def test_active_limit_serializes_concurrent_admissions_and_preserves_replays(app, client):
    headers, owner_id, original = await owner(client, app)
    responses = await asyncio.gather(
        check(client, headers, "second"), check(client, headers, "third")
    )
    assert sorted(r.status_code for r in responses) == [202, 429]
    denied = next(r for r in responses if r.status_code == 429)
    assert_error(denied, 429, "QUOTA_EXCEEDED", "retry")
    assert denied.headers["Retry-After"] == "30"
    assert (await check(client, headers, "first")).json() == original.json()
    assert (await usage(app, owner_id)).checks == 2
    other, _, _ = await owner(client, app)
    assert (await check(client, other, "other")).status_code == 202


async def test_daily_budget_survives_cancel_and_content_deletion_then_resets_utc(app, client):
    headers, owner_id, _ = await owner(client, app)
    for key in ("second", "third"):
        await clear_active(app, owner_id)
        assert (await check(client, headers, key)).status_code == 202
    await clear_active(app, owner_id)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            delete(investigations).where(investigations.c.owner_id == owner_id)
        )
    denied = await check(client, headers, "fourth")
    assert_error(denied, 429, "QUOTA_EXCEEDED")
    assert 1 <= int(denied.headers["Retry-After"]) <= 86400
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(quota_usage)
            .where(quota_usage.c.owner_id == owner_id)
            .values(day=datetime.now(UTC).date() - timedelta(days=1))
        )
    assert (await check(client, headers, "next-day")).status_code == 202
    assert (await usage(app, owner_id)).checks == 1


async def test_reanalysis_budget_rolls_back_correction_and_replays_after_exhaustion(app, client):
    headers, owner_id, original = await owner(client, app)
    identifier = original.json()["id"]
    report = await publish(app, identifier)
    path = f"/v1/investigations/{identifier}/reanalyze"
    body = {"reason": "deeper", "base_version": report.version}
    first = await client.post(path, json=body, headers={**headers, "Idempotency-Key": "again"})
    assert first.status_code == 202, first.text
    second = await client.post(path, json=body, headers={**headers, "Idempotency-Key": "again-2"})
    assert second.status_code == 202, second.text
    denied = await client.post(
        path,
        json={
            "reason": "correction",
            "base_version": report.version,
            "claim_id": report.claims[0].id,
            "proposition": "A different synthetic claim.",
        },
        headers={**headers, "Idempotency-Key": "correction"},
    )
    assert_error(denied, 429, "QUOTA_EXCEEDED")
    assert (
        await client.post(path, json=body, headers={**headers, "Idempotency-Key": "again"})
    ).json() == first.json()
    assert (await usage(app, owner_id)).checks == 3
    async with app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(report_versions)
                .where(report_versions.c.investigation_id == uuid.UUID(identifier))
            )
            == 1
        )


async def test_failed_enqueue_rolls_back_admission(app, client):
    headers, owner_id, _ = await owner(client, app)

    class Failed:
        async def dispatch(self, *args):
            raise RuntimeError("synthetic dispatcher failure")

    dispatcher = app.state.dispatcher
    app.state.dispatcher = Failed()
    assert_error(await check(client, headers, "failed"), 500, "INTERNAL_ERROR")
    assert (await usage(app, owner_id)).checks == 1
    app.state.dispatcher = dispatcher
    assert (await check(client, headers, "failed")).status_code == 202


async def test_capture_reservations_charge_once_and_group_fanout(app, client):
    headers, owner_id, _ = await owner(client, app)
    session = await create(client, headers, key="capture")
    assert await create(client, headers, key="capture") == session
    assert (await put(client, headers, session)).status_code == 200
    assert (await put(client, headers, session)).json()["disposition"] == "duplicate"
    assert (await usage(app, owner_id)).upload_bytes == len(DATA)
    async with app.state.database.engine.begin() as connection:
        for stage in ("asr", "device_text"):
            await JobQueue(app.state.database).enqueue(
                connection,
                StageKey(1, stage, uuid.uuid4().hex),
                {"capture_id": session["id"], "seq": 0},
                owner_id=owner_id,
            )
        assert len(await active_checks(connection, owner_id)) == 2
    assert_error(await check(client, headers, "over"), 429, "QUOTA_EXCEEDED")
    assert (await close(client, headers, session, choice=False)).status_code == 200


async def test_upload_reservations_and_chunks_share_byte_budget(app, client):
    headers, owner_id, _ = await owner(client, app)
    session = await create(client, headers)
    body = {"size_bytes": 90, "sha256": "a" * 64, "content_type": "video/mp4"}
    assert (await client.post("/v1/uploads", json=body, headers=headers)).status_code == 201
    denied = await put(client, headers, session)
    assert_error(denied, 429, "QUOTA_EXCEEDED")
    assert (await usage(app, owner_id)).upload_bytes == 90
    async with app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(capture_chunks)
                .where(capture_chunks.c.session_id == uuid.UUID(session["id"]))
            )
            == 0
        )
    body["size_bytes"] = 10
    assert (await client.post("/v1/uploads", json=body, headers=headers)).status_code == 201
    body["size_bytes"] = 1
    assert_error(
        await client.post("/v1/uploads", json=body, headers=headers), 429, "QUOTA_EXCEEDED"
    )
    assert (await usage(app, owner_id)).upload_bytes == 100


async def test_provider_pause_preserves_replays_reads_stop_and_existing_chunks(app, client):
    headers, owner_id, original = await owner(client, app)
    session = await create(client, headers)
    app.state.settings.scholarxiv_api_key = "synthetic-key"
    bucket = TokenBucket(app.state.database, "scholarxiv", 1000)
    await bucket.block(60)
    try:
        denied = await check(client, headers, "paused")
        assert_error(denied, 429, "PROVIDER_QUOTA_EXHAUSTED")
        assert int(denied.headers["Retry-After"]) >= 60
        assert (await check(client, headers, "first")).json() == original.json()
        assert (await put(client, headers, session)).status_code == 200
        assert (await close(client, headers, session)).status_code == 200
        assert (await client.get("/v1/investigations", headers=headers)).status_code == 200
        assert (await usage(app, owner_id)).checks == 2
        state = await summary(app.state.database, app.state.settings)
        assert state["intake_paused"]
        assert state["providers"]["groq"]["upstream_remaining"] is None
        assert "synthetic-key" not in json.dumps(state)
    finally:
        async with app.state.database.engine.begin() as connection:
            await connection.execute(
                delete(provider_buckets).where(provider_buckets.c.name == "scholarxiv")
            )


async def test_disabled_policy_preserves_existing_behavior(app, client):
    app.state.settings.quotas_enabled = False
    headers, owner_id, _ = await owner(client, app)
    for index in range(5):
        assert (await check(client, headers, str(index))).status_code == 202
    async with app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(quota_usage.c.owner_id).where(quota_usage.c.owner_id == owner_id)
            )
            is None
        )


async def test_request_slots_are_shared_release_on_cancel_and_recover_expiry(database_url):
    config = Settings(database_url=database_url, _env_file=None)
    first, second = Database(config), Database(config)
    name = "slots-" + uuid.uuid4().hex
    a = TokenBucket(first, name, 10, concurrency=1)
    b = replace(a, database=second)
    try:
        async with a.request():
            with pytest.raises(RateLimited):
                async with b.request():
                    pytest.fail("Second process exceeded concurrency")
            async with first.engine.connect() as connection:
                assert await TokenBucket.balance(connection, name, 10) < 9.1
                assert await TokenBucket.balance(connection, name, 10) >= 9
        entered = asyncio.Event()

        async def pending():
            async with a.request():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(pending())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with b.request():
            pass
        old_lease = uuid.uuid4()
        assert await a._slot(old_lease) == 0
        async with first.engine.begin() as connection:
            await connection.execute(
                update(provider_slots)
                .where(provider_slots.c.provider == name)
                .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
            )
        async with b.request():
            pass
        with pytest.raises(ProviderError, match="deadline"):
            async with replace(a, request_timeout_seconds=0.01).request():
                await asyncio.sleep(0.1)
        async with first.engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(provider_slots)
                    .where(provider_slots.c.provider == name)
                )
                == 0
            )
    finally:
        await first.close()
        await second.close()


async def test_weighted_bucket_and_invalid_costs(database_url):
    database = Database(Settings(database_url=database_url, _env_file=None))
    bucket = TokenBucket(database, "weighted-" + uuid.uuid4().hex, 120, max_wait_seconds=0)
    try:
        await bucket.acquire(90)
        with pytest.raises(RateLimited):
            await bucket.acquire(31)
        async with database.engine.connect() as connection:
            assert 30 <= await TokenBucket.balance(connection, bucket.name, 120) < 31
        for amount in (0, -1, float("nan"), float("inf"), 121):
            with pytest.raises(ValueError):
                await bucket.acquire(amount)
    finally:
        await database.close()


async def test_two_slots_reject_third_and_rate_limit_releases_slot(database_url):
    database = Database(Settings(database_url=database_url, _env_file=None))
    bucket = TokenBucket(database, "two-slots-" + uuid.uuid4().hex, 2, concurrency=2)
    try:
        async with bucket.request(), bucket.request():
            with pytest.raises(RateLimited):
                async with bucket.request():
                    pytest.fail("A third request exceeded the two-slot limit")
            async with database.engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(provider_slots)
                        .where(provider_slots.c.provider == bucket.name)
                    )
                    == 2
                )
        with pytest.raises(RateLimited):
            async with bucket.request():
                pytest.fail("A request with no rate units was admitted")
        async with database.engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(func.count())
                    .select_from(provider_slots)
                    .where(provider_slots.c.provider == bucket.name)
                )
                == 0
            )
    finally:
        await database.close()


async def test_expired_capture_and_rejected_principal_cannot_hold_or_spend_quota(app, client):
    headers, owner_id, _ = await owner(client, app)
    session = await create(client, headers)
    await clear_active(app, owner_id)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(capture_sessions)
            .where(capture_sessions.c.id == uuid.UUID(session["id"]))
            .values(expires_at=func.clock_timestamp() - timedelta(seconds=1))
        )
        assert await active_checks(connection, owner_id) == set()
    assert (await check(client, headers, "after-expiry")).status_code == 202
    other_headers, other_id, _ = await owner(client, app)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(principals).where(principals.c.id == owner_id).values(merged_into=other_id)
        )
        for identifier in (owner_id, uuid.uuid4()):
            with pytest.raises(ApiError) as rejected:
                await lock_owner(connection, Principal(identifier, "guest"), app.state.settings)
            assert rejected.value.code == "INVALID_CREDENTIAL"
    assert (await usage(app, owner_id)).checks == 3
    assert (await check(client, other_headers, "still-allowed")).status_code == 202


async def test_summary_command_is_read_only_and_fails_without_success_output(
    app, client, monkeypatch, capsys
):
    from services import quota_summary

    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            delete(provider_buckets).where(provider_buckets.c.name == "scholarxiv")
        )
    monkeypatch.setattr(quota_summary, "load_settings", lambda: app.state.settings)
    await quota_summary.main()
    output = json.loads(capsys.readouterr().out)
    assert output["providers"]["scholarxiv"]["local_available_units"] is None
    assert output["providers"]["scholarxiv"]["observed"] is False
    assert output["providers"]["voxide"]["upstream_remaining"] is None
    assert not output["intake_paused"]
    async with app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(provider_buckets.c.name).where(provider_buckets.c.name == "scholarxiv")
            )
            is None
        )

    async def unavailable(*args):
        raise TimeoutError("do not expose this internal detail")

    monkeypatch.setattr(quota_summary, "summary", unavailable)
    with pytest.raises(SystemExit) as stopped:
        await quota_summary.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "Quota summary unavailable: database request failed\n"


def test_quota_configuration_requires_valid_limits():
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://local@localhost/test",
            quotas_enabled=True,
            scholarxiv_requests_per_hour=50,
            quota_provider_reserve=50,
        )
