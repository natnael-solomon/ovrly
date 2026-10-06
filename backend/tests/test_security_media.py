"""Media intake fuzz smoke (REPO-06, #28).

The codec/ffmpeg media-validation stage that will answer malformed or oversized media
with ``MEDIA_INVALID`` does not exist yet: it is blocked by #20 (BE-07), and
``test_media_invalid_from_the_codec_stage`` is skipped with that reason. Until then this
smoke covers the limits that exist today: upload declarations, streamed bodies,
compressed ("zip-bomb-like") bodies, capture multipart parsing and the worker's
``media_validation`` byte check. Each malformed input gets a typed, safe error, nothing
expands in memory or on disk, and the worker keeps processing after a corrupt chunk.
"""

import asyncio
import gzip
import hashlib
import json
import random
import uuid
from datetime import timedelta

import httpx
import pytest
from recovery.test_captures import create, parts, put
from sqlalchemy import func, select, update
from test_intake_api import guest
from test_security_errors import assert_safe_error

from services.api.main import create_app
from services.captures import CAPTURE_STAGE
from services.jobs.handlers import default_handlers
from services.jobs.models import jobs
from services.models import capture_chunks
from services.settings import Settings
from services.worker.runtime import Worker

LIMIT = 1024
RNG = random.Random(28)  # noqa: S311 - deterministic fuzz data, not security randomness


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "media",
            upload_max_bytes=LIMIT,
            _env_file=None,
        )
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.skip(reason="Blocked by #20: no codec/ffmpeg stage emits MEDIA_INVALID yet")
def test_media_invalid_from_the_codec_stage():
    """Placeholder for #20: malformed containers, truncated streams and decompression
    bombs must fail ``media_validation`` with ``MEDIA_INVALID`` while the worker survives."""


DECLARATIONS = [
    ({"size_bytes": LIMIT + 1, "sha256": "0" * 64}, 413, "UPLOAD_TOO_LARGE"),
    ({"size_bytes": 2**63, "sha256": "0" * 64}, 413, "UPLOAD_TOO_LARGE"),
    ({"size_bytes": 0, "sha256": "0" * 64}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": -1, "sha256": "0" * 64}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": "12", "sha256": "0" * 64}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": 1.5, "sha256": "0" * 64}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": 10, "sha256": "Z" * 64}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": 10, "sha256": "0" * 63}, 422, "VALIDATION_FAILED"),
    ({"size_bytes": 10, "sha256": "0" * 64, "content_type": "x" * 4096}, 422, "VALIDATION_FAILED"),
    (
        {"size_bytes": 10, "sha256": "0" * 64, "content_type": "video/mp4\r\nX-Injected: 1"},
        None,
        None,
    ),
    ({"size_bytes": 10}, 422, "VALIDATION_FAILED"),
    ([], 422, "VALIDATION_FAILED"),
]


@pytest.mark.parametrize(("body", "status", "code"), DECLARATIONS)
async def test_malformed_upload_declarations_get_typed_errors(client, body, status, code):
    headers = await guest(client)
    response = await client.post("/v1/uploads", json=body, headers=headers)
    if status is None:
        # Opaque metadata is stored as data and never reflected into a header.
        assert response.status_code == 201, response.text
        assert "x-injected" not in response.headers
        return
    assert_safe_error(response, status, code)


async def test_malformed_json_and_huge_bodies_are_refused(client):
    headers = await guest(client)
    for content in (b"{", b"\xff\xfe\x00", b"[" * 10_000 + b"]" * 10_000, b"{}" * 100_000):
        response = await client.post(
            "/v1/uploads",
            content=content,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert_safe_error(response, 422, "VALIDATION_FAILED")


async def declare(client, headers, data, declared=None):
    response = await client.post(
        "/v1/uploads",
        json={
            "size_bytes": declared if declared is not None else len(data),
            "sha256": sha(data),
            "content_type": "video/mp4",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_streamed_bytes_stop_at_the_limit_and_leave_nothing(client, app, tmp_path):
    headers = await guest(client)
    big = RNG.randbytes(LIMIT * 4)
    upload = await declare(client, headers, big[:LIMIT])

    async def stream():
        for start in range(0, len(big), 256):
            yield big[start : start + 256]

    response = await client.put(upload["target"], content=stream(), headers=headers)
    assert_safe_error(response, 413, "UPLOAD_TOO_LARGE")
    assert list((tmp_path / "media").glob("*")) == []
    done = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert_safe_error(done, 409, "UPLOAD_CONTENT_MISSING")


async def test_compressed_bodies_are_stored_raw_never_expanded(client, tmp_path):
    """A gzip "bomb" is just its compressed bytes: the server never decodes request bodies."""
    headers = await guest(client)
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024), compresslevel=9)
    assert len(bomb) < LIMIT < 64 * 1024 * 1024
    upload = await declare(client, headers, bomb)
    response = await client.put(
        upload["target"],
        content=bomb,
        headers={**headers, "Content-Encoding": "gzip", "Content-Type": "video/mp4"},
    )
    assert response.status_code == 204, response.text
    stored = list((tmp_path / "media").glob("*"))
    assert len(stored) == 1 and stored[0].stat().st_size == len(bomb)
    done = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert done.status_code == 200 and done.json()["state"] == "completed"


@pytest.mark.parametrize(
    "garbage",
    [
        b"",
        b"\x00\x00\x00\x18ftypmp42",  # truncated MP4 header
        b"PK\x03\x04" + b"\xff" * 64,  # zip local header with junk
        b"\x1f\x8b\x08" + b"\x00" * 16,  # truncated gzip
        bytes(range(256)),
    ],
    ids=["empty", "truncated-mp4", "zip-junk", "truncated-gzip", "all-bytes"],
)
async def test_bytes_that_differ_from_their_declaration_are_discarded(client, tmp_path, garbage):
    headers = await guest(client)
    declared = b"declared synthetic media"
    upload = await declare(client, headers, declared)
    response = await client.put(upload["target"], content=garbage, headers=headers)
    assert response.status_code == 204, response.text
    done = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    code = "UPLOAD_CONTENT_MISSING" if not garbage else "UPLOAD_MISMATCH"
    assert_safe_error(done, 409, code)
    assert [p for p in (tmp_path / "media").glob("*") if p.stat().st_size] == []


async def test_capture_multipart_fuzz_gets_typed_errors(client):
    headers = await guest(client)
    session = await create(client, headers)
    url = f"/v1/captures/{session['id']}/chunks/0"
    metadata, data = parts(session, 0, b"chunk")
    cases = [
        ({"content": b"not multipart"}, 422, "VALIDATION_FAILED"),
        ({"files": {"content": ("c", data)}}, 422, "VALIDATION_FAILED"),
        ({"files": {"metadata": (None, "{not json"), "content": ("c", data)}}, 422, None),
        (
            {"files": {"metadata": (None, "x" * 9000), "content": ("c", data)}},
            422,
            "VALIDATION_FAILED",
        ),
        (
            {
                "files": {
                    "metadata": (None, json.dumps(metadata)),
                    "content": (None, "text, not a file"),
                }
            },
            422,
            "VALIDATION_FAILED",
        ),
        (
            {
                "files": {
                    "metadata": (None, json.dumps(metadata)),
                    "content": ("c", data),
                    "extra": ("e", b"x"),
                }
            },
            422,
            "VALIDATION_FAILED",
        ),
    ]
    for kwargs, status, code in cases:
        response = await client.put(url, headers=headers, **kwargs)
        assert_safe_error(response, status, code)
    oversized = RNG.randbytes(LIMIT + 1)
    assert_safe_error(await put(client, headers, session, data=oversized), 413, "CAPTURE_TOO_LARGE")
    lying, _ = parts(session, 0, b"declared")
    assert_safe_error(
        await put(client, headers, session, metadata=lying, data=b"different"),
        409,
        "CAPTURE_CHUNK_CONFLICT",
    )
    # After every refusal the session still accepts a well-formed chunk.
    assert (await put(client, headers, session)).status_code == 200


async def test_worker_survives_a_corrupt_chunk_and_keeps_validating(client, app, tmp_path):
    headers = await guest(client)
    session = await create(client, headers)
    assert (await put(client, headers, session, 0)).status_code == 200
    assert (await put(client, headers, session, 1)).status_code == 200
    capture_id = uuid.UUID(session["id"])
    async with app.state.database.engine.begin() as connection:
        rows = (
            await connection.execute(
                select(capture_chunks.c.seq, capture_chunks.c.job_id, capture_chunks.c.storage_key)
                .where(capture_chunks.c.session_id == capture_id)
                .order_by(capture_chunks.c.seq)
            )
        ).all()
        # Claim these jobs before any other test's leftovers.
        await connection.execute(
            update(jobs)
            .where(jobs.c.id.in_([row.job_id for row in rows]))
            .values(available_at=func.now() - timedelta(days=365))
        )
    corrupt = tmp_path / "media" / rows[0].storage_key
    corrupt.write_bytes(b"\x00" * corrupt.stat().st_size)
    worker = Worker(
        app.state.database, 2, handlers=default_handlers(app.state.upload_store), poll_seconds=0.05
    )
    await worker.start()
    try:
        for _ in range(200):
            async with app.state.database.engine.connect() as connection:
                states = {
                    row.id: (row.state, row.retry_class)
                    for row in await connection.execute(
                        select(jobs.c.id, jobs.c.state, jobs.c.retry_class).where(
                            jobs.c.id.in_([row.job_id for row in rows])
                        )
                    )
                }
            if all(state in {"failed", "published"} for state, _ in states.values()):
                break
            await asyncio.sleep(0.05)
        assert worker.running
    finally:
        await worker.stop()
    assert states[rows[0].job_id] == ("failed", "non_retriable_input")
    assert states[rows[1].job_id][0] == "published"
    polled = await client.get(f"/v1/captures/{session['id']}", headers=headers)
    assert polled.status_code == 200
    failed = next(item for item in polled.json()["work"] if item["seq"] == 0)
    assert failed["error"]["code"] == "PROCESSING_FAILED"
    assert "corrupt" not in json.dumps(polled.json())
    assert CAPTURE_STAGE == "media_validation"
