"""Explicit retries reserve quota before making only missing speech runnable."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Request
from sqlalchemy import Row, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import CurrentPrincipal, load_owned, lock_active_principal
from services.api.errors import ApiError
from services.api.routes.common import engine, settings
from services.api.routes.investigations import _idempotency_key
from services.api.schemas import SpeechRetryOutcome, SpeechRetryRequest, SpeechRetryResponse
from services.asr.groq import ASRQuotaExhausted, ASRUnavailable
from services.asr.reservations import reserve_in
from services.captures import chunk_jobs, stage_key
from services.jobs.models import job_results, jobs
from services.models import (
    capture_chunks,
    capture_sessions,
    investigations,
    speech_retries,
    upload_text,
    uploads,
)
from services.pipeline.media_validation import media_stage_key
from services.pipeline.speech import _read_audio, speech_settings_hash
from services.settings import Settings

router = APIRouter(tags=["investigations"])
_ACTIVE = {"queued", "leased", "running"}


@router.post("/investigations/{investigation_id}/speech/retry", response_model=SpeechRetryResponse)
async def retry_speech(
    request: Request,
    investigation_id: UUID,
    body: SpeechRetryRequest,
    principal: CurrentPrincipal,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> SpeechRetryResponse:
    del body
    key = _idempotency_key(idempotency_key)
    config = settings(request)
    response = SpeechRetryResponse(investigation_id=investigation_id, outcome="not_eligible")
    async with engine(request).begin() as connection:
        await lock_active_principal(connection, principal)
        source_kind = (
            await load_owned(connection, investigations, investigation_id, principal)
        ).source_kind
        if source_kind == "capture":
            blocked = await _lock_capture(connection, investigation_id, principal.id)
            previous = await _previous(connection, principal.id, key, investigation_id)
            if previous is not None:
                return previous
            if blocked is None:
                return response
            response.outcome = await _capture_outcome(request, connection, config, blocked)
            await _record(connection, principal.id, key, response)
            return response
        investigation = await load_owned(
            connection, investigations, investigation_id, principal, for_no_key_update=True
        )
        media_key = media_stage_key(investigation_id)
        stages = (
            await connection.execute(
                select(jobs)
                .where(
                    jobs.c.owner_id == principal.id,
                    jobs.c.version == media_key.version,
                    jobs.c.input_hash == media_key.input_hash,
                    jobs.c.stage.in_(["intake", "media_validation", "upload_asr"]),
                )
                .order_by(jobs.c.id)
                .with_for_update()
            )
        ).all()
        fence = (
            await connection.execute(
                select(upload_text).where(upload_text.c.id == investigation_id)
            )
        ).first()
        previous = await _previous(connection, principal.id, key, investigation_id)
        if previous is not None:
            return previous
        if (
            investigation.state == "cancelled"
            or investigation.source_kind != "upload"
            or any(row.cancel_requested or row.state in {"deleted", "cancelled"} for row in stages)
            or (fence is not None and fence.media_job_id is None)
        ):
            return response
        media = next((row for row in stages if row.stage == "media_validation"), None)
        speech = next((row for row in stages if row.stage == "upload_asr"), None)
        if speech is not None:
            if speech.state == "published":
                response.outcome = "already_complete"
            elif speech.state in {"queued", "leased", "running"}:
                response.outcome = "in_progress"
            elif (
                speech.state == "failed"
                and speech.failure == "ASRQuotaExhausted"
                and config.asr_enabled
                and config.asr_configured
                and media is not None
                and media.state == "published"
                and speech.payload.get("settings_sha256") == speech_settings_hash(config)
            ):
                source = await load_owned(connection, uploads, investigation.upload_id, principal)
                prepared = await connection.scalar(
                    select(job_results.c.result).where(job_results.c.job_id == media.id)
                )
                if (
                    source.state == "completed"
                    and prepared is not None
                    and prepared["source_sha256"] == source.declared_sha256
                    and source.declared_size_bytes <= config.upload_max_bytes
                    and prepared["coverage"]["total_ms"]
                    <= config.max_shared_duration_seconds * 1000
                    and prepared["audio_artifact"] is not None
                ):
                    try:
                        path = await request.app.state.upload_store.local_path(source.storage_key)
                        if not await asyncio.to_thread(path.is_file):
                            raise ASRUnavailable
                        await asyncio.to_thread(
                            _read_audio,
                            config.artifacts_dir,
                            prepared["audio_artifact"],
                            config.asr_audio_max_bytes,
                        )
                        number = int(speech.payload.get("speech_retry", 0)) + 1
                        await reserve_in(
                            connection,
                            speech.id,
                            config,
                            prepared["audio_artifact"]["duration_seconds"],
                            number * 100,
                        )
                    except ASRQuotaExhausted:
                        response.outcome = "quota_exhausted"
                    except (ASRUnavailable, OSError):
                        pass
                    else:
                        await connection.execute(
                            update(jobs)
                            .where(jobs.c.id == speech.id)
                            .values(
                                state="queued",
                                generation=jobs.c.generation + 1,
                                failure=None,
                                retry_class=None,
                                retry_counts={},
                                payload={**speech.payload, "speech_retry": number},
                            )
                        )
                        response.outcome = "accepted"
        await _record(connection, principal.id, key, response)
    return response


async def _previous(
    connection: AsyncConnection, owner: UUID, key: str, investigation_id: UUID
) -> SpeechRetryResponse | None:
    previous = (
        await connection.execute(
            select(speech_retries).where(
                speech_retries.c.owner_id == owner,
                speech_retries.c.request_key == key,
            )
        )
    ).first()
    if previous is None:
        return None
    if previous.investigation_id != investigation_id:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "The retry key names different work")
    return SpeechRetryResponse.model_validate(previous.response)


async def _record(
    connection: AsyncConnection, owner: UUID, key: str, response: SpeechRetryResponse
) -> None:
    await connection.execute(
        insert(speech_retries).values(
            owner_id=owner,
            request_key=key,
            investigation_id=response.investigation_id,
            response=response.model_dump(mode="json"),
        )
    )


@dataclass(frozen=True)
class _CaptureSpeech:
    chunks: Sequence[Row[Any]]
    rows: Sequence[Row[Any]]


async def _lock_capture(
    connection: AsyncConnection, capture_id: UUID, owner: UUID
) -> _CaptureSpeech | None:
    """Lock in the publish order (session, then jobs); None means no eligible retry."""
    session = (
        await connection.execute(
            select(capture_sessions)
            .where(capture_sessions.c.id == capture_id, capture_sessions.c.owner_id == owner)
            .with_for_update()
        )
    ).first()
    if session is None or session.continue_research is False:
        return None
    chunks = (
        await connection.execute(
            select(capture_chunks).where(
                capture_chunks.c.session_id == capture_id,
                capture_chunks.c.received_at.is_not(None),
            )
        )
    ).all()
    rows = (
        await connection.execute(
            select(jobs, job_results.c.result)
            .outerjoin(job_results, job_results.c.job_id == jobs.c.id)
            .where(
                chunk_jobs(capture_id, [chunk.seq for chunk in chunks]), jobs.c.owner_id == owner
            )
            .order_by(jobs.c.id)
            .with_for_update(of=jobs)
        )
    ).all()
    state = await connection.scalar(
        select(investigations.c.state).where(investigations.c.id == capture_id)
    )
    if state == "cancelled" or any(
        row.cancel_requested or row.state in {"deleted", "cancelled"} for row in rows
    ):
        return None
    return _CaptureSpeech(chunks, rows)


async def _capture_outcome(
    request: Request, connection: AsyncConnection, config: Settings, locked: _CaptureSpeech
) -> SpeechRetryOutcome:
    """Reserve every quota-blocked chunk together, then requeue only those chunks."""
    by_hash = {(row.stage, row.input_hash): row for row in locked.rows}
    store = request.app.state.upload_store
    blocked: list[tuple[Row[Any], float]] = []
    active = False
    for chunk in locked.chunks:
        digest = stage_key(chunk.session_id, chunk.seq).input_hash
        validation = by_hash.get(("media_validation", digest))
        speech = by_hash.get(("asr", digest))
        if validation is None or validation.state != "published":
            active = active or (validation is not None and validation.state in _ACTIVE)
            continue
        if speech is None or speech.state in _ACTIVE:
            active = True
            continue
        package = (validation.result or {}).get("package")
        if (
            speech.state == "failed"
            and speech.failure == "ASRQuotaExhausted"
            and config.asr_enabled
            and config.asr_configured
            and package is not None
            and package["audio"] is not None
            and await store.digest(chunk.storage_key) == (chunk.size_bytes, chunk.sha256)
        ):
            blocked.append((speech, package["audio"]["duration_ms"] / 1000))
    if not blocked:
        if active:
            return "in_progress"
        if any(row.stage == "asr" and row.state == "published" for row in locked.rows):
            return "already_complete"
        return "not_eligible"
    try:
        async with connection.begin_nested():
            for speech, seconds in blocked:
                number = int(speech.payload.get("speech_retry", 0)) + 1
                await reserve_in(connection, speech.id, config, seconds, number * 100)
    except ASRQuotaExhausted:
        return "quota_exhausted"
    for speech, _ in blocked:
        await connection.execute(
            update(jobs)
            .where(jobs.c.id == speech.id)
            .values(
                state="queued",
                generation=jobs.c.generation + 1,
                failure=None,
                retry_class=None,
                retry_counts={},
                payload={
                    **speech.payload,
                    "speech_retry": int(speech.payload.get("speech_retry", 0)) + 1,
                },
            )
        )
    return "accepted"
