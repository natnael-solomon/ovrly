"""Bounded, owner-scoped device text; completion records delivery, never continuous coverage."""

import json
from typing import Annotated, Any, TypeVar
from uuid import UUID

from fastapi import APIRouter, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import Row, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, Principal, load_owned, lock_active_principal
from services.api.errors import ApiError
from services.api.routes.common import engine
from services.api.text_schemas import DeviceTextRead, TextBatch, TextCompletion
from services.jobs.models import DEVICE_TEXT_FENCE_STAGE, job_results, jobs
from services.jobs.queue import JobQueue, StageKey
from services.models import investigations, upload_text, uploads
from services.pipeline.intake import intake_stage_key
from services.pipeline.media_validation import media_stage_key
from services.pipeline.speech import speech_stage_key

router = APIRouter(tags=["investigations"])
MAX_BODY_BYTES = 262_144
MAX_STORED_BYTES = 2_097_152
_T = TypeVar("_T", bound=BaseModel)


def conflict(code: str = "DEVICE_TEXT_CONFLICT") -> ApiError:
    return ApiError(
        409, code, "The device-text submission conflicts with the current source or work"
    )


def unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return value


async def bounded_body(request: Request, model: type[_T]) -> _T:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ApiError(415, "DEVICE_TEXT_CONTENT_TYPE", "Device text requires application/json")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise ApiError(
                413, "DEVICE_TEXT_TOO_LARGE", "Device-text request exceeds its byte limit"
            )
        body.extend(chunk)
    try:
        json.loads(body, object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise ApiError(422, "VALIDATION_FAILED", "The request is invalid at: body") from None
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None


async def eligible(
    connection: AsyncConnection,
    identifier: UUID,
    principal: Principal,
) -> tuple[Row[Any], Row[Any], dict[str, Any], Row[Any] | None]:
    await lock_active_principal(connection, principal)
    # Serialize submissions without blocking a deleting prerequisite's FK-backed fence.
    investigation = await load_owned(
        connection, investigations, identifier, principal, for_no_key_update=True
    )
    if investigation.source_kind != "upload":
        raise conflict("DEVICE_TEXT_SOURCE_INVALID")
    source = await load_owned(connection, uploads, investigation.upload_id, principal)
    key = media_stage_key(identifier)
    keys = [intake_stage_key(identifier), key, speech_stage_key(identifier)]
    rows = (
        await connection.execute(
            select(jobs)
            .where(
                jobs.c.owner_id == principal.id,
                jobs.c.input_hash == key.input_hash,
                jobs.c.stage.in_([item.stage for item in keys]),
                jobs.c.version == key.version,
            )
            .order_by(jobs.c.id)
            .with_for_update()
        )
    ).all()
    if investigation.state == "cancelled" or any(
        row.cancel_requested or row.state in {"cancelled", "deleted"} for row in rows
    ):
        raise conflict("DEVICE_TEXT_INELIGIBLE")
    media = next((row for row in rows if row.stage == key.stage), None)
    if media is None or media.state != "published" or source.state != "completed":
        raise conflict("DEVICE_TEXT_NOT_READY")
    result = await connection.scalar(
        select(job_results.c.result).where(job_results.c.job_id == media.id)
    )
    if (
        result is None
        or result["source_sha256"] != source.declared_sha256
        or not result["coverage"]["media"]["has_video"]
    ):
        raise conflict("DEVICE_TEXT_SOURCE_INVALID")
    document = (
        await connection.execute(select(upload_text).where(upload_text.c.id == identifier))
    ).first()
    if document is not None and document.media_job_id != media.id:
        raise conflict("DEVICE_TEXT_INELIGIBLE")
    return source, media, result, document


async def text_state(connection: AsyncConnection, document: Row[Any] | None) -> str:
    if document is None:
        return "not_started"
    job = (
        await connection.execute(select(jobs).where(jobs.c.id == document.job_id).with_for_update())
    ).first()
    if job is None or job.state == "deleted":
        raise conflict("DEVICE_TEXT_INELIGIBLE")
    if job.cancel_requested or job.state == "cancelled":
        return "cancelled"
    return "completed" if document.completion is not None else "receiving"


async def snapshot(
    connection: AsyncConnection,
    identifier: UUID,
    source: Row[Any],
    document: Row[Any] | None,
) -> DeviceTextRead:
    return DeviceTextRead.model_validate_json(
        json.dumps(
            {
                "investigation_id": identifier,
                "upload_id": source.id,
                "source_sha256": source.declared_sha256,
                "status": await text_state(connection, document),
                "job_id": document.job_id if document is not None else None,
                "batches": document.batches if document is not None else [],
                "completion": document.completion if document is not None else None,
            },
            default=str,
        )
    )


@router.get("/investigations/{investigation_id}/device-text", response_model=DeviceTextRead)
async def get_text(
    request: Request,
    investigation_id: UUID,
    principal: CurrentPrincipal,
) -> DeviceTextRead:
    async with engine(request).begin() as connection:
        source, _, _, document = await eligible(connection, investigation_id, principal)
        return await snapshot(connection, investigation_id, source, document)


async def create_text_document(
    connection: AsyncConnection,
    queue: JobQueue,
    identifier: UUID,
    owner_id: UUID,
    media: Row[Any],
    batches: list[dict[str, Any]],
    completion: dict[str, Any] | None,
) -> Row[Any]:
    queued = await queue.enqueue(
        connection,
        StageKey(1, DEVICE_TEXT_FENCE_STAGE, media.input_hash),
        {"investigation_id": str(identifier)},
        owner_id=owner_id,
    )
    document: Row[Any] = (
        await connection.execute(
            insert(upload_text)
            .values(
                id=identifier,
                job_id=queued.job_id,
                media_job_id=media.id,
                batches=batches,
                completion=completion,
            )
            .returning(upload_text)
        )
    ).one()
    return document


@router.put(
    "/investigations/{investigation_id}/device-text/batches/{batch_id}",
    response_model=DeviceTextRead,
)
async def put_batch(
    request: Request,
    investigation_id: UUID,
    batch_id: Annotated[int, Path(ge=0, le=63)],
    principal: CurrentPrincipal,
) -> DeviceTextRead:
    body = await bounded_body(request, TextBatch)
    async with engine(request).begin() as connection:
        source, media, result, document = await eligible(connection, investigation_id, principal)
        if body.upload_id != source.id or body.source_sha256 != source.declared_sha256:
            raise conflict("DEVICE_TEXT_SOURCE_INVALID")
        if await text_state(connection, document) == "cancelled":
            raise conflict("DEVICE_TEXT_INELIGIBLE")
        if any(frame.frame_pts >= result["coverage"]["total_ms"] for frame in body.frames):
            raise ApiError(
                422, "DEVICE_TEXT_TIMESTAMP_INVALID", "Frame timestamp is outside the media"
            )
        batches = list(document.batches) if document is not None else []
        value = body.model_dump(mode="json")
        previous = next((item for item in batches if item["batch_id"] == batch_id), None)
        if previous is not None:
            if previous["body"] != value:
                raise conflict()
            return await snapshot(connection, investigation_id, source, document)
        if document is not None and document.completion is not None:
            raise conflict()
        if batches and any(
            batches[0]["body"][field] != value[field] for field in ("sampling", "recognizer")
        ):
            raise conflict()
        batches.append({"batch_id": batch_id, "body": value})
        frames = [frame for batch in batches for frame in batch["body"]["frames"]]
        observations = [item for frame in frames for item in frame["text_observations"]]
        if len({frame["frame_pts"] for frame in frames}) != len(frames) or len(
            {item["id"] for item in observations}
        ) != len(observations):
            raise conflict()
        if (
            len(frames) > 1200
            or len(observations) > 5000
            or len(json.dumps(batches).encode()) > MAX_STORED_BYTES
        ):
            raise ApiError(413, "DEVICE_TEXT_TOO_LARGE", "Device text exceeds its source budget")
        batches.sort(key=lambda item: item["batch_id"])
        if document is None:
            document = await create_text_document(
                connection,
                JobQueue(request.app.state.database),
                investigation_id,
                principal.id,
                media,
                batches,
                None,
            )
        else:
            document = (
                await connection.execute(
                    update(upload_text)
                    .where(upload_text.c.id == investigation_id)
                    .values(batches=batches)
                    .returning(upload_text)
                )
            ).one()
        return await snapshot(connection, investigation_id, source, document)


@router.post(
    "/investigations/{investigation_id}/device-text/complete", response_model=DeviceTextRead
)
async def complete_text(
    request: Request,
    investigation_id: UUID,
    principal: CurrentPrincipal,
) -> DeviceTextRead:
    body = await bounded_body(request, TextCompletion)
    async with engine(request).begin() as connection:
        source, media, _, document = await eligible(connection, investigation_id, principal)
        if body.upload_id != source.id or body.source_sha256 != source.declared_sha256:
            raise conflict("DEVICE_TEXT_SOURCE_INVALID")
        if await text_state(connection, document) == "cancelled":
            raise conflict("DEVICE_TEXT_INELIGIBLE")
        batches = document.batches if document is not None else []
        value = body.model_dump(mode="json")
        if document is not None and document.completion is not None:
            if document.completion != value:
                raise conflict()
            return await snapshot(connection, investigation_id, source, document)
        if [item["batch_id"] for item in batches] != list(range(body.batch_count)):
            raise conflict()
        if batches and any(
            batches[0]["body"][field] != value[field] for field in ("sampling", "recognizer")
        ):
            raise conflict()
        if document is None:
            document = await create_text_document(
                connection,
                JobQueue(request.app.state.database),
                investigation_id,
                principal.id,
                media,
                [],
                value,
            )
        else:
            document = (
                await connection.execute(
                    update(upload_text)
                    .where(upload_text.c.id == investigation_id)
                    .values(completion=value)
                    .returning(upload_text)
                )
            ).one()
        return await snapshot(connection, investigation_id, source, document)
