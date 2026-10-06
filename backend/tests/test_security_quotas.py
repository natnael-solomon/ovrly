"""Quota abuse against the opt-in #22 admission quotas (REPO-06, #28).

Policy is unchanged here; these tests only attack it: one admission more than the
active-check limit at once, oversize uploads, and replay storms of one idempotency key.
Every refusal is a typed, safe error and nothing is charged or created twice.
"""

import asyncio
import hashlib

import httpx
import pytest
from sqlalchemy import func, select, update
from test_intake_api import URL_BODY, guest
from test_reports_api import publish
from test_security_errors import assert_safe_error

from services.api.main import create_app
from services.jobs.models import jobs
from services.jobs.queue import JobQueue
from services.models import (
    capture_sessions,
    credentials,
    investigations,
    quota_usage,
    reanalysis_requests,
)
from services.settings import Settings

ACTIVE = 2
DAILY_BYTES = 100
MAX_UPLOAD = 150
STORM = 20


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            quotas_enabled=True,
            quota_active_checks=ACTIVE,
            quota_daily_checks=10,
            quota_daily_upload_bytes=DAILY_BYTES,
            upload_max_bytes=MAX_UPLOAD,
            _env_file=None,
        )
    )
    application.state.owners = set()
    async with application.router.lifespan_context(application):
        yield application
        # Leave no queued work for another test's worker.
        async with application.state.database.engine.begin() as connection:
            rows = await connection.execute(
                select(jobs.c.id).where(jobs.c.owner_id.in_(application.state.owners))
            )
            for job_id in rows.scalars().all():
                await JobQueue(application.state.database).delete(job_id, connection=connection)


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


async def principal(client, app):
    headers = await guest(client)
    token = headers["Authorization"].split(" ", 1)[1]
    async with app.state.database.engine.connect() as connection:
        owner_id = await connection.scalar(
            select(credentials.c.principal_id).where(
                credentials.c.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
    app.state.owners.add(owner_id)
    return headers, owner_id


async def counted(app, table, owner_id):
    async with app.state.database.engine.connect() as connection:
        return await connection.scalar(
            select(func.count()).select_from(table).where(table.c.owner_id == owner_id)
        )


async def clear_active(app, owner_id):
    """Finish the owner's work so the active limit does not mask the replay checks."""
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(jobs).where(jobs.c.owner_id == owner_id).values(state="cancelled")
        )


async def usage(app, owner_id):
    async with app.state.database.engine.connect() as connection:
        return (
            await connection.execute(select(quota_usage).where(quota_usage.c.owner_id == owner_id))
        ).one_or_none()


async def test_n_plus_one_concurrent_checks_admit_exactly_the_limit(client, app):
    headers, owner_id = await principal(client, app)
    responses = await asyncio.gather(
        *(
            client.post(
                "/v1/investigations",
                json=URL_BODY,
                headers={**headers, "Idempotency-Key": f"burst-{index}"},
            )
            for index in range(ACTIVE + 1)
        )
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [202] * ACTIVE + [429]
    denied = next(r for r in responses if r.status_code == 429)
    assert_safe_error(denied, 429, "QUOTA_EXCEEDED", "retry")
    assert int(denied.headers["Retry-After"]) > 0
    assert await counted(app, investigations, owner_id) == ACTIVE
    assert await counted(app, jobs, owner_id) == ACTIVE
    assert (await usage(app, owner_id)).checks == ACTIVE
    # A capture is a check too: the limit holds across kinds.
    capture = await client.post(
        "/v1/captures", json={}, headers={**headers, "Idempotency-Key": "burst-capture"}
    )
    assert_safe_error(capture, 429, "QUOTA_EXCEEDED", "retry")
    assert await counted(app, capture_sessions, owner_id) == 0
    assert (await usage(app, owner_id)).checks == ACTIVE


async def test_oversize_uploads_are_refused_without_a_charge(client, app):
    headers, owner_id = await principal(client, app)

    async def declare(size, data=b""):
        return await client.post(
            "/v1/uploads",
            json={
                "size_bytes": size,
                "sha256": hashlib.sha256(data or b"x" * size).hexdigest(),
                "content_type": "video/mp4",
            },
            headers=headers,
        )

    assert_safe_error(await declare(MAX_UPLOAD + 1), 413, "UPLOAD_TOO_LARGE", "fix_request")
    assert_safe_error(await declare(DAILY_BYTES + 1), 429, "QUOTA_EXCEEDED", "retry")
    assert await usage(app, owner_id) is None or (await usage(app, owner_id)).upload_bytes == 0
    # Concurrent declarations cannot overspend the shared daily byte budget.
    half = DAILY_BYTES // 2
    raced = await asyncio.gather(*(declare(half) for _ in range(3)))
    assert sorted(r.status_code for r in raced) == [201, 201, 429]
    assert (await usage(app, owner_id)).upload_bytes == 2 * half
    # Streaming more than declared stops at the declaration; nothing more is charged.
    target = next(r for r in raced if r.status_code == 201).json()
    sent = await client.put(target["target"], content=b"y" * (half * 3), headers=headers)
    assert_safe_error(sent, 413, "UPLOAD_TOO_LARGE")
    assert (await usage(app, owner_id)).upload_bytes == 2 * half


async def test_idempotency_replay_storm_creates_and_charges_once(client, app):
    headers, owner_id = await principal(client, app)
    replay = {**headers, "Idempotency-Key": "storm"}
    responses = await asyncio.gather(
        *(client.post("/v1/investigations", json=URL_BODY, headers=replay) for _ in range(STORM))
    )
    assert {r.status_code for r in responses} == {202}
    assert len({r.text for r in responses}) == 1
    assert await counted(app, investigations, owner_id) == 1
    assert await counted(app, jobs, owner_id) == 1
    assert (await usage(app, owner_id)).checks == 1
    # The same key with another body is refused, also uncharged.
    other = {"source": {"kind": "url", "url": "https://example.com/other"}}
    reused = await asyncio.gather(
        *(client.post("/v1/investigations", json=other, headers=replay) for _ in range(5))
    )
    for response in reused:
        assert_safe_error(response, 409, "IDEMPOTENCY_KEY_REUSED")
    assert (await usage(app, owner_id)).checks == 1

    await clear_active(app, owner_id)
    investigation = responses[0].json()["id"]
    await publish(app, investigation)
    reanalyses = await asyncio.gather(
        *(
            client.post(
                f"/v1/investigations/{investigation}/reanalyze",
                json={"reason": "deeper", "base_version": 1},
                headers={**headers, "Idempotency-Key": "again"},
            )
            for _ in range(STORM)
        )
    )
    assert {r.status_code for r in reanalyses} == {202}
    assert len({r.text for r in reanalyses}) == 1
    assert await counted(app, reanalysis_requests, owner_id) == 1
    assert (await usage(app, owner_id)).checks == 2

    await clear_active(app, owner_id)
    captures = await asyncio.gather(
        *(
            client.post("/v1/captures", json={}, headers={**headers, "Idempotency-Key": "cap"})
            for _ in range(STORM)
        )
    )
    assert {r.status_code for r in captures} == {201}
    assert len({r.text for r in captures}) == 1
    assert await counted(app, capture_sessions, owner_id) == 1
    assert (await usage(app, owner_id)).checks == 3
