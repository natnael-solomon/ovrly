import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update
from test_account_link import LINK, FakeVerifier, link_body
from test_intake_api import CONTENT, completed_upload, declare_upload, guest

from services.api.main import create_app
from services.jobs.handlers import JobContext
from services.jobs.models import job_results, jobs
from services.jobs.queue import JobQueue, PublishRejected, StageKey
from services.models import credentials, idempotency_keys, investigations, principals, uploads
from services.privacy import RETENTION_STAGE, Retention
from services.settings import Settings
from services.worker.runtime import Worker


@pytest.fixture
async def privacy_app(database_url, tmp_path):
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            retention_enabled=True,
            job_lease_seconds=30,
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(privacy_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=privacy_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def seed(app, client):
    headers = await guest(client)
    upload = await completed_upload(client, headers)
    response = await client.post(
        "/v1/investigations",
        json={"source": {"kind": "upload", "upload_id": upload["id"]}},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == 202, response.text
    investigation_id = uuid.UUID(response.json()["id"])
    async with app.state.database.engine.connect() as connection:
        owner_id = await connection.scalar(
            select(investigations.c.owner_id).where(investigations.c.id == investigation_id)
        )
    return headers, uuid.UUID(upload["id"]), investigation_id, owner_id


async def expire(app, owner_id):
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(principals)
            .where(principals.c.id == owner_id)
            .values(created_at=func.now() - timedelta(days=2))
        )


def retention(app):
    return Retention(app.state.database, app.state.settings, app.state.upload_store)


async def sweep(app):
    service = retention(app)
    queue = service.queue
    # A unique stage isolates this direct handler exercise from scheduled retention jobs.
    stage = "retention-test-" + uuid.uuid4().hex
    async with app.state.database.engine.begin() as connection:
        queued = await queue.enqueue(connection, StageKey(1, stage, uuid.uuid4().hex), {})
    claimed = await queue.claim("retention-test", [stage], 30)
    await queue.start(claimed.lease)
    try:
        return await service.run(claimed, JobContext(queue, claimed.lease, 30))
    finally:
        await queue.delete(queued.job_id)


async def test_retention_erases_whole_workspace_and_preserves_other_owner(privacy_app, client):
    app = privacy_app
    headers, upload_id, investigation_id, owner_id = await seed(app, client)
    other_headers, _, other_investigation, other_owner = await seed(app, client)
    queue = JobQueue(app.state.database)
    stage = uuid.uuid4().hex
    async with app.state.database.engine.begin() as connection:
        queued = await queue.enqueue(
            connection,
            StageKey(1, stage, "synthetic-private-hash"),
            {"private": "payload"},
            owner_id=owner_id,
        )
    claim = await queue.claim("publisher", [stage], 30)
    await queue.start(claim.lease)
    await queue.record_request_id(claim.lease, "synthetic-provider-reference")
    await queue.publish(claim.lease, {"private": "result"})
    await expire(app, owner_id)
    counts = await sweep(app)
    assert counts["principals"] >= 1
    async with app.state.database.engine.connect() as connection:
        for table, clause in (
            (principals, principals.c.id == owner_id),
            (credentials, credentials.c.principal_id == owner_id),
            (uploads, uploads.c.id == upload_id),
            (investigations, investigations.c.id == investigation_id),
            (idempotency_keys, idempotency_keys.c.owner_id == owner_id),
            (job_results, job_results.c.job_id == queued.job_id),
        ):
            assert (await connection.execute(select(table).where(clause))).first() is None
        tombstone = (await connection.execute(select(jobs).where(jobs.c.id == queued.job_id))).one()
        assert tombstone.state == "deleted" and tombstone.payload == {}
        assert tombstone.owner_id is None and tombstone.provider_request_id is None
        assert tombstone.input_hash == queued.job_id.hex
        assert await connection.scalar(
            select(principals.c.id).where(principals.c.id == other_owner)
        )
    assert len(list(app.state.settings.storage_dir.iterdir())) == 1
    assert (
        await client.get(f"/v1/investigations/{investigation_id}", headers=headers)
    ).status_code == 401
    assert (
        await client.get(f"/v1/investigations/{other_investigation}", headers=other_headers)
    ).status_code == 200
    with pytest.raises(PublishRejected):
        await queue.publish(claim.lease, {"private": "late"})
    await sweep(app)
    async with app.state.database.engine.connect() as connection:
        replay = (await connection.execute(select(jobs).where(jobs.c.id == queued.job_id))).one()
    assert replay == tombstone


@pytest.mark.parametrize("removed_first", [False, True])
async def test_storage_failure_leaves_retryable_database_record(
    privacy_app, client, monkeypatch, removed_first
):
    app = privacy_app
    _, upload_id, _, owner_id = await seed(app, client)
    await expire(app, owner_id)
    store = app.state.upload_store
    original = store.delete

    async def unavailable(key):
        if removed_first:
            await original(key)
        raise OSError("synthetic-private-storage-detail")

    monkeypatch.setattr(store, "delete", unavailable)
    with pytest.raises(OSError):
        await sweep(app)
    async with app.state.database.engine.connect() as connection:
        assert await connection.scalar(select(uploads.c.id).where(uploads.c.id == upload_id))
        assert await connection.scalar(select(principals.c.id).where(principals.c.id == owner_id))
    monkeypatch.setattr(store, "delete", original)
    await sweep(app)
    assert not list(app.state.settings.storage_dir.iterdir())


async def test_expired_pending_upload_removed_without_expiring_owner(privacy_app, client):
    app = privacy_app
    headers = await guest(client)
    upload = await declare_upload(client, headers)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(uploads)
            .where(uploads.c.id == uuid.UUID(upload["id"]))
            .values(expires_at=func.now())
        )
    counts = await sweep(app)
    assert counts["uploads"] >= 1
    response = await client.put(upload["target"], headers=headers, content=b"late")
    assert response.status_code == 404
    assert (await client.get("/v1/investigations", headers=headers)).status_code == 200


async def test_linked_account_survives_the_sweep_while_aged_guests_expire(database_url, tmp_path):
    """BC-D07 accounts carry the saved-report promise: the demo lifetime never expires them.

    Three principals age past the lifetime: a guest upgraded in place to an account, a
    second-device guest merged into that account (credential revoked, ``merged_into`` set)
    and a plain guest. Only the account, its credential and its objects survive.
    """
    verifier = FakeVerifier()
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            retention_enabled=True,
            job_lease_seconds=30,
            _env_file=None,
        ),
        id_token_verifier=verifier,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            account_headers, account_upload, account_investigation, account_id = await seed(
                app, client
            )
            linked = await client.post(LINK, json=link_body("token-alice"), headers=account_headers)
            assert linked.status_code == 200 and linked.json()["kind"] == "account", linked.text
            merged_headers, merged_upload, merged_investigation, merged_id = await seed(app, client)
            merged = await client.post(LINK, json=link_body("token-alice"), headers=merged_headers)
            assert merged.status_code == 200, merged.text
            assert uuid.UUID(merged.json()["principal_id"]) == account_id
            continued = {"Authorization": f"Bearer {merged.json()['credential']['token']}"}
            guest_headers, guest_upload, guest_investigation, guest_id = await seed(app, client)
            for principal_id in (account_id, merged_id, guest_id):
                await expire(app, principal_id)

            counts = await sweep(app)
            assert counts["principals"] == 2

            async with app.state.database.engine.connect() as connection:
                account = (
                    await connection.execute(
                        select(principals).where(principals.c.id == account_id)
                    )
                ).one()
                assert account.kind == "account"
                assert account.google_sub == verifier.tokens["token-alice"]
                live = (
                    await connection.execute(
                        select(credentials.c.id).where(
                            credentials.c.principal_id == account_id,
                            credentials.c.revoked_at.is_(None),
                        )
                    )
                ).all()
                assert len(live) == 2, "the original and the second-device credentials remain"
                assert await connection.scalar(
                    select(uploads.c.id).where(uploads.c.id == account_upload)
                )
                for table, clause in (
                    (principals, principals.c.id.in_([merged_id, guest_id])),
                    (credentials, credentials.c.principal_id.in_([merged_id, guest_id])),
                    (uploads, uploads.c.id.in_([merged_upload, guest_upload])),
                    (
                        investigations,
                        investigations.c.id.in_([merged_investigation, guest_investigation]),
                    ),
                ):
                    assert (await connection.execute(select(table).where(clause))).first() is None
            assert len(list(app.state.settings.storage_dir.iterdir())) == 1

            for headers in (account_headers, continued):
                response = await client.get(
                    f"/v1/investigations/{account_investigation}", headers=headers
                )
                assert response.status_code == 200, response.text
            for headers in (merged_headers, guest_headers):
                assert (await client.get("/v1/investigations", headers=headers)).status_code == 401

            # A second sweep finds nothing else to expire and leaves the account alone.
            assert (await sweep(app))["principals"] == 0
            async with app.state.database.engine.connect() as connection:
                assert await connection.scalar(
                    select(principals.c.id).where(principals.c.id == account_id)
                )


async def test_tombstone_purge_still_rejects_late_publication(privacy_app):
    app = privacy_app
    queue = JobQueue(app.state.database)
    stage = uuid.uuid4().hex
    async with app.state.database.engine.begin() as connection:
        job = await queue.enqueue(
            connection, StageKey(1, stage, uuid.uuid4().hex), {"private": "old"}
        )
        await connection.execute(
            update(jobs)
            .where(jobs.c.id == job.job_id)
            .values(created_at=func.now() - timedelta(days=2))
        )
    claim = await queue.claim("late", [stage], 30)
    await queue.start(claim.lease)
    await sweep(app)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs)
            .where(jobs.c.id == job.job_id)
            .values(updated_at=func.now() - timedelta(days=8))
        )
    await sweep(app)
    assert await queue.get(job.job_id) is None
    with pytest.raises(PublishRejected):
        await queue.publish(claim.lease, {"private": "late"})


async def test_scheduler_deduplicates_across_processes(privacy_app):
    app = privacy_app
    first, second = retention(app), retention(app)
    await asyncio.gather(first.schedule(), second.schedule())
    await first.schedule()
    async with app.state.database.engine.connect() as connection:
        rows = (await connection.execute(select(jobs).where(jobs.c.stage == RETENTION_STAGE))).all()
    assert rows
    assert len({row.input_hash for row in rows}) == len(rows)
    assert all(row.owner_id is None and row.payload == {} for row in rows)


async def test_worker_retention_races_with_delayed_callback(privacy_app, client):
    app = privacy_app
    _, _, _, owner_id = await seed(app, client)
    queue = JobQueue(app.state.database)
    entered, proceed, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    stage = uuid.uuid4().hex
    async with app.state.database.engine.begin() as connection:
        queued = await queue.enqueue(
            connection,
            StageKey(1, stage, uuid.uuid4().hex),
            {"synthetic": "content"},
            owner_id=owner_id,
        )

    async def delayed(job, context):
        await context.record_request_id("synthetic-request")
        entered.set()
        await proceed.wait()
        return {"synthetic": "late result"}

    worker = Worker(
        app.state.database, 2, handlers={stage: delayed}, lease_seconds=30, poll_seconds=0.01
    )
    service = retention(app)

    async def cleanup_handler(job, context):
        result = await service.run(job, context)
        cleaned.set()
        return result

    cleanup = Worker(
        app.state.database,
        2,
        handlers={RETENTION_STAGE: cleanup_handler},
        maintenance=service.schedule,
        lease_seconds=30,
        poll_seconds=0.01,
    )
    try:
        await worker.start()
        await asyncio.wait_for(entered.wait(), 3)
        await expire(app, owner_id)
        await cleanup.start()
        await asyncio.wait_for(cleaned.wait(), 5)
        assert (await queue.get(queued.job_id)).status.state.value == "deleted"
        proceed.set()
        await worker.stop()
        async with app.state.database.engine.connect() as connection:
            assert (
                await connection.scalar(
                    select(job_results.c.job_id).where(job_results.c.job_id == queued.job_id)
                )
                is None
            )
    finally:
        proceed.set()
        await worker.stop()
        await cleanup.stop()


async def test_cleanup_waits_for_upload_write_before_deleting_bytes(
    privacy_app, client, monkeypatch
):
    app = privacy_app
    headers, _, _, owner_id = await seed(app, client)
    pending = await declare_upload(client, headers)
    await expire(app, owner_id)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = app.state.upload_store.write

    async def delayed_write(*args):
        entered.set()
        await proceed.wait()
        return await original(*args)

    monkeypatch.setattr(app.state.upload_store, "write", delayed_write)
    writer = asyncio.create_task(client.put(pending["target"], headers=headers, content=CONTENT))
    await asyncio.wait_for(entered.wait(), 3)
    cleanup = asyncio.create_task(sweep(app))
    try:
        await asyncio.sleep(0.05)
        assert not cleanup.done()
    finally:
        proceed.set()
    response, _ = await asyncio.wait_for(asyncio.gather(writer, cleanup), 5)
    assert response.status_code == 204
    assert not list(app.state.settings.storage_dir.iterdir())
    assert (
        await client.put(pending["target"], headers=headers, content=CONTENT)
    ).status_code == 401


async def test_concurrent_sweeps_are_idempotent(privacy_app, client):
    app = privacy_app
    _, _, _, owner_id = await seed(app, client)
    await expire(app, owner_id)
    # Each sweep uses a separate engine, as independent worker processes do.
    from services.database import Database

    second_database = Database(app.state.settings)
    second_app = create_app(app.state.settings)
    second_app.state.database = second_database
    second_app.state.upload_store = app.state.upload_store
    try:
        counts = await asyncio.gather(sweep(app), sweep(second_app))
        assert sum(result["principals"] for result in counts) == 1
        assert not list(app.state.settings.storage_dir.iterdir())
    finally:
        await second_database.close()


async def test_embedded_retention_is_registered_and_disabled_by_default(database_url, tmp_path):
    for enabled in (False, True):
        app = create_app(
            Settings(
                database_url=database_url,
                _env_file=None,
                embed_worker=True,
                retention_enabled=enabled,
                storage_dir=tmp_path,
            )
        )
        async with app.router.lifespan_context(app):
            assert (RETENTION_STAGE in app.state.worker.handlers) is enabled
            assert (app.state.worker.maintenance is not None) is enabled


async def test_api_logs_do_not_copy_body_url_or_request_id(privacy_app, client, caplog):
    import logging

    from services.logging import request_log_id

    caplog.set_level(logging.INFO)
    marker = "synthetic-private-request"
    headers = await guest(client)
    response = await client.post(
        "/v1/investigations",
        headers={**headers, "Idempotency-Key": marker, "X-Request-Id": marker},
        json={"source": {"kind": "url", "url": f"https://example.com/{marker}"}},
    )
    assert response.status_code == 202
    assert marker not in caplog.text
    assert str(request_log_id(marker)) in caplog.text


async def test_standalone_registers_retention_and_stops_cleanly(
    database_url, tmp_path, monkeypatch
):
    from services.worker import __main__ as standalone

    settings = Settings(
        database_url=database_url,
        _env_file=None,
        retention_enabled=True,
        storage_dir=tmp_path,
        worker_shutdown_seconds=5,
    )
    monkeypatch.setattr(standalone, "load_settings", lambda: settings)
    started = []
    original = Worker.start

    async def start_and_stop(worker):
        await original(worker)
        started.append(worker)
        worker.request_stop()

    monkeypatch.setattr(Worker, "start", start_and_stop)
    await standalone.serve()
    assert len(started) == 1
    worker = started[0]
    assert RETENTION_STAGE in worker.handlers
    assert worker.maintenance is not None
    assert not worker.running
    assert not worker.owned_leases
