"""Allowlisted voice actions (BC-D04): one owned target, an audit row, idempotent replay.

The server enforces the same allowlist as the Android ``VoiceCommandContract``. Every action
reuses the logic of its REST route: ``save_report`` the save, ``queue_cancel`` the job cancel
with its durable receipt. The queue has no transition out of a terminal job state, so
``queue_retry`` and ``queue_continue`` change nothing: on a queued or running job they are
accepted because the check is already in progress, and on a finished, failed or cancelled
job they are denied with ``VOICE_ACTION_INVALID_STATE``.

Each request writes one ``voice_actions`` row (the audit event and the replay record) in the
same transaction as the action. No transcript or audio reaches this endpoint or is stored.
"""

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, Principal, load_owned
from services.api.errors import ApiError
from services.api.routes.common import engine
from services.api.routes.jobs import cancel_owned_job
from services.api.schemas import (
    VOICE_TARGET_KINDS,
    VoiceActionEnvelope,
    VoiceActionRequest,
    VoiceActionResponse,
    VoiceError,
    VoiceErrorCode,
    VoiceTarget,
)
from services.jobs.models import jobs
from services.jobs.queue import CancelOutcome, JobQueue
from services.jobs.states import JobState
from services.models import investigations, voice_actions
from services.reports import parse_canonical_uuid, save_owned_report

logger = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

_IN_PROGRESS = {JobState.QUEUED.value, JobState.LEASED.value, JobState.RUNNING.value}


class _Denied(Exception):
    def __init__(
        self,
        code: VoiceErrorCode,
        spoken: str,
        detail: str,
        action: Literal["none", "fix_request"] = "none",
    ):
        super().__init__(detail)
        self.code = code
        self.spoken = spoken
        self.detail = detail
        self.action = action


def _validated[Model: BaseModel](model: type[Model], body: Any) -> Model:
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
        raise RequestValidationError(errors) from None


def _well_formed_target(value: Any) -> VoiceTarget | None:
    try:
        return VoiceTarget.model_validate(value)
    except ValidationError:
        return None


def _not_found(kind: str) -> _Denied:
    return _Denied(
        "VOICE_TARGET_NOT_FOUND",
        f"I could not find that {'check' if kind == 'investigation' else kind}.",
        f"No such {kind} is visible to the caller.",
        "fix_request",
    )


async def _owned_job(connection: AsyncConnection, principal: Principal, job_id: uuid.UUID) -> str:
    row = await load_owned(connection, jobs, job_id, principal, for_update=True)
    if row.state == JobState.DELETED.value:
        raise _not_found("job")
    state: str = row.state
    return state


async def _execute(
    request: Request,
    connection: AsyncConnection,
    principal: Principal,
    voice: VoiceActionRequest,
) -> str:
    """Run one allowlisted action; return the accepted message or raise ``_Denied``."""
    kind = voice.target.kind
    target_id = parse_canonical_uuid(voice.target.id)
    if target_id is None:
        raise _not_found(kind)
    try:
        if voice.action == "open_check":
            await load_owned(connection, investigations, target_id, principal)
            return "Opened the check."
        if voice.action == "save_report":
            await save_owned_report(connection, principal, target_id)
            return "Saved the report."
        if voice.action == "queue_cancel":
            queue = JobQueue(request.app.state.database)
            outcome = await cancel_owned_job(connection, queue, target_id, principal)
            if outcome == CancelOutcome.NOT_CANCELLABLE:
                raise _Denied(
                    "VOICE_ACTION_INVALID_STATE",
                    "That check already finished, so it cannot be cancelled.",
                    "The job can no longer be cancelled.",
                )
            if outcome == CancelOutcome.REQUESTED:
                return "Stopping the check."
            return "Cancelled the check."
        state = await _owned_job(connection, principal, target_id)
    except ApiError as exc:
        if exc.code == "NOT_FOUND":
            raise _not_found(kind) from None
        raise
    if state in _IN_PROGRESS:
        return "The check is already in progress."
    if voice.action == "queue_retry":
        raise _Denied(
            "VOICE_ACTION_INVALID_STATE",
            "That check cannot be retried by voice. Start a new check instead.",
            f"Manual retry is not available; the job state is {state}.",
        )
    raise _Denied(
        "VOICE_ACTION_INVALID_STATE",
        "That check already stopped, so there is nothing to continue.",
        f"queue_continue requires a queued or running job; the job state is {state}.",
    )


def _denied(
    envelope: VoiceActionEnvelope, target: VoiceTarget | None, denial: _Denied
) -> VoiceActionResponse:
    return VoiceActionResponse(
        request_id=envelope.request_id,
        result="denied",
        action=envelope.action,
        target=target,
        message=denial.spoken,
        error=VoiceError(
            code=denial.code,
            message=denial.detail,
            retryable=False,
            action=denial.action,
            request_id=envelope.request_id,
        ),
    )


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        raise ApiError(
            422, "VALIDATION_FAILED", "The request is invalid at: body", action="fix_request"
        ) from None


@router.post("/voice/actions", response_model=VoiceActionResponse, response_model_exclude_none=True)
async def voice_action(request: Request, principal: CurrentPrincipal) -> JSONResponse:
    body = await _json_body(request)
    envelope = _validated(VoiceActionEnvelope, body)
    supported = envelope.action in VOICE_TARGET_KINDS
    voice = _validated(VoiceActionRequest, body) if supported else None
    target = voice.target if voice is not None else _well_formed_target(envelope.target)
    target_kind = target.kind if target is not None else None
    target_id = target.id if target is not None else None
    async with engine(request).begin() as connection:
        # Serialise one caller's repeats of a request_id so the action runs at most once.
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"voice:{principal.id}:{envelope.request_id}"},
        )
        stored = (
            await connection.execute(
                select(voice_actions).where(
                    voice_actions.c.owner_id == principal.id,
                    voice_actions.c.request_id == envelope.request_id,
                )
            )
        ).first()
        if stored is not None:
            if (stored.action, stored.target_kind, stored.target_id) != (
                envelope.action,
                target_kind,
                target_id,
            ):
                raise ApiError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The request_id was already used for a different voice action",
                    action="fix_request",
                )
            replay: dict[str, Any] = stored.response
            return JSONResponse(replay)
        if voice is None:
            response = _denied(
                envelope,
                target,
                _Denied(
                    "VOICE_ACTION_UNSUPPORTED",
                    "That action is not available by voice.",
                    "The action is not in the voice allowlist.",
                    "fix_request",
                ),
            )
        else:
            try:
                message = await _execute(request, connection, principal, voice)
            except _Denied as denial:
                response = _denied(envelope, target, denial)
            else:
                response = VoiceActionResponse(
                    request_id=voice.request_id,
                    result="accepted",
                    action=voice.action,
                    target=voice.target,
                    message=message,
                )
        payload = response.model_dump(mode="json", exclude_none=True)
        await connection.execute(
            insert(voice_actions).values(
                owner_id=principal.id,
                request_id=envelope.request_id,
                action=envelope.action,
                target_kind=target_kind,
                target_id=target_id,
                result=response.result,
                error_code=response.error.code if response.error is not None else None,
                response=payload,
                created_at=func.now(),
            )
        )
    logger.info(
        "Voice action %s %s", envelope.action if supported else "unsupported", response.result
    )
    return JSONResponse(payload)
