"""Owner-scoped job actions, serialized with worker writes and durable replay receipts."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, Principal, load_owned
from services.api.auth.ownership import not_found
from services.api.errors import ApiError
from services.api.routes.common import engine
from services.jobs.models import jobs
from services.jobs.queue import CancelOutcome, JobQueue
from services.jobs.states import JobState

router = APIRouter(tags=["jobs"])


class CancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    cancellation: Literal["requested", "effective"]


class DeleteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    state: Literal["deleted"] = "deleted"
    access_revoked: Literal[True] = True
    cleanup_status: Literal["complete"] = "complete"
    cleanup_scope: Literal["job_payload_and_result"] = "job_payload_and_result"


async def require_empty_body(request: Request) -> None:
    # These commands have no body: even a client-supplied identity must not be ignored.
    async for chunk in request.stream():
        if chunk:
            raise ApiError(
                422, "VALIDATION_FAILED", "This operation accepts no body", action="fix_request"
            )


async def cancel_owned_job(
    connection: AsyncConnection, queue: JobQueue, job_id: UUID, principal: Principal
) -> CancelOutcome:
    """Cancel one of the caller's jobs inside ``connection``; replays the stored receipt.

    Shared by the cancel route and the ``queue_cancel`` voice action. Missing, other-owner,
    legacy ownerless and deleted jobs raise the same 404.
    """
    row = await load_owned(connection, jobs, job_id, principal, for_update=True)
    # Deletion revokes reads, including a previously stored cancellation receipt.
    if row.state == JobState.DELETED.value:
        raise not_found()
    if row.cancel_outcome is not None:
        return CancelOutcome(row.cancel_outcome)
    outcome = (
        CancelOutcome.EFFECTIVE
        if row.state == JobState.CANCELLED.value
        else await queue.request_cancel(job_id, connection=connection)
    )
    await connection.execute(
        update(jobs).where(jobs.c.id == job_id).values(cancel_outcome=outcome.value)
    )
    return outcome


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=CancelResponse,
    responses={202: {"model": CancelResponse}},
)
async def cancel_job(request: Request, job_id: UUID, principal: CurrentPrincipal) -> JSONResponse:
    await require_empty_body(request)
    queue = JobQueue(request.app.state.database)
    async with engine(request).begin() as connection:
        outcome = await cancel_owned_job(connection, queue, job_id, principal)
    if outcome == CancelOutcome.NOT_CANCELLABLE:
        raise ApiError(409, "JOB_NOT_CANCELLABLE", "The job can no longer be cancelled")
    response = CancelResponse(
        job_id=job_id,
        cancellation="requested" if outcome == CancelOutcome.REQUESTED else "effective",
    )
    return JSONResponse(
        response.model_dump(mode="json"),
        status_code=202 if outcome == CancelOutcome.REQUESTED else 200,
    )


@router.delete("/jobs/{job_id}", response_model=DeleteResponse)
async def delete_job(request: Request, job_id: UUID, principal: CurrentPrincipal) -> DeleteResponse:
    await require_empty_body(request)
    queue = JobQueue(request.app.state.database)
    async with engine(request).begin() as connection:
        await load_owned(connection, jobs, job_id, principal, for_update=True)
        await queue.delete(job_id, connection=connection)
    return DeleteResponse(job_id=job_id)
