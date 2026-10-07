"""Authorization matrix generated from the OpenAPI paths (REPO-06, #28).

Every route with a path parameter is an object route. Each one is called as the owner,
another owner (a principal with objects of its own), a fresh guest and an unauthenticated
caller (no credential, then an unknown one). The owner gets 2xx; every other caller gets
the same 404 or 401 as for a missing object, so existence never leaks; every error passes
``assert_safe_error``. A new object route without a case here fails the test, so it cannot
be forgotten. Collection routes are covered by the authentication and listing checks.
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from recovery.test_captures import DATA, parts
from sqlalchemy import update
from test_intake_api import CONTENT, URL_BODY, guest
from test_reports_api import publish
from test_security_errors import assert_safe_error

from services.api.main import create_app
from services.jobs.handlers import default_handlers
from services.jobs.models import jobs
from services.media.runner import CommandLimits, run_command
from services.pipeline.intake import intake_stage_key
from services.pipeline.media_validation import media_stage_key
from services.settings import Settings
from services.worker.runtime import Worker

PUBLIC = {("POST", "/v1/principals/guest"), ("GET", "/healthz")}


@dataclass
class Workspace:
    headers: dict[str, str]
    investigation: str
    job: str
    report: str
    upload: str
    capture: str
    version: int = 1
    # An upload investigation whose video passed media validation, for device text and speech.
    text_investigation: str = ""
    text_source: dict[str, Any] = field(default_factory=dict)


def device_text(w: Workspace, suffix: str = "") -> str:
    return f"/v1/investigations/{w.text_investigation}/device-text{suffix}"


Request = tuple[str, str, dict[str, Any]]
CASES: dict[tuple[str, str], Callable[[Workspace], Request]] = {
    ("PUT", "/v1/uploads/{upload_id}/content"): lambda w: (
        f"/v1/uploads/{w.upload}/content",
        "PUT",
        {"content": CONTENT},
    ),
    ("POST", "/v1/uploads/{upload_id}/complete"): lambda w: (
        f"/v1/uploads/{w.upload}/complete",
        "POST",
        {},
    ),
    ("GET", "/v1/investigations/{investigation_id}"): lambda w: (
        f"/v1/investigations/{w.investigation}",
        "GET",
        {},
    ),
    ("POST", "/v1/jobs/{job_id}/cancel"): lambda w: (f"/v1/jobs/{w.job}/cancel", "POST", {}),
    ("DELETE", "/v1/jobs/{job_id}"): lambda w: (f"/v1/jobs/{w.job}", "DELETE", {}),
    ("PUT", "/v1/captures/{capture_id}/chunks/{seq}"): lambda w: (
        f"/v1/captures/{w.capture}/chunks/0",
        "PUT",
        {"files": chunk_files(w.capture)},
    ),
    ("POST", "/v1/captures/{capture_id}/close"): lambda w: (
        f"/v1/captures/{w.capture}/close",
        "POST",
        {"json": {"continue_research": True}},
    ),
    ("GET", "/v1/captures/{capture_id}"): lambda w: (f"/v1/captures/{w.capture}", "GET", {}),
    ("GET", "/v1/investigations/{investigation_id}/reports"): lambda w: (
        f"/v1/investigations/{w.investigation}/reports",
        "GET",
        {},
    ),
    ("GET", "/v1/investigations/{investigation_id}/reports/{version}"): lambda w: (
        f"/v1/investigations/{w.investigation}/reports/{w.version}",
        "GET",
        {},
    ),
    ("GET", "/v1/investigations/{investigation_id}/reports/{version}/export"): lambda w: (
        f"/v1/investigations/{w.investigation}/reports/{w.version}/export",
        "GET",
        {},
    ),
    ("POST", "/v1/investigations/{investigation_id}/reanalyze"): lambda w: (
        f"/v1/investigations/{w.investigation}/reanalyze",
        "POST",
        {
            "json": {"reason": "deeper", "base_version": w.version},
            "headers": {"Idempotency-Key": "authz-matrix"},
        },
    ),
    ("POST", "/v1/reports/{report_id}/save"): lambda w: (
        f"/v1/reports/{w.report}/save",
        "POST",
        {},
    ),
    ("POST", "/v1/investigations/{investigation_id}/speech/retry"): lambda w: (
        f"/v1/investigations/{w.text_investigation}/speech/retry",
        "POST",
        {
            "json": {"protocol_version": 1},
            "headers": {"Idempotency-Key": "authz-matrix"},
        },
    ),
    ("GET", "/v1/investigations/{investigation_id}/device-text"): lambda w: (
        device_text(w),
        "GET",
        {},
    ),
    ("PUT", "/v1/investigations/{investigation_id}/device-text/batches/{batch_id}"): lambda w: (
        device_text(w, "/batches/0"),
        "PUT",
        {"json": {**w.text_source, "frames": []}},
    ),
    ("POST", "/v1/investigations/{investigation_id}/device-text/complete"): lambda w: (
        device_text(w, "/complete"),
        "POST",
        {
            "json": {
                **w.text_source,
                "batch_count": 1,
                "dropped_frames": 0,
                "capped_frames": 0,
                "unfinished_frames": 0,
            }
        },
    ),
}


def chunk_files(capture_id):
    metadata, data = parts({"id": capture_id, "chunk_duration_ms": 10_000}, 0, DATA)
    return {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "content": ("chunk.bin", data, "application/octet-stream"),
    }


def operations(app):
    # OpenAPI order: cancel before delete, upload bytes before completion, chunk before close.
    return [
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    ]


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            artifacts_dir=tmp_path / "artifacts",
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


async def validated_video(client, app, headers) -> tuple[str, dict[str, Any]]:
    """Create an upload investigation and run only its intake and media validation jobs."""
    video = app.state.settings.storage_dir.parent / f"{uuid.uuid4().hex}.mkv"
    generated = await run_command(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=4"]
        + ["-t", "1", "-c:v", "ffv1", str(video)],
        CommandLimits(10, 5, 65536, 1048576),
    )
    assert generated.returncode == 0
    content = video.read_bytes()
    declared = await client.post(
        "/v1/uploads",
        json={"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()},
        headers=headers,
    )
    assert declared.status_code == 201, declared.text
    upload = declared.json()
    stored = await client.put(upload["target"], headers=headers, content=content)
    assert stored.status_code == 204, stored.text
    completed = await client.post(f"/v1/uploads/{upload['id']}/complete", headers=headers)
    assert completed.status_code == 200, completed.text
    created = await client.post(
        "/v1/investigations",
        json={"source": {"kind": "upload", "upload_id": upload["id"]}},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert created.status_code == 202, created.text
    investigation = created.json()["id"]
    worker = Worker(
        app.state.database,
        2,
        handlers=default_handlers(app.state.upload_store, settings=app.state.settings),
    )
    for key in (
        intake_stage_key(uuid.UUID(investigation)),
        media_stage_key(uuid.UUID(investigation)),
    ):
        # Leave every other queued job, including the URL intake jobs the matrix cancels, alone.
        async with app.state.database.engine.begin() as connection:
            await connection.execute(
                update(jobs)
                .where(jobs.c.stage == key.stage, jobs.c.input_hash == key.input_hash)
                .values(available_at=datetime(1970, 1, 1, tzinfo=UTC))
            )
        claim = await worker.queue.claim("authz-matrix", [key.stage], 30)
        assert claim is not None and claim.key == key
        await worker._execute(claim)
    read = await client.get(f"/v1/investigations/{investigation}", headers=headers)
    assert read.json()["coverage"]["media"]["has_video"] is True, read.text
    return investigation, {
        "protocol_version": 1,
        "upload_id": upload["id"],
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "timebase": "media",
        "rotation_degrees": 0,
        "box_space": "normalized_10000",
        "recognizer": {"name": "mlkit-text-recognition-latin-bundled", "version": "16.0.1"},
        "sampling": None,
    }


async def workspace(client, app) -> Workspace:
    headers = await guest(client)
    text_investigation, text_source = await validated_video(client, app, headers)
    created = await client.post(
        "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": "authz"}
    )
    assert created.status_code == 202, created.text
    investigation = created.json()["id"]
    read = await client.get(f"/v1/investigations/{investigation}", headers=headers)
    job = read.json()["job"]["id"]
    report = await publish(app, investigation)
    upload = await client.post(
        "/v1/uploads",
        json={
            "size_bytes": len(CONTENT),
            "sha256": hashlib.sha256(CONTENT).hexdigest(),
            "content_type": "video/mp4",
        },
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    capture = await client.post(
        "/v1/captures",
        json={"chunk_duration_ms": 10_000},
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert capture.status_code == 201, capture.text
    return Workspace(
        headers,
        investigation,
        job,
        report.id,
        upload.json()["id"],
        capture.json()["id"],
        text_investigation=text_investigation,
        text_source=text_source,
    )


def test_every_object_route_has_a_matrix_case(app):
    object_routes = {(m, p) for m, p in operations(app) if "{" in p}
    assert object_routes == set(CASES), (
        "Add an authorization-matrix case for every new object route: "
        f"missing {sorted(object_routes - set(CASES))}, stale {sorted(set(CASES) - object_routes)}"
    )


async def call(client, case, target, headers):
    path, method, kwargs = case(target)
    extra = kwargs.pop("headers", {})
    return await client.request(method, path, headers={**headers, **extra}, **kwargs)


async def test_object_routes_owner_other_owner_guest_and_unauthenticated(client, app):
    owner = await workspace(client, app)
    other = await workspace(client, app)
    fresh = {"guest": await guest(client)}
    callers = {
        "unauthenticated": ({}, 401, "AUTHENTICATION_REQUIRED"),
        "unknown credential": (
            {"Authorization": "Bearer ovk_" + "0" * 43},
            401,
            "INVALID_CREDENTIAL",
        ),
        "guest": (fresh["guest"], 404, "NOT_FOUND"),
        "other owner": (other.headers, 404, "NOT_FOUND"),
    }
    for route in operations(app):
        if route not in CASES:
            continue
        case = CASES[route]
        for name, (headers, status, code) in callers.items():
            response = await call(client, case, owner, headers)
            assert response.status_code == status, (route, name, response.text)
            assert_safe_error(response, status, code)
            if status == 401:
                assert response.headers["WWW-Authenticate"].startswith("Bearer")
        # A missing object is indistinguishable from another owner's.
        missing = Workspace(
            owner.headers,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            text_investigation=str(uuid.uuid4()),
            text_source=owner.text_source,
        )
        absent = await call(client, case, missing, owner.headers)
        assert_safe_error(absent, 404, "NOT_FOUND")
        allowed = await call(client, case, owner, owner.headers)
        assert 200 <= allowed.status_code < 300, (route, allowed.text)
        # The other owner still reaches its own object through the same route.
        own = await call(client, case, other, other.headers)
        assert 200 <= own.status_code < 300, (route, own.text)


async def test_every_non_public_route_requires_a_credential(client, app):
    for method, path in set(operations(app)) - PUBLIC:
        concrete = path.replace("{seq}", "0").replace("{version}", "1")
        while "{" in concrete:
            start = concrete.index("{")
            concrete = concrete[:start] + str(uuid.uuid4()) + concrete[concrete.index("}") + 1 :]
        response = await client.request(method, concrete)
        assert_safe_error(response, 401, "AUTHENTICATION_REQUIRED")


async def test_collections_and_voice_never_show_another_owners_objects(client, app):
    owner = await workspace(client, app)
    other = await workspace(client, app)
    saved = await client.post(f"/v1/reports/{owner.report}/save", headers=owner.headers)
    assert saved.status_code == 200, saved.text
    listed = await client.get("/v1/investigations", headers=other.headers)
    assert owner.investigation not in {item["id"] for item in listed.json()["items"]}
    saves = await client.get("/v1/reports/saved", headers=other.headers)
    assert owner.report not in {item["report_id"] for item in saves.json()["items"]}
    for action, kind, target in (
        ("open_check", "investigation", owner.investigation),
        ("save_report", "report", owner.report),
        ("queue_cancel", "job", owner.job),
    ):
        response = await client.post(
            "/v1/voice/actions",
            json={
                "request_id": f"req_{uuid.uuid4().hex}",
                "action": action,
                "target": {"kind": kind, "id": target},
            },
            headers=other.headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"] == "denied"
        assert response.json()["error"]["code"] == "VOICE_TARGET_NOT_FOUND"
