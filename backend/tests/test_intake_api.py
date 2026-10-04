import asyncio
import hashlib
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from services.api.main import create_app
from services.models import credentials, idempotency_keys, investigations, uploads
from services.settings import Settings

ERROR_FIELDS = {"code", "message", "retryable", "action", "request_id"}
ERROR_SCHEMA = "error.schema.json"
MAX_BYTES = 64
CONTENT = b"synthetic video bytes for the intake test"
CONTENT_SHA = hashlib.sha256(CONTENT).hexdigest()
URL_BODY = {"source": {"kind": "url", "url": "https://example.com/watch?v=1"}}


def _contracts_validator():
    """The stdlib-only validator from packages/contracts, loaded by path."""
    path = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "validate.py"
    spec = importlib.util.spec_from_file_location("ovrly_contracts_validate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Validator(), module.Invalid


CONTRACT_VALIDATOR, ContractInvalid = _contracts_validator()


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            upload_max_bytes=MAX_BYTES,
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


async def guest(client) -> dict[str, str]:
    response = await client.post("/v1/principals/guest")
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['credential']['token']}"}


def assert_error(response, status, code, action=None):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == ERROR_FIELDS
    assert body["code"] == code
    assert body["request_id"] == response.headers["X-Request-Id"]
    if action is not None:
        assert body["action"] == action
    # Every error produced by the API must satisfy the shared contract schema.
    CONTRACT_VALIDATOR.validate(body, ERROR_SCHEMA)
    return body


async def declare_upload(client, headers, content=CONTENT, sha256=None, size=None):
    response = await client.post(
        "/v1/uploads",
        json={
            "size_bytes": size if size is not None else len(content),
            "sha256": sha256 or hashlib.sha256(content).hexdigest(),
            "content_type": "video/mp4",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def completed_upload(client, headers):
    upload = await declare_upload(client, headers)
    put = await client.put(upload["target"], content=CONTENT, headers=headers)
    assert put.status_code == 204, put.text
    done = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "completed"
    return done.json()


async def test_guest_principal_mints_opaque_credential(client, app, caplog):
    response = await client.post("/v1/principals/guest", json={})
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "guest"
    assert set(body["credential"]) == {"token", "token_type"}
    assert "bearer" in body["credential"].values()
    token = body["credential"]["token"]
    assert token.startswith("ovk_") and len(token) > 40
    assert token not in caplog.text
    async with app.state.database.engine.connect() as connection:
        stored = (await connection.execute(select(credentials.c.token_hash))).scalars().all()
    assert token not in stored
    assert hashlib.sha256(token.encode()).hexdigest() in stored
    second = await client.post("/v1/principals/guest")
    assert second.json()["principal_id"] != body["principal_id"]
    assert second.json()["credential"]["token"] != token


@pytest.mark.parametrize(
    "kwargs,status",
    [
        ({"json": {"user_id": "someone-else"}}, 422),
        ({"params": {"user_id": "someone-else"}}, 400),
        ({"headers": {"X-User-Id": "someone-else"}}, 400),
    ],
)
async def test_client_supplied_identity_is_rejected_everywhere(client, kwargs, status):
    assert_error(
        await client.post("/v1/principals/guest", **kwargs),
        status,
        "CLIENT_IDENTITY_REJECTED",
        "fix_request",
    )
    headers = await guest(client)
    merged = {**kwargs, "headers": {**headers, **kwargs.get("headers", {})}}
    if "json" in merged:
        merged["json"] = {**URL_BODY, **merged["json"]}
        merged["headers"]["Idempotency-Key"] = "identity-check"
    assert_error(
        await client.post("/v1/investigations", **merged), status, "CLIENT_IDENTITY_REJECTED"
    )
    listing = await client.get("/v1/investigations", params={"owner_id": "x"}, headers=headers)
    assert_error(listing, 400, "CLIENT_IDENTITY_REJECTED")


async def test_missing_or_invalid_bearer(client, app):
    missing = await client.get("/v1/investigations")
    assert_error(missing, 401, "AUTHENTICATION_REQUIRED", "authenticate")
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    for header in ("Basic abc", "Bearer", "Bearer  ", "Bearer two tokens", "ovk_raw"):
        assert_error(
            await client.get("/v1/investigations", headers={"Authorization": header}),
            401,
            "INVALID_CREDENTIAL",
            "authenticate",
        )
    assert_error(
        await client.get("/v1/investigations", headers={"Authorization": "Bearer ovk_unknown"}),
        401,
        "INVALID_CREDENTIAL",
    )
    headers = await guest(client)
    assert (await client.get("/v1/investigations", headers=headers)).status_code == 200
    async with app.state.database.engine.begin() as connection:
        await connection.execute(update(credentials).values(revoked_at=datetime.now(UTC)))
    assert_error(await client.get("/v1/investigations", headers=headers), 401, "INVALID_CREDENTIAL")


async def test_upload_lifecycle_and_idempotent_completion(client, app, tmp_path):
    headers = await guest(client)
    upload = await declare_upload(client, headers)
    assert upload["state"] == "pending"
    assert upload["max_bytes"] == MAX_BYTES
    assert upload["target"] == f"/v1/uploads/{upload['id']}/content"
    expires = datetime.fromisoformat(upload["expires_at"])
    assert timedelta(minutes=14) < expires - datetime.now(UTC) <= timedelta(minutes=15)
    assert_error(
        await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers),
        409,
        "UPLOAD_CONTENT_MISSING",
        "upload_again",
    )
    put = await client.put(upload["target"], content=CONTENT, headers=headers)
    assert put.status_code == 204
    stored = list((tmp_path / "uploads").iterdir())
    assert len(stored) == 1 and stored[0].read_bytes() == CONTENT
    first = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert first.status_code == 200 and first.json()["state"] == "completed"
    again = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert again.status_code == 200 and again.json() == first.json()
    assert_error(
        await client.put(upload["target"], content=CONTENT, headers=headers),
        409,
        "UPLOAD_ALREADY_COMPLETED",
    )


async def test_upload_byte_limits(client):
    headers = await guest(client)
    too_big = await client.post(
        "/v1/uploads",
        json={"size_bytes": MAX_BYTES + 1, "sha256": CONTENT_SHA},
        headers=headers,
    )
    assert_error(too_big, 413, "UPLOAD_TOO_LARGE", "fix_request")
    assert str(MAX_BYTES) in too_big.json()["message"]
    upload = await declare_upload(client, headers)
    oversized = CONTENT + b"!"
    assert_error(
        await client.put(upload["target"], content=oversized, headers=headers),
        413,
        "UPLOAD_TOO_LARGE",
    )

    async def stream():
        yield CONTENT
        yield b"!"

    streamed = await client.put(
        upload["target"], content=stream(), headers={**headers, "Transfer-Encoding": "chunked"}
    )
    assert_error(streamed, 413, "UPLOAD_TOO_LARGE")
    assert_error(
        await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers),
        409,
        "UPLOAD_CONTENT_MISSING",
    )


async def test_upload_hash_and_size_mismatch_rejected(client, tmp_path):
    headers = await guest(client)
    wrong_hash = await declare_upload(client, headers, sha256="0" * 64)
    assert (
        await client.put(wrong_hash["target"], content=CONTENT, headers=headers)
    ).status_code == 204
    assert_error(
        await client.post(f"/v1/uploads/{wrong_hash['id']}/complete", headers=headers),
        409,
        "UPLOAD_MISMATCH",
        "upload_again",
    )
    assert list((tmp_path / "uploads").iterdir()) == []
    short = await declare_upload(client, headers, size=len(CONTENT) + 1)
    assert (await client.put(short["target"], content=CONTENT, headers=headers)).status_code == 204
    assert_error(
        await client.post(f"/v1/uploads/{short['id']}/complete", headers=headers),
        409,
        "UPLOAD_MISMATCH",
    )


async def test_expired_upload_target(client, app):
    headers = await guest(client)
    upload = await declare_upload(client, headers)
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(uploads).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert_error(
        await client.put(upload["target"], content=CONTENT, headers=headers),
        410,
        "UPLOAD_EXPIRED",
        "upload_again",
    )
    assert_error(
        await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers),
        410,
        "UPLOAD_EXPIRED",
    )


async def test_cross_owner_access_is_not_found(client):
    owner = await guest(client)
    other = await guest(client)
    upload = await completed_upload(client, owner)
    created = await client.post(
        "/v1/investigations",
        json={"source": {"kind": "upload", "upload_id": upload["id"]}},
        headers={**owner, "Idempotency-Key": "owner-key"},
    )
    assert created.status_code == 202, created.text
    investigation_id = created.json()["id"]
    for response in (
        await client.put(upload["target"], content=CONTENT, headers=other),
        await client.post(f"/v1/uploads/{upload['id']}/complete", headers=other),
        await client.get(f"/v1/investigations/{investigation_id}", headers=other),
        await client.post(
            "/v1/investigations",
            json={"source": {"kind": "upload", "upload_id": upload["id"]}},
            headers={**other, "Idempotency-Key": "other-key"},
        ),
        await client.get(f"/v1/investigations/{uuid.uuid4()}", headers=owner),
    ):
        assert_error(response, 404, "NOT_FOUND")
    listing = await client.get("/v1/investigations", headers=other)
    assert listing.json() == {"items": []}
    mine = await client.get("/v1/investigations", headers=owner)
    assert [item["id"] for item in mine.json()["items"]] == [investigation_id]


async def test_investigation_is_durable_before_202_and_replays(client, app):
    headers = await guest(client)
    assert_error(
        await client.post("/v1/investigations", json=URL_BODY, headers=headers),
        400,
        "IDEMPOTENCY_KEY_REQUIRED",
        "fix_request",
    )
    assert_error(
        await client.post(
            "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": "k" * 201}
        ),
        400,
        "IDEMPOTENCY_KEY_INVALID",
    )
    key = {**headers, "Idempotency-Key": "share-1"}
    first = await client.post("/v1/investigations", json=URL_BODY, headers=key)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["state"] == "queued" and body["stage"] == "intake" and body["version"] == 1
    assert body["coverage"] == {"status": "not_started"} and body["error"] is None
    assert body["source"] == {"kind": "url", "url": "https://example.com/watch?v=1"}
    async with app.state.database.engine.connect() as connection:
        row = (
            await connection.execute(
                select(investigations).where(investigations.c.id == uuid.UUID(body["id"]))
            )
        ).one()
        keys = (
            await connection.execute(
                select(idempotency_keys).where(idempotency_keys.c.owner_id == row.owner_id)
            )
        ).all()
    assert row.state == "queued" and str(row.owner_id) != ""
    assert len(keys) == 1 and keys[0].response_status == 202
    replay = await client.post("/v1/investigations", json=URL_BODY, headers=key)
    assert replay.status_code == 202 and replay.json() == body
    equivalent = await client.post(
        "/v1/investigations",
        content=json.dumps({"source": {"url": URL_BODY["source"]["url"], "kind": "url"}}),
        headers={**key, "Content-Type": "application/json"},
    )
    assert equivalent.status_code == 202 and equivalent.json() == body
    conflict = await client.post(
        "/v1/investigations",
        json={"source": {"kind": "url", "url": "https://example.com/other"}},
        headers=key,
    )
    assert_error(conflict, 409, "IDEMPOTENCY_KEY_REUSED", "fix_request")
    fetched = await client.get(f"/v1/investigations/{body['id']}", headers=headers)
    assert fetched.status_code == 200 and fetched.json() == body
    other_owner = await guest(client)
    reused_by_other = await client.post(
        "/v1/investigations",
        json=URL_BODY,
        headers={**other_owner, "Idempotency-Key": "share-1"},
    )
    assert reused_by_other.status_code == 202 and reused_by_other.json()["id"] != body["id"]


async def test_upload_source_requires_completed_owned_upload(client):
    headers = await guest(client)
    pending = await declare_upload(client, headers)
    assert_error(
        await client.post(
            "/v1/investigations",
            json={"source": {"kind": "upload", "upload_id": pending["id"], "duration_ms": 1000}},
            headers={**headers, "Idempotency-Key": "pending"},
        ),
        409,
        "UPLOAD_INCOMPLETE",
        "upload_again",
    )
    upload = await completed_upload(client, headers)
    created = await client.post(
        "/v1/investigations",
        json={"source": {"kind": "upload", "upload_id": upload["id"], "duration_ms": 1000}},
        headers={**headers, "Idempotency-Key": "completed"},
    )
    assert created.status_code == 202
    assert created.json()["source"] == {
        "kind": "upload",
        "upload_id": upload["id"],
        "duration_ms": 1000,
    }


async def test_duration_limit_from_settings(client):
    headers = {**(await guest(client)), "Idempotency-Key": "long"}
    too_long = {"source": {"kind": "url", "url": "https://example.com/a", "duration_ms": 600_001}}
    response = await client.post("/v1/investigations", json=too_long, headers=headers)
    assert_error(response, 422, "DURATION_LIMIT_EXCEEDED", "fix_request")
    assert "600000" in response.json()["message"]
    exact = {"source": {"kind": "url", "url": "https://example.com/a", "duration_ms": 600_000}}
    assert (await client.post("/v1/investigations", json=exact, headers=headers)).status_code == 202


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"source": {"kind": "stream", "url": "https://example.com"}},
        {"source": {"kind": "url", "url": "not a url"}},
        {"source": {"kind": "upload", "upload_id": "not-a-uuid"}},
        {"source": {"kind": "url", "url": "https://example.com", "extra": 1}},
        {"source": {"kind": "url", "url": "https://example.com", "duration_ms": 0}},
    ],
)
async def test_malformed_bodies_use_shared_error_shape(client, body):
    headers = {**(await guest(client)), "Idempotency-Key": "malformed"}
    response = await client.post("/v1/investigations", json=body, headers=headers)
    error = assert_error(response, 422, "VALIDATION_FAILED", "fix_request")
    assert "pydantic" not in error["message"].lower()
    assert "https://example.com" not in error["message"]
    raw = await client.post("/v1/investigations", content=b"{not json", headers=headers)
    assert_error(raw, 422, "VALIDATION_FAILED")
    bad_upload = await client.post("/v1/uploads", json={"size_bytes": 0}, headers=headers)
    assert_error(bad_upload, 422, "VALIDATION_FAILED")


async def test_safe_error_field_and_list_order(client, app):
    headers = await guest(client)
    ids = []
    for index in range(3):
        response = await client.post(
            "/v1/investigations",
            json={"source": {"kind": "url", "url": f"https://example.com/{index}"}},
            headers={**headers, "Idempotency-Key": f"order-{index}"},
        )
        ids.append(response.json()["id"])
    async with app.state.database.engine.begin() as connection:
        await connection.execute(
            update(investigations)
            .where(investigations.c.id == uuid.UUID(ids[0]))
            .values(state="failed", error_code="SOURCE_UNAVAILABLE")
        )
    listing = await client.get("/v1/investigations", headers=headers)
    items = listing.json()["items"]
    assert [item["id"] for item in items] == list(reversed(ids))
    failed = items[-1]
    assert failed["state"] == "failed"
    assert failed["error"] == {
        "code": "SOURCE_UNAVAILABLE",
        "message": "Processing failed",
        "retryable": False,
    }


async def test_dispatcher_runs_in_the_same_transaction(database_url, tmp_path):
    seen = []

    class RecordingDispatcher:
        async def dispatch(self, connection, investigation_id):
            row = (
                await connection.execute(
                    select(investigations).where(investigations.c.id == investigation_id)
                )
            ).one()
            seen.append(row.id)
            if len(seen) == 2:
                raise RuntimeError("dispatch failed")

    app = create_app(Settings(database_url=database_url, storage_dir=tmp_path, _env_file=None))
    app.state.dispatcher = RecordingDispatcher()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            headers = await guest(client)
            first = await client.post(
                "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": "a"}
            )
            assert first.status_code == 202 and seen == [uuid.UUID(first.json()["id"])]
            failed = await client.post(
                "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": "b"}
            )
            assert_error(failed, 500, "INTERNAL_ERROR")
            assert "dispatch failed" not in failed.text
            listing = await client.get("/v1/investigations", headers=headers)
            assert [item["id"] for item in listing.json()["items"]] == [first.json()["id"]]


async def test_unknown_routes_and_methods_use_the_error_shape(client):
    assert_error(await client.get("/v1/missing"), 404, "NOT_FOUND")
    assert_error(await client.delete("/v1/principals/guest"), 405, "METHOD_NOT_ALLOWED")
    tagged = await client.get("/v1/missing", headers={"X-Request-Id": "client-trace-1"})
    assert tagged.json()["request_id"] == "client-trace-1"
    for bad in ("bad id\n", "-leading-hyphen", "x" * 129, ""):
        rejected = await client.get("/v1/missing", headers={"X-Request-Id": bad})
        assert rejected.json()["request_id"] != bad
        CONTRACT_VALIDATOR.validate(rejected.json(), ERROR_SCHEMA)
    ok = await client.get("/healthz")
    assert ok.status_code == 200 and ok.headers["X-Request-Id"]


def test_contract_schema_rejects_lowercase_codes_and_bad_request_ids():
    valid = {
        "code": "NOT_FOUND",
        "message": "The requested resource was not found",
        "retryable": False,
        "action": "none",
        "request_id": uuid.uuid4().hex,
    }
    CONTRACT_VALIDATOR.validate(valid, ERROR_SCHEMA)
    for broken in (
        {**valid, "code": "not_found"},
        {**valid, "request_id": "-starts-with-hyphen"},
        {**valid, "action": "shrug"},
        {k: v for k, v in valid.items() if k != "retryable"},
    ):
        with pytest.raises(ContractInvalid):
            CONTRACT_VALIDATOR.validate(broken, ERROR_SCHEMA)


async def test_complete_waits_for_an_in_flight_upload(client):
    headers = await guest(client)
    upload = await declare_upload(client, headers)
    first_chunk_sent = asyncio.Event()
    release = asyncio.Event()

    async def slow_body():
        yield CONTENT[:10]
        first_chunk_sent.set()
        await release.wait()
        yield CONTENT[10:]

    put = asyncio.create_task(
        client.put(
            upload["target"],
            content=slow_body(),
            headers={**headers, "Transfer-Encoding": "chunked"},
        )
    )
    await asyncio.wait_for(first_chunk_sent.wait(), timeout=5)
    complete = asyncio.create_task(
        client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    )
    done, _ = await asyncio.wait({complete}, timeout=0.5)
    assert not done, "complete must wait for the row lock held by the streaming PUT"
    release.set()
    assert (await put).status_code == 204
    completed = await complete
    assert completed.status_code == 200, completed.text
    assert completed.json()["state"] == "completed"


async def test_database_failures_are_safe_503(client, app, monkeypatch, caplog):
    headers = await guest(client)

    def broken(*args, **kwargs):
        raise OperationalError("SELECT", None, Exception("private database details"))

    monkeypatch.setattr(type(app.state.database.engine), "connect", broken)
    response = await client.get("/v1/investigations", headers=headers)
    assert_error(response, 503, "DATABASE_UNAVAILABLE", "retry")
    assert response.json()["retryable"] is True
    assert "private database details" not in response.text + caplog.text
