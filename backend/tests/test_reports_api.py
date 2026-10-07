"""Report versions and explicit saves (BE-10, #33): owner scoping, immutability, idempotency
and the development-only stub path."""

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from test_intake_api import CONTRACT_VALIDATOR, URL_BODY, assert_error, guest

from services.api.main import create_app
from services.api.schemas import ReportVersion
from services.jobs.handlers import default_handlers
from services.models import investigations, report_versions, saved_reports
from services.pipeline.intake import INTAKE_STAGE, intake_payload, intake_stage
from services.pipeline.stub_reports import (
    STUB_CHANGE_SUMMARY,
    StubReportsDisabled,
    enable_stub_reports,
    intake_with_stub_report,
    publish_stub_report,
    stub_report,
)
from services.reports import NextVersion, parse_canonical_uuid, publish_report_version
from services.settings import Settings

OPENAPI = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "openapi.json"
REPORT_SCHEMA = "report-version.schema.json"


def _component_schema(name):
    """An inline OpenAPI component with its references rewritten for the contract validator."""
    components = json.loads(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]

    def resolve(node):
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            target = components[ref.rsplit("/", 1)[1]]
            if set(target) == {"$ref"}:
                return {"$ref": target["$ref"].removeprefix("schemas/")}
            return resolve(copy.deepcopy(target))
        if isinstance(ref, str):
            return {**node, "$ref": ref.removeprefix("schemas/")}
        return {key: resolve(value) for key, value in node.items()}

    return resolve(copy.deepcopy(components[name]))


def assert_component(body, name):
    # Inline components are not schema files; validate them with the same stdlib validator.
    CONTRACT_VALIDATOR._validate(body, _component_schema(name), REPORT_SCHEMA, "$", False)


@pytest.fixture
async def app(database_url, tmp_path):
    application = create_app(
        Settings(database_url=database_url, storage_dir=tmp_path / "uploads", _env_file=None)
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


async def create_investigation(client, headers, key="report-check"):
    response = await client.post(
        "/v1/investigations", json=URL_BODY, headers={**headers, "Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return response.json()["id"]


def correction(identity: NextVersion) -> ReportVersion:
    """A second, non-fixture version: the stub content with one claim's assessment rerun."""
    report = stub_report(identity)
    return report.model_copy(
        update={
            "change_summary": "Correction: the user corrected one claim; its assessment was rerun.",
            "provisional": False,
            "assessments": [
                item.model_copy(update={"provisional": False}) for item in report.assessments
            ],
        }
    )


async def publish(app, investigation_id, build=None):
    async with app.state.database.engine.begin() as connection:
        if build is None:
            return await publish_stub_report(connection, uuid.UUID(investigation_id), enabled=True)
        return await publish_report_version(connection, uuid.UUID(investigation_id), build)


async def test_versions_are_listed_read_and_immutable(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    empty = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert empty.status_code == 200
    assert empty.json() == {"investigation_id": investigation_id, "items": []}

    first = await publish(app, investigation_id)
    second = await publish(app, investigation_id, correction)
    assert (first.version, second.version) == (1, 2)
    assert first.supersedes is None and second.supersedes == first.id
    assert first.change_summary == STUB_CHANGE_SUMMARY
    assert first.change_summary.startswith("Development fixture, not a check of this media")
    assert first.provisional and all(item.provisional for item in first.assessments)

    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert_component(body, "ReportVersionList")
    assert [item["version"] for item in body["items"]] == [1, 2]
    assert [item["fixture"] for item in body["items"]] == [True, False]
    assert [item["supersedes"] for item in body["items"]] == [None, first.id]
    assert body["items"][1]["change_summary"].startswith("Correction:")

    for report in (first, second):
        read = await client.get(
            f"/v1/investigations/{investigation_id}/reports/{report.version}", headers=headers
        )
        assert read.status_code == 200, read.text
        CONTRACT_VALIDATOR.validate(read.json(), REPORT_SCHEMA)
        assert read.json() == report.model_dump(mode="json")
        assert all(item["version"] == report.version for item in read.json()["assessments"])

    missing = f"/v1/investigations/{investigation_id}/reports/3"
    assert_error(await client.get(missing, headers=headers), 404, "NOT_FOUND")
    for bad in ("0", "-1", "one", "99999999999"):
        assert_error(
            await client.get(
                f"/v1/investigations/{investigation_id}/reports/{bad}", headers=headers
            ),
            422,
            "VALIDATION_FAILED",
        )
    # A published version is never edited; the database refuses an UPDATE.
    with pytest.raises(DBAPIError, match="immutable"):
        async with app.state.database.engine.begin() as connection:
            await connection.execute(
                update(report_versions)
                .where(report_versions.c.id == uuid.UUID(first.id))
                .values(change_summary="edited")
            )


async def test_publish_rejects_a_report_with_the_wrong_identity(client, app):
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    with pytest.raises(ValueError, match="identity"):
        await publish(
            app,
            investigation_id,
            lambda identity: stub_report(identity).model_copy(update={"version": 7}),
        )
    with pytest.raises(LookupError):
        await publish(app, str(uuid.uuid4()), stub_report)


async def test_every_report_route_is_owner_scoped(client, app):
    owner = await guest(client)
    other = await guest(client)
    investigation_id = await create_investigation(client, owner)
    report = await publish(app, investigation_id)
    routes = (
        ("get", f"/v1/investigations/{investigation_id}/reports"),
        ("get", f"/v1/investigations/{investigation_id}/reports/1"),
        ("post", f"/v1/reports/{report.id}/save"),
    )
    for method, path in routes:
        assert_error(await client.request(method, path, headers=other), 404, "NOT_FOUND")
        assert_error(await client.request(method, path), 401, "AUTHENTICATION_REQUIRED")
    unknown = str(uuid.uuid4())
    for path in (
        f"/v1/investigations/{unknown}/reports",
        f"/v1/investigations/{unknown}/reports/1",
    ):
        assert_error(await client.get(path, headers=owner), 404, "NOT_FOUND")
    for report_id in (unknown, report.id.upper(), "rpt_synthetic_0001_v2", "x" * 128):
        assert_error(
            await client.post(f"/v1/reports/{report_id}/save", headers=owner), 404, "NOT_FOUND"
        )
    assert (await client.get("/v1/reports/saved", headers=other)).json() == {"items": []}
    assert_error(await client.get("/v1/reports/saved"), 401, "AUTHENTICATION_REQUIRED")
    assert_error(
        await client.get("/v1/reports/saved", params={"owner_id": unknown}, headers=other),
        400,
        "CLIENT_IDENTITY_REJECTED",
    )
    async with app.state.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                select(func.count())
                .select_from(saved_reports)
                .where(saved_reports.c.report_id == uuid.UUID(report.id))
            )
            == 0
        )


async def test_save_is_explicit_idempotent_and_listed(client, app):
    headers = await guest(client)
    first_investigation = await create_investigation(client, headers, "first")
    second_investigation = await create_investigation(client, headers, "second")
    first = await publish(app, first_investigation)
    second = await publish(app, second_investigation)
    assert (await client.get("/v1/reports/saved", headers=headers)).json() == {"items": []}

    saved = await client.post(f"/v1/reports/{first.id}/save", headers=headers)
    assert saved.status_code == 200, saved.text
    assert_component(saved.json(), "SavedReport")
    assert saved.json()["report"] == first.model_dump(mode="json")
    assert saved.json()["report_id"] == first.id
    assert saved.json()["investigation_id"] == first_investigation
    repeat = await client.post(f"/v1/reports/{first.id}/save", headers=headers)
    assert repeat.status_code == 200 and repeat.json() == saved.json()
    later = await client.post(f"/v1/reports/{second.id}/save", headers=headers)
    assert later.status_code == 200

    listing = await client.get("/v1/reports/saved", headers=headers)
    assert listing.status_code == 200
    assert_component(listing.json(), "SavedReportList")
    assert [item["report_id"] for item in listing.json()["items"]] == [second.id, first.id]
    assert_error(
        await client.post(f"/v1/reports/{first.id}/save", json={"user_id": "x"}, headers=headers),
        422,
        "VALIDATION_FAILED",
    )
    async with app.state.database.engine.connect() as connection:
        count = await connection.scalar(
            select(func.count())
            .select_from(saved_reports)
            .where(saved_reports.c.report_id.in_([uuid.UUID(first.id), uuid.UUID(second.id)]))
        )
    assert count == 2


async def test_unsave_removes_only_the_callers_save_and_is_idempotent(client, app):
    owner = await guest(client)
    other = await guest(client)
    investigation_id = await create_investigation(client, owner)
    report = await publish(app, investigation_id)
    path = f"/v1/reports/{report.id}/save"
    assert (await client.post(path, headers=owner)).status_code == 200

    # Another principal's unsave is the same 404 as a missing report and changes nothing.
    assert_error(await client.delete(path, headers=other), 404, "NOT_FOUND")
    unknown = f"/v1/reports/{uuid.uuid4()}/save"
    assert_error(await client.delete(unknown, headers=owner), 404, "NOT_FOUND")
    assert len((await client.get("/v1/reports/saved", headers=owner)).json()["items"]) == 1
    assert_error(await client.delete(path), 401, "AUTHENTICATION_REQUIRED")
    for report_id in (report.id.upper(), "rpt_synthetic_0001_v2", "x" * 128):
        assert_error(
            await client.delete(f"/v1/reports/{report_id}/save", headers=owner), 404, "NOT_FOUND"
        )
    assert_error(
        await client.request("DELETE", path, json={"user_id": "x"}, headers=owner),
        422,
        "VALIDATION_FAILED",
    )

    removed = await client.delete(path, headers=owner)
    assert removed.status_code == 204 and removed.content == b""
    assert (await client.get("/v1/reports/saved", headers=owner)).json() == {"items": []}
    again = await client.delete(path, headers=owner)
    assert again.status_code == 204
    # The immutable version is untouched and can be saved again.
    read = await client.get(f"/v1/investigations/{investigation_id}/reports/1", headers=owner)
    assert read.status_code == 200 and read.json() == report.model_dump(mode="json")
    assert (await client.post(path, headers=owner)).status_code == 200
    async with app.state.database.engine.connect() as connection:
        count = await connection.scalar(
            select(func.count())
            .select_from(saved_reports)
            .where(saved_reports.c.report_id == uuid.UUID(report.id))
        )
    assert count == 1


def test_report_ids_are_canonical_uuids():
    value = uuid.uuid4()
    assert parse_canonical_uuid(str(value)) == value
    for bad in (str(value).upper(), value.hex, f"{{{value}}}", "", "rpt_synthetic"):
        assert parse_canonical_uuid(bad) is None


async def test_stub_path_is_off_by_default_and_refused_when_disabled(client, app):
    assert Settings(database_url="postgresql+psycopg://u@h/d", _env_file=None).stub_reports is False
    handlers = dict(default_handlers())
    assert handlers[INTAKE_STAGE] is intake_stage
    enable_stub_reports(handlers)
    assert handlers[INTAKE_STAGE] is intake_with_stub_report
    headers = await guest(client)
    investigation_id = await create_investigation(client, headers)
    async with app.state.database.engine.begin() as connection:
        with pytest.raises(StubReportsDisabled):
            await publish_stub_report(connection, uuid.UUID(investigation_id), enabled=False)
    listing = await client.get(f"/v1/investigations/{investigation_id}/reports", headers=headers)
    assert listing.json()["items"] == []


async def test_stub_intake_publishes_one_fixture_report(database_url, tmp_path, caplog):
    app = create_app(
        Settings(
            database_url=database_url,
            storage_dir=tmp_path / "uploads",
            stub_reports=True,
            _env_file=None,
        )
    )
    async with app.router.lifespan_context(app):
        assert "Stub reports enabled; fixture reports will be published" in caplog.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            headers = await guest(client)
            investigation_id = await create_investigation(client, headers)
            principal = await _owner_of(app, investigation_id)
            job = SimpleNamespace(payload=intake_payload(uuid.UUID(investigation_id), principal))
            context = SimpleNamespace(
                queue=SimpleNamespace(database=app.state.database), successors=[]
            )
            # A re-leased intake job runs the stage again; the stub is published once.
            for _ in range(2):
                result = await intake_with_stub_report(job, context)
                assert result["investigation_id"] == investigation_id
            listing = await client.get(
                f"/v1/investigations/{investigation_id}/reports", headers=headers
            )
            items = listing.json()["items"]
            assert len(items) == 1 and items[0]["fixture"] is True
            assert items[0]["change_summary"] == STUB_CHANGE_SUMMARY


async def _owner_of(app, investigation_id):
    async with app.state.database.engine.connect() as connection:
        return await connection.scalar(
            select(investigations.c.owner_id).where(
                investigations.c.id == uuid.UUID(investigation_id)
            )
        )
