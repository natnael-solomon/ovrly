"""Owner-scoped report versions, explicit saves, reanalysis and export (BE-10, #33)."""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Path, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from services.api.auth import CurrentPrincipal, load_owned, owned_rows
from services.api.auth.dependency import Principal
from services.api.auth.ownership import not_found
from services.api.routes.common import engine, settings
from services.api.routes.investigations import IDEMPOTENCY_HEADER, _idempotency_key
from services.api.routes.jobs import require_empty_body
from services.api.schemas import (
    ReanalysisRequest,
    ReanalysisResponse,
    ReportExport,
    ReportVersion,
    ReportVersionListResponse,
    ReportVersionSummary,
    SavedReport,
    SavedReportListResponse,
)
from services.jobs.queue import JobQueue
from services.models import investigations, report_versions, saved_reports
from services.reanalysis import replay, request_hash, request_reanalysis
from services.report_export import build_export
from services.reports import (
    parse_canonical_uuid,
    report_from_row,
    save_owned_report,
    saved_report,
    unsave_owned_report,
)

router = APIRouter(tags=["reports"])

_SAVED_LIMIT = 100


@router.get("/investigations/{investigation_id}/reports", response_model=ReportVersionListResponse)
async def list_report_versions(
    request: Request, investigation_id: uuid.UUID, principal: CurrentPrincipal
) -> ReportVersionListResponse:
    async with engine(request).connect() as connection:
        await load_owned(connection, investigations, investigation_id, principal)
        rows = (
            await connection.execute(
                select(report_versions.c.payload, report_versions.c.fixture)
                .where(report_versions.c.investigation_id == investigation_id)
                .order_by(report_versions.c.version)
            )
        ).all()
    items = []
    for row in rows:
        report = ReportVersion.model_validate(row.payload)
        items.append(
            ReportVersionSummary(
                id=report.id,
                version=report.version,
                created_at=report.created_at,
                provisional=report.provisional,
                change_summary=report.change_summary,
                supersedes=report.supersedes,
                fixture=row.fixture,
            )
        )
    return ReportVersionListResponse(investigation_id=investigation_id, items=items)


VersionPath = Annotated[int, Path(ge=1, le=2_147_483_647)]


async def _owned_version(
    request: Request, investigation_id: uuid.UUID, version: int, principal: Principal
) -> tuple[ReportVersion, bool]:
    async with engine(request).connect() as connection:
        await load_owned(connection, investigations, investigation_id, principal)
        row = (
            await connection.execute(
                select(report_versions.c.payload, report_versions.c.fixture).where(
                    report_versions.c.investigation_id == investigation_id,
                    report_versions.c.version == version,
                )
            )
        ).first()
    if row is None:
        raise not_found()
    return report_from_row(row.payload, row.fixture), row.fixture


@router.get("/investigations/{investigation_id}/reports/{version}", response_model=ReportVersion)
async def get_report_version(
    request: Request, investigation_id: uuid.UUID, version: VersionPath, principal: CurrentPrincipal
) -> ReportVersion:
    report, _ = await _owned_version(request, investigation_id, version, principal)
    return report


@router.get(
    "/investigations/{investigation_id}/reports/{version}/export", response_model=ReportExport
)
async def export_report_version(
    request: Request, investigation_id: uuid.UUID, version: VersionPath, principal: CurrentPrincipal
) -> ReportExport:
    report, fixture = await _owned_version(request, investigation_id, version, principal)
    return build_export(report, fixture=fixture, retrieved_at=datetime.now(UTC))


@router.post(
    "/investigations/{investigation_id}/reanalyze",
    status_code=202,
    response_model=ReanalysisResponse,
)
async def reanalyze(
    request: Request,
    investigation_id: uuid.UUID,
    body: Annotated[ReanalysisRequest, Body(discriminator="reason")],
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> Any:
    key = _idempotency_key(idempotency_key)
    queue = JobQueue(request.app.state.database)
    database = engine(request)
    try:
        async with database.begin() as connection:
            response = await request_reanalysis(
                connection, queue, principal, investigation_id, body, key, settings(request)
            )
    except IntegrityError:
        # The same key was used concurrently for another investigation of this caller.
        async with database.connect() as connection:
            replayed = await replay(
                connection, principal, key, request_hash(investigation_id, body)
            )
        if replayed is None:
            raise
        response = replayed
    return JSONResponse(response, status_code=202)


@router.post("/reports/{report_id}/save", response_model=SavedReport)
async def save_report(
    request: Request,
    report_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: CurrentPrincipal,
) -> SavedReport:
    await require_empty_body(request)
    parsed = parse_canonical_uuid(report_id)
    if parsed is None:
        raise not_found()
    async with engine(request).begin() as connection:
        return await save_owned_report(connection, principal, parsed)


@router.delete("/reports/{report_id}/save", status_code=204)
async def unsave_report(
    request: Request,
    report_id: Annotated[str, Path(min_length=1, max_length=128)],
    principal: CurrentPrincipal,
) -> Response:
    await require_empty_body(request)
    parsed = parse_canonical_uuid(report_id)
    if parsed is None:
        raise not_found()
    async with engine(request).begin() as connection:
        await unsave_owned_report(connection, principal, parsed)
    return Response(status_code=204)


@router.get("/reports/saved", response_model=SavedReportListResponse)
async def list_saved_reports(
    request: Request, principal: CurrentPrincipal
) -> SavedReportListResponse:
    query = (
        owned_rows(saved_reports, principal)
        .order_by(saved_reports.c.saved_at.desc(), saved_reports.c.report_id)
        .limit(_SAVED_LIMIT)
    )
    async with engine(request).connect() as connection:
        rows = (await connection.execute(query)).all()
    return SavedReportListResponse(items=[saved_report(row) for row in rows])
