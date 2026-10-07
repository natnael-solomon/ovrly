"""Investigations: idempotent creation with a durable record first, owner-scoped reads."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Row, Text, and_, case, insert, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, Principal, load_owned, owned_rows
from services.api.errors import ApiError, extraction_failure_code, safe_error
from services.api.intake import InvestigationDispatcher
from services.api.routes.common import engine, settings
from services.api.schemas import (
    Coverage,
    InvestigationCreateRequest,
    InvestigationListResponse,
    InvestigationReadModel,
    InvestigationResponse,
    JobSummary,
    ProcessingStatus,
    ReportVersion,
    SafeError,
    SpeechResult,
    UploadSource,
)
from services.captures import CAPTURE_STAGES
from services.jobs.models import job_results, jobs
from services.jobs.states import JobState
from services.models import (
    capture_chunks,
    idempotency_keys,
    investigations,
    reanalysis_requests,
    uploads,
)
from services.pipeline.analysis import uploaded_analysis
from services.pipeline.capture_media import capture_analysis
from services.pipeline.intake import (
    COVERAGE_PLACEHOLDER,
    INITIAL_STATE,
    INTAKE_STAGE,
    intake_stage_key,
)
from services.pipeline.media_validation import MEDIA_STAGE, media_stage_key
from services.pipeline.speech import ASR_STAGE, speech_stage_key
from services.quotas import charge, lock_owner
from services.reports import latest_reports, summarize_job

router = APIRouter(tags=["investigations"])

IDEMPOTENCY_HEADER = "Idempotency-Key"
_LIST_LIMIT = 100
INITIAL_STAGE = INTAKE_STAGE
# Job states as seen by the client. Worker internals (leases, retries, failure types)
# never appear; a failed job without a recorded cause reports this generic code.
_JOB_STATE_TO_INVESTIGATION = {
    "queued": "queued",
    "leased": "queued",
    "running": "running",
    "failed": "failed",
    "cancelled": "cancelled",
    "deleted": "cancelled",
}
PROCESSING_FAILED = "PROCESSING_FAILED"
_MEDIA_FAILURES = {
    "InvalidMedia": "INVALID_MEDIA",
    "NonRetriableInput": "INVALID_MEDIA",
    "MediaTooLarge": "MEDIA_SIZE_LIMIT_EXCEEDED",
    "MediaTooLong": "DURATION_LIMIT_EXCEEDED",
    "MediaTimedOut": "MEDIA_PROCESSING_TIMEOUT",
    "MediaToolUnavailable": "MEDIA_PROCESSING_UNAVAILABLE",
}


@dataclass(frozen=True)
class PipelineJob:
    state: str
    stage: str
    failure: str | None
    result: dict[str, Any] | None
    media_result: dict[str, Any] | None = None


def _source(row: Row[Any]) -> dict[str, Any]:
    source: dict[str, Any] = {"kind": row.source_kind}
    if row.source_kind == "url":
        source["url"] = row.source_url
    elif row.source_kind == "capture":
        source["capture_id"] = str(row.id)
    else:
        source["upload_id"] = str(row.upload_id)
    if row.declared_duration_ms is not None:
        source["duration_ms"] = row.declared_duration_ms
    return source


def investigation_response(row: Row[Any], job: PipelineJob | None = None) -> InvestigationResponse:
    """Build the client view from the current stage's fenced state and result.

    A published job means the stage finished and the stored state stands.
    """
    state = row.state
    error_code = row.error_code
    coverage = COVERAGE_PLACEHOLDER
    stage = row.stage
    speech = None
    if job is not None:
        stage = job.stage
        if job.media_result is not None:
            coverage = Coverage.model_validate(job.media_result["coverage"]).model_dump(
                mode="json", exclude_unset=True
            )
            speech = job.media_result.get("speech")
        if stage == ASR_STAGE:
            speech = (
                job.result["speech"]
                if job.state == "published" and job.result is not None
                else {"status": "running" if job.state == "running" else "pending"}
            )
            if job.state == "failed":
                speech = {
                    "status": "unavailable",
                    "reason": (
                        "quota_exhausted"
                        if job.failure == "ASRQuotaExhausted"
                        else "unknown_outcome"
                        if job.failure == "ASRUnknownOutcome"
                        else "provider_unavailable"
                    ),
                }
            elif job.state in {"cancelled", "deleted"}:
                speech = {"status": "unavailable", "reason": "cancelled"}
    if job is not None and job.state != "published":
        state = _JOB_STATE_TO_INVESTIGATION[job.state]
        if state == "failed" and error_code is None:
            error_code = (
                _MEDIA_FAILURES.get(job.failure or "", PROCESSING_FAILED)
                if job.stage == MEDIA_STAGE
                else (
                    "ASR_QUOTA_EXHAUSTED"
                    if job.failure == "ASRQuotaExhausted"
                    else "ASR_UNAVAILABLE"
                )
                if job.stage == ASR_STAGE
                else PROCESSING_FAILED
            )
    return InvestigationResponse(
        id=row.id,
        state=state,
        stage="asr" if stage == ASR_STAGE else stage,
        coverage=coverage,
        version=row.version,
        error=safe_error(error_code),
        source=_source(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
        speech=speech,
    )


async def job_states(
    connection: AsyncConnection, investigation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, PipelineJob]:
    """Current pipeline stage, including only its fenced published result."""
    if not investigation_ids:
        return {}
    keys = {
        (key.version, key.stage, key.input_hash): identifier
        for identifier in investigation_ids
        for key in (
            intake_stage_key(identifier),
            media_stage_key(identifier),
            speech_stage_key(identifier),
        )
    }
    rows = await connection.execute(
        select(
            jobs.c.version,
            jobs.c.input_hash,
            jobs.c.state,
            jobs.c.stage,
            jobs.c.failure,
            job_results.c.result,
        )
        .outerjoin(job_results, job_results.c.job_id == jobs.c.id)
        .where(tuple_(jobs.c.version, jobs.c.stage, jobs.c.input_hash).in_(list(keys)))
        .order_by(case({INTAKE_STAGE: 0, MEDIA_STAGE: 1, ASR_STAGE: 2}, value=jobs.c.stage))
    )
    states: dict[uuid.UUID, PipelineJob] = {}
    for row in rows:
        identifier = keys[(row.version, row.stage, row.input_hash)]
        previous = states.get(identifier)
        media = previous.media_result if previous is not None else None
        if row.stage == MEDIA_STAGE and row.state == "published":
            media = row.result
        states[identifier] = PipelineJob(row.state, row.stage, row.failure, row.result, media)
    return states


async def latest_jobs(
    connection: AsyncConnection, investigation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, JobSummary]:
    """The most recently created live job of each investigation.

    Candidates are the ``intake`` job, its reanalysis jobs and its capture chunk jobs;
    deleted jobs are tombstones, so an investigation whose jobs are all deleted has none.
    """
    if not investigation_ids:
        return {}
    keys = {
        (key.version, key.stage, key.input_hash): identifier
        for identifier in investigation_ids
        for key in (
            intake_stage_key(identifier),
            media_stage_key(identifier),
            speech_stage_key(identifier),
        )
    }
    candidates: list[tuple[uuid.UUID, Row[Any]]] = []
    intake = await connection.execute(
        select(jobs).where(
            tuple_(jobs.c.version, jobs.c.stage, jobs.c.input_hash).in_(list(keys)),
        )
    )
    candidates.extend((keys[(row.version, row.stage, row.input_hash)], row) for row in intake)
    for link, owner in ((reanalysis_requests.c.job_id, reanalysis_requests.c.investigation_id),):
        linked = await connection.execute(
            select(jobs, owner.label("investigation_id"))
            .join_from(jobs, owner.table, link == jobs.c.id)
            .where(owner.in_(investigation_ids))
        )
        candidates.extend((row.investigation_id, row) for row in linked)
    validation = jobs.alias("capture_validation")
    capture = await connection.execute(
        select(jobs, capture_chunks.c.session_id.label("investigation_id"))
        .select_from(
            capture_chunks.join(validation, capture_chunks.c.job_id == validation.c.id).join(
                jobs,
                and_(
                    jobs.c.version == validation.c.version,
                    jobs.c.input_hash == validation.c.input_hash,
                    jobs.c.owner_id == validation.c.owner_id,
                    jobs.c.stage.in_(CAPTURE_STAGES),
                ),
            ),
        )
        .where(capture_chunks.c.session_id.in_(investigation_ids))
    )
    candidates.extend((row.investigation_id, row) for row in capture)
    extraction = await connection.execute(
        select(jobs, investigations.c.id.label("investigation_id"))
        .join(
            investigations,
            (jobs.c.payload["investigation_id"].as_string() == investigations.c.id.cast(Text))
            & (jobs.c.owner_id == investigations.c.owner_id),
        )
        .where(
            jobs.c.stage.in_(["claim_extraction", "reconciliation"]),
            investigations.c.id.in_(investigation_ids),
        )
    )
    candidates.extend((row.investigation_id, row) for row in extraction)
    newest: dict[uuid.UUID, Row[Any]] = {}
    for investigation_id, row in candidates:
        if row.state == JobState.DELETED.value:
            continue
        current = newest.get(investigation_id)
        if current is None or (row.created_at, str(row.id)) > (current.created_at, str(current.id)):
            newest[investigation_id] = row
    return {key: summarize_job(row) for key, row in newest.items()}


def read_model(
    row: Row[Any],
    job_state: PipelineJob | None,
    job: JobSummary | None,
    report: ReportVersion | None,
) -> InvestigationReadModel:
    """The contract read model: progress, stored state, job and findings kept apart.

    A failure never carries a report and a report never carries an error, matching the
    branches of packages/contracts/schemas/investigation.schema.json. A provisional report
    reads as ``partial`` (state ``running``), a final one as ``complete``.
    """
    base = investigation_response(row, job_state)
    if job is not None and job.stage == "claim_extraction":
        speech = base.speech
        base = investigation_response(
            row,
            PipelineJob(
                state=job.state.value,
                stage=job.stage,
                failure=None,
                result=None,
                media_result=job_state.media_result if job_state is not None else None,
            ),
        )
        base.stage = job.stage
        # Extraction consumes committed speech; its job must not hide that upstream result.
        base.speech = speech
    state = base.state
    error = base.error
    status: ProcessingStatus
    if state == "failed":
        status = "failed"
        report = None
        if error is None:
            error = SafeError.model_validate(safe_error(PROCESSING_FAILED))
    elif state == "cancelled":
        status = "cancelled"
    elif report is not None:
        state, status = ("running", "partial") if report.provisional else ("completed", "complete")
    elif state == "queued":
        status = "waiting"
    else:
        # Running, or a stored completion that published no report yet: still checking.
        state, status = "running", "checking"
    if state != "failed":
        error = None
    return InvestigationReadModel(
        **{
            **base.model_dump(),
            "state": state,
            "error": error,
            "version": row.version if report is None else report.version,
        },
        processing_status=status,
        job=job,
        report=report,
    )


async def read_models(
    connection: AsyncConnection, rows: list[Row[Any]]
) -> list[InvestigationReadModel]:
    identifiers = [row.id for row in rows]
    states = await job_states(connection, identifiers)
    current = await latest_jobs(connection, identifiers)
    reports = await latest_reports(connection, identifiers)
    models = []
    for row in rows:
        stage = states.get(row.id)
        model = read_model(row, stage, current.get(row.id), reports.get(row.id))
        if (
            row.source_kind == "upload"
            and model.state != "cancelled"
            and stage is not None
            and stage.media_result is not None
            and model.speech is not None
        ):
            model.analysis = await uploaded_analysis(
                connection, row, stage.media_result, model.speech.model_dump()
            )
            if model.analysis is not None and model.report is None:
                model.state = "running"
                model.processing_status = "checking"
                model.error = None
        elif row.source_kind == "capture" and model.state != "cancelled":
            captured = await capture_analysis(connection, row.id)
            if captured is not None:
                model.speech = SpeechResult.model_validate(captured[0])
                model.analysis = captured[1]
        models.append(model)
    from services.pipeline.incremental import extraction_progress
    from services.pipeline.reconciliation import reconciliation_progress

    for item in models:
        item.extraction_progress = await extraction_progress(connection, item.id)
        item.reconciliation_progress = await reconciliation_progress(connection, item.id)
    extraction_failures = [
        item.job.id
        for item in models
        if item.job is not None and item.job.stage == "claim_extraction" and item.state == "failed"
    ]
    if extraction_failures:
        failures = await connection.execute(
            select(jobs.c.id, jobs.c.failure).where(jobs.c.id.in_(extraction_failures))
        )
        by_id = {row.id: extraction_failure_code(row.failure) for row in failures}
        for item in models:
            if item.job is not None and item.job.id in by_id:
                item.error = SafeError.model_validate(safe_error(by_id[item.job.id]))
    return models


def request_hash(body: InvestigationCreateRequest) -> str:
    canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise ApiError(
            400,
            "IDEMPOTENCY_KEY_REQUIRED",
            f"The {IDEMPOTENCY_HEADER} header is required",
            action="fix_request",
        )
    key = value.strip()
    if len(key) > 200:
        raise ApiError(
            400,
            "IDEMPOTENCY_KEY_INVALID",
            f"The {IDEMPOTENCY_HEADER} header must be at most 200 characters",
            action="fix_request",
        )
    return key


async def _replay(
    connection: AsyncConnection, owner_id: uuid.UUID, key: str, digest: str
) -> tuple[int, dict[str, Any]]:
    stored = (
        await connection.execute(
            select(idempotency_keys).where(
                idempotency_keys.c.owner_id == owner_id, idempotency_keys.c.key == key
            )
        )
    ).one()
    if stored.request_hash != digest:
        raise ApiError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            f"The {IDEMPOTENCY_HEADER} was already used with a different request body",
            action="fix_request",
        )
    body: dict[str, Any] = stored.response_body
    return stored.response_status, body


async def _create(
    request: Request,
    connection: AsyncConnection,
    principal: Principal,
    body: InvestigationCreateRequest,
    key: str,
    digest: str,
) -> tuple[int, dict[str, Any]]:
    await lock_owner(connection, principal, settings(request))
    stored = await connection.scalar(
        select(idempotency_keys.c.key).where(
            idempotency_keys.c.owner_id == principal.id, idempotency_keys.c.key == key
        )
    )
    if stored is not None:
        return await _replay(connection, principal.id, key, digest)
    now = datetime.now(UTC)
    source = body.source
    upload_id = None
    source_url = None
    if isinstance(source, UploadSource):
        upload = await load_owned(connection, uploads, source.upload_id, principal)
        if upload.state != "completed":
            raise ApiError(
                409,
                "UPLOAD_INCOMPLETE",
                "The upload must be completed before it can be investigated",
                action="upload_again",
            )
        upload_id = upload.id
    else:
        source_url = str(source.url)
    await charge(connection, principal, settings(request), checks=1)
    row = (
        await connection.execute(
            insert(investigations)
            .values(
                id=uuid.uuid4(),
                owner_id=principal.id,
                source_kind=source.kind,
                source_url=source_url,
                upload_id=upload_id,
                declared_duration_ms=source.duration_ms,
                state=INITIAL_STATE,
                stage=INITIAL_STAGE,
                version=1,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
            .returning(investigations)
        )
    ).one()
    dispatcher: InvestigationDispatcher = request.app.state.dispatcher
    await dispatcher.dispatch(connection, row.id, principal.id)
    response = (await read_models(connection, [row]))[0].model_dump(mode="json")
    await connection.execute(
        insert(idempotency_keys).values(
            owner_id=principal.id,
            key=key,
            request_hash=digest,
            investigation_id=row.id,
            response_status=202,
            response_body=response,
            created_at=now,
        )
    )
    return 202, response


@router.post("/investigations", status_code=202, response_model=InvestigationReadModel)
async def create_investigation(
    request: Request,
    body: InvestigationCreateRequest,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias=IDEMPOTENCY_HEADER)] = None,
) -> Any:
    key = _idempotency_key(idempotency_key)
    limit_ms = settings(request).max_shared_duration_seconds * 1000
    if body.source.duration_ms is not None and body.source.duration_ms > limit_ms:
        raise ApiError(
            422,
            "DURATION_LIMIT_EXCEEDED",
            f"Shared media is limited to {limit_ms} milliseconds",
            action="fix_request",
        )
    digest = request_hash(body)
    database = engine(request)
    try:
        async with database.begin() as connection:
            status, response = await _create(request, connection, principal, body, key, digest)
    except IntegrityError:
        async with database.connect() as connection:
            status, response = await _replay(connection, principal.id, key, digest)
    return JSONResponse(response, status_code=status)


@router.get("/investigations", response_model=InvestigationListResponse)
async def list_investigations(
    request: Request, principal: CurrentPrincipal
) -> InvestigationListResponse:
    query = (
        owned_rows(investigations, principal)
        .order_by(investigations.c.created_at.desc(), investigations.c.id)
        .limit(_LIST_LIMIT)
    )
    async with engine(request).connect() as connection:
        await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        rows = (await connection.execute(query)).all()
        items = await read_models(connection, list(rows))
    return InvestigationListResponse(items=items)


@router.get("/investigations/{investigation_id}", response_model=InvestigationReadModel)
async def get_investigation(
    request: Request, investigation_id: uuid.UUID, principal: CurrentPrincipal
) -> InvestigationReadModel:
    async with engine(request).connect() as connection:
        await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        row = await load_owned(connection, investigations, investigation_id, principal)
        return (await read_models(connection, [row]))[0]
