"""Investigations: idempotent creation with a durable record first, owner-scoped reads."""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Row, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, Principal, load_owned, owned_rows
from services.api.errors import ApiError, safe_error
from services.api.intake import InvestigationDispatcher
from services.api.routes.common import engine, settings
from services.api.schemas import (
    InvestigationCreateRequest,
    InvestigationListResponse,
    InvestigationResponse,
    UploadSource,
)
from services.jobs.models import jobs
from services.models import idempotency_keys, investigations, uploads
from services.pipeline.intake import (
    COVERAGE_PLACEHOLDER,
    INITIAL_STATE,
    INTAKE_STAGE,
    INTAKE_VERSION,
    intake_stage_key,
)

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


def _source(row: Row[Any]) -> dict[str, Any]:
    source: dict[str, Any] = {"kind": row.source_kind}
    if row.source_kind == "url":
        source["url"] = row.source_url
    else:
        source["upload_id"] = str(row.upload_id)
    if row.declared_duration_ms is not None:
        source["duration_ms"] = row.declared_duration_ms
    return source


def investigation_response(row: Row[Any], job_state: str | None = None) -> InvestigationResponse:
    """Build the client view; ``job_state`` (when a job exists) overrides the stored state.

    A published job means the stage finished and the stored state stands.
    """
    state = row.state
    error_code = row.error_code
    if job_state is not None and job_state != "published":
        state = _JOB_STATE_TO_INVESTIGATION[job_state]
        if state == "failed" and error_code is None:
            error_code = PROCESSING_FAILED
    return InvestigationResponse(
        id=row.id,
        state=state,
        stage=row.stage,
        coverage=COVERAGE_PLACEHOLDER,
        version=row.version,
        error=safe_error(error_code),
        source=_source(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def job_states(
    connection: AsyncConnection, investigation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """State of each investigation's intake job, keyed by investigation id."""
    if not investigation_ids:
        return {}
    keys = {intake_stage_key(identifier).input_hash: identifier for identifier in investigation_ids}
    rows = await connection.execute(
        select(jobs.c.input_hash, jobs.c.state).where(
            jobs.c.version == INTAKE_VERSION,
            jobs.c.stage == INTAKE_STAGE,
            jobs.c.input_hash.in_(list(keys)),
        )
    )
    return {keys[row.input_hash]: row.state for row in rows}


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
    response = investigation_response(row).model_dump(mode="json")
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
    dispatcher: InvestigationDispatcher = request.app.state.dispatcher
    await dispatcher.dispatch(connection, row.id, principal.id)
    return 202, response


@router.post("/investigations", status_code=202, response_model=InvestigationResponse)
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
        rows = (await connection.execute(query)).all()
        states = await job_states(connection, [row.id for row in rows])
    return InvestigationListResponse(
        items=[investigation_response(row, states.get(row.id)) for row in rows]
    )


@router.get("/investigations/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(
    request: Request, investigation_id: uuid.UUID, principal: CurrentPrincipal
) -> InvestigationResponse:
    async with engine(request).connect() as connection:
        row = await load_owned(connection, investigations, investigation_id, principal)
        states = await job_states(connection, [row.id])
    return investigation_response(row, states.get(row.id))
