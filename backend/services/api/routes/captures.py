"""Owner-scoped capture intake with durable reservations and per-chunk enqueue."""

import hashlib
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import Row, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from services.api.auth import CurrentPrincipal, load_owned
from services.api.capture_schemas import (
    MAX_CAPTURE_MS,
    CaptureChunk,
    CaptureCloseRequest,
    CaptureCreateRequest,
    CaptureMetadata,
    CaptureSession,
    CaptureStatus,
    CaptureWork,
)
from services.api.errors import ApiError, safe_error
from services.api.routes.common import engine, settings, upload_store
from services.api.routes.investigations import _idempotency_key
from services.captures import gaps, interval, manifest, session_response, stage_key
from services.jobs.models import jobs
from services.jobs.queue import JobQueue
from services.models import capture_chunks, capture_sessions, investigations, principals

router = APIRouter(tags=["captures"])


def invalid(code: str, message: str, status: int = 409) -> ApiError:
    return ApiError(status, code, message, action="fix_request")


async def chunks_for(connection: AsyncConnection, capture_id: uuid.UUID) -> Sequence[Row[Any]]:
    return (
        await connection.execute(
            select(capture_chunks)
            .where(capture_chunks.c.session_id == capture_id)
            .order_by(capture_chunks.c.seq)
        )
    ).all()


async def database_time(connection: AsyncConnection) -> datetime:
    now: datetime = (await connection.execute(select(func.clock_timestamp()))).scalar_one()
    return now


@router.post("/captures", status_code=201, response_model=CaptureSession)
async def create_capture(
    request: Request,
    body: CaptureCreateRequest,
    principal: CurrentPrincipal,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CaptureSession:
    key = _idempotency_key(idempotency_key)
    async with engine(request).begin() as connection:
        # Match retention's lock order; also serialize concurrent create replays.
        await connection.execute(
            select(principals.c.id).where(principals.c.id == principal.id).with_for_update()
        )
        now = await database_time(connection)
        session = (
            await connection.execute(
                select(capture_sessions).where(
                    capture_sessions.c.owner_id == principal.id,
                    capture_sessions.c.request_key == key,
                )
            )
        ).first()
        if session is not None:
            if session.chunk_duration_ms != body.chunk_duration_ms:
                raise invalid("IDEMPOTENCY_KEY_REUSED", "The key names a different capture request")
            # Replay the original open/empty creation acknowledgement, not current progress.
            return session_response(session, [], session.started_at).model_copy(
                update={"state": "open", "closed_at": None}
            )
        identifier = uuid.uuid4()
        await connection.execute(
            insert(investigations).values(
                id=identifier,
                owner_id=principal.id,
                source_kind="capture",
                source_url=None,
                upload_id=None,
                declared_duration_ms=None,
                state="queued",
                stage="media_validation",
                version=1,
                error_code=None,
                created_at=now,
                updated_at=now,
            )
        )
        session = (
            await connection.execute(
                insert(capture_sessions)
                .values(
                    id=identifier,
                    owner_id=principal.id,
                    request_key=key,
                    chunk_duration_ms=body.chunk_duration_ms,
                    state="open",
                    started_at=now,
                    closed_at=None,
                    duration_ms=None,
                    continue_research=None,
                    expires_at=now
                    + timedelta(
                        milliseconds=MAX_CAPTURE_MS,
                        seconds=settings(request).upload_target_seconds,
                    ),
                )
                .returning(capture_sessions)
            )
        ).one()
        return session_response(session, [], now)


class BodyTooLarge(MultiPartException):
    pass


@asynccontextmanager
async def chunk_body(request: Request) -> AsyncIterator[tuple[CaptureMetadata, UploadFile]]:
    limit = settings(request).upload_max_bytes

    async def bounded() -> AsyncGenerator[bytes, None]:
        size = 0
        async for block in request.stream():
            size += len(block)
            if size > limit + 16_384:
                raise BodyTooLarge("Capture body exceeds the limit")
            yield block

    if request.headers.get("content-type", "").split(";")[0].strip() != "multipart/form-data":
        raise invalid("VALIDATION_FAILED", "Use multipart/form-data", 422)
    parser = MultiPartParser(
        request.headers, bounded(), max_files=1, max_fields=1, max_part_size=8192
    )
    form: FormData | None = None
    try:
        form = await parser.parse()
        if sorted(form.keys()) != ["content", "metadata"] or len(form.multi_items()) != 2:
            raise invalid("VALIDATION_FAILED", "Provide exactly metadata and content parts", 422)
        raw, content = form["metadata"], form["content"]
        if not isinstance(raw, str) or not isinstance(content, UploadFile):
            raise invalid("VALIDATION_FAILED", "Metadata is JSON text; content is a file", 422)
        try:
            metadata = CaptureMetadata.model_validate_json(raw)
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from None
        if metadata.chunk.size_bytes > limit:
            raise BodyTooLarge("Chunk exceeds the limit")
        digest = hashlib.sha256()
        size = 0
        async for block in file_blocks(content):
            size += len(block)
            digest.update(block)
        if size != metadata.chunk.size_bytes or digest.hexdigest() != metadata.chunk.sha256:
            raise invalid("CAPTURE_CHUNK_CONFLICT", "Chunk bytes do not match their declaration")
        await content.seek(0)
        yield metadata, content
    except BodyTooLarge:
        raise invalid("CAPTURE_TOO_LARGE", "Capture byte limit exceeded", 413) from None
    except MultiPartException:
        raise invalid("VALIDATION_FAILED", "Invalid capture multipart body", 422) from None
    finally:
        # The parser owns cleanup until it returns; we own the returned form thereafter.
        if form is not None:
            await form.close()


async def file_blocks(content: UploadFile) -> AsyncIterator[bytes]:
    while block := await content.read(64 * 1024):
        yield block


def require_open(session: Row[Any], now: datetime) -> None:
    if session.state != "open":
        raise invalid("CAPTURE_CLOSED", "New chunks are not accepted after close")
    if session.expires_at <= now:
        raise invalid("CAPTURE_EXPIRED", "The capture upload window has expired", 410)


async def reserve(
    request: Request,
    capture_id: uuid.UUID,
    seq: int,
    principal: CurrentPrincipal,
    metadata: CaptureMetadata,
) -> None:
    chunk = metadata.chunk
    async with engine(request).begin() as connection:
        session = await load_owned(
            connection, capture_sessions, capture_id, principal, for_update=True
        )
        chunks = await chunks_for(connection, capture_id)
        if chunk.session_id != capture_id or chunk.seq != seq:
            raise invalid("VALIDATION_FAILED", "Chunk identifiers must match the path", 422)
        start = seq * session.chunk_duration_ms
        if chunk.interval.start_ms != start or chunk.interval.end_ms > min(
            start + session.chunk_duration_ms, MAX_CAPTURE_MS
        ):
            raise invalid("VALIDATION_FAILED", "Chunk interval does not match its sequence", 422)
        existing = next((c for c in chunks if c.seq == seq), None)
        if existing is not None:
            if (
                existing.end_ms != chunk.interval.end_ms
                or existing.size_bytes != chunk.size_bytes
                or existing.sha256 != chunk.sha256
                or existing.content_type != chunk.content_type
                or existing.modality != metadata.modality
            ):
                raise invalid("CAPTURE_CHUNK_CONFLICT", "The sequence has different content")
            if existing.received_at is not None:
                return
        require_open(session, await database_time(connection))
        if existing is not None:
            return
        if any(
            (c.seq < seq and c.end_ms < (c.seq + 1) * session.chunk_duration_ms)
            or (c.seq > seq and chunk.interval.end_ms < start + session.chunk_duration_ms)
            for c in chunks
        ):
            raise invalid("CAPTURE_FINAL_CHUNK", "A short chunk must be the final sequence")
        if (
            sum(c.size_bytes for c in chunks) + chunk.size_bytes
            > settings(request).upload_max_bytes
        ):
            raise invalid("CAPTURE_TOO_LARGE", "Capture byte limit exceeded", 413)
        await connection.execute(
            insert(capture_chunks).values(
                session_id=capture_id,
                seq=seq,
                end_ms=chunk.interval.end_ms,
                size_bytes=chunk.size_bytes,
                sha256=chunk.sha256,
                content_type=chunk.content_type,
                modality=metadata.modality,
                storage_key=uuid.uuid4().hex,
                received_at=None,
                job_id=None,
            )
        )


@router.put("/captures/{capture_id}/chunks/{seq}", response_model=CaptureChunk)
async def put_chunk(
    request: Request, capture_id: uuid.UUID, seq: int, principal: CurrentPrincipal
) -> CaptureChunk:
    # Authorize before parsing/spooling media.
    async with engine(request).connect() as connection:
        await load_owned(connection, capture_sessions, capture_id, principal)
    async with chunk_body(request) as (metadata, content):
        await reserve(request, capture_id, seq, principal, metadata)
        async with engine(request).begin() as connection:
            await connection.execute(
                select(principals.c.id)
                .where(principals.c.id == principal.id)
                .with_for_update(read=True, key_share=True)
            )
            session = await load_owned(
                connection, capture_sessions, capture_id, principal, for_update=True
            )
            chunks = await chunks_for(connection, capture_id)
            stored = next((c for c in chunks if c.seq == seq), None)
            if stored is None:
                require_open(session, await database_time(connection))
                raise invalid("CAPTURE_CHUNK_CONFLICT", "The chunk reservation no longer exists")
            received = [c.seq for c in chunks if c.received_at is not None]
            disposition: Literal["stored", "duplicate", "out_of_order"] = "duplicate"
            if stored.received_at is None:
                require_open(session, await database_time(connection))
                await upload_store(request).write(
                    stored.storage_key, file_blocks(content), stored.size_bytes
                )
                now = await database_time(connection)
                require_open(session, now)
                queued = await JobQueue(request.app.state.database).enqueue(
                    connection,
                    stage_key(capture_id, seq),
                    {"session_id": str(capture_id), "seq": seq, "owner_id": str(principal.id)},
                    owner_id=principal.id,
                )
                stored = (
                    await connection.execute(
                        update(capture_chunks)
                        .where(
                            capture_chunks.c.session_id == capture_id, capture_chunks.c.seq == seq
                        )
                        .values(received_at=now, job_id=queued.job_id)
                        .returning(capture_chunks)
                    )
                ).one()
                disposition = (
                    "stored" if seq == len(received) and not gaps(received) else "out_of_order"
                )
                received.append(seq)
            return CaptureChunk(
                session_id=capture_id,
                seq=seq,
                interval=interval(seq * session.chunk_duration_ms, stored.end_ms),
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                disposition=disposition,
                received_at=stored.received_at,
                gaps=gaps(received),
            )


@router.post("/captures/{capture_id}/close", response_model=CaptureSession)
async def close_capture(
    request: Request, capture_id: uuid.UUID, body: CaptureCloseRequest, principal: CurrentPrincipal
) -> CaptureSession:
    async with engine(request).begin() as connection:
        session = await load_owned(
            connection, capture_sessions, capture_id, principal, for_update=True
        )
        chunks = await chunks_for(connection, capture_id)
        received = [c for c in chunks if c.received_at is not None]
        duration = body.duration_ms
        if duration is None:
            duration = max((c.end_ms for c in received), default=0)
        if session.state == "closed":
            if (
                session.continue_research != body.continue_research
                or session.duration_ms != duration
            ):
                raise invalid("CAPTURE_CLOSE_CONFLICT", "The capture already has a different close")
        else:
            if any(c.end_ms > duration for c in received):
                raise invalid("VALIDATION_FAILED", "Duration cannot truncate received chunks", 422)
            if any(
                c.end_ms < (c.seq + 1) * session.chunk_duration_ms and c.end_ms != duration
                for c in received
            ):
                raise invalid("CAPTURE_FINAL_CHUNK", "Duration must end with the short final chunk")
            now = await database_time(connection)
            session = (
                await connection.execute(
                    update(capture_sessions)
                    .where(capture_sessions.c.id == capture_id)
                    .values(
                        state="closed",
                        closed_at=now,
                        duration_ms=duration,
                        continue_research=body.continue_research,
                    )
                    .returning(capture_sessions)
                )
            ).one()
            if not body.continue_research:
                queue = JobQueue(request.app.state.database)
                for chunk in received:
                    if chunk.job_id is not None:
                        await queue.request_cancel(chunk.job_id, connection=connection)
            await connection.execute(
                update(investigations)
                .where(investigations.c.id == capture_id)
                .values(
                    declared_duration_ms=duration,
                    state="queued" if body.continue_research else "cancelled",
                    updated_at=now,
                )
            )
        return session_response(session, chunks, await database_time(connection))


@router.get("/captures/{capture_id}", response_model=CaptureStatus)
async def capture_status(
    request: Request, capture_id: uuid.UUID, principal: CurrentPrincipal
) -> CaptureStatus:
    async with engine(request).connect() as connection:
        await connection.execution_options(
            isolation_level="REPEATABLE READ", postgresql_readonly=True
        )
        session = await load_owned(connection, capture_sessions, capture_id, principal)
        chunks = await chunks_for(connection, capture_id)
        states = {
            row.id: row
            for row in (
                await connection.execute(
                    select(jobs).where(
                        jobs.c.id.in_([c.job_id for c in chunks if c.job_id is not None]),
                        jobs.c.owner_id == principal.id,
                    )
                )
            ).all()
        }
        work = []
        for chunk in chunks:
            if chunk.received_at is None:
                continue
            job = states.get(chunk.job_id)
            state = job.state if job is not None else "deleted"
            status: Literal["waiting", "checking", "failed", "cancelled"] = "waiting"
            if session.continue_research is False or state in {"cancelled", "deleted"}:
                status = "cancelled"
            elif state == "failed":
                status = "failed"
            elif state in {"leased", "running"}:
                status = "checking"
            work.append(
                CaptureWork(
                    seq=chunk.seq,
                    job_id=chunk.job_id,
                    processing_status=status,
                    error=safe_error("PROCESSING_FAILED" if status == "failed" else None),
                )
            )
        return CaptureStatus(
            session=session_response(session, chunks, await database_time(connection)),
            continue_research=session.continue_research,
            expires_at=session.expires_at,
            manifest=manifest(session, chunks),
            work=work,
            claims=[],
        )
