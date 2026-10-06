"""Upload speech stage. Media facts and timed speech remain separate."""

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from services.asr.groq import ASRAdapter, ASRUnavailable, GroqAdapter, transcription_parameters
from services.asr.reservations import complete, reserve, resume
from services.jobs.faults import Checkpoint
from services.jobs.handlers import JobContext, JobHandler
from services.jobs.models import job_results, jobs
from services.jobs.queue import ClaimedJob, StageKey
from services.jobs.retries import RetryableError
from services.models import investigations, uploads
from services.pipeline.intake import _identifier
from services.pipeline.media_validation import _heartbeat_while, media_stage_key
from services.settings import Settings

# Capture chunks use the separate per-chunk ``asr`` stage (services.pipeline.capture_media).
ASR_STAGE = "upload_asr"
ASR_VERSION = 1


def speech_stage_key(identifier: uuid.UUID) -> StageKey:
    digest = hashlib.sha256(f"investigation:{identifier}".encode()).hexdigest()
    return StageKey(ASR_VERSION, ASR_STAGE, digest)


def speech_settings_hash(settings: Settings) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "version": ASR_VERSION,
                **transcription_parameters(settings.groq_model),
                "audio_max_bytes": settings.asr_audio_max_bytes,
                "response_max_bytes": settings.asr_response_max_bytes,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _read_audio(root: Path, artifact: dict[str, Any], maximum: int) -> bytes:
    path = (root / str(artifact["key"])).resolve()
    if not path.is_relative_to(root.resolve()) or artifact["size_bytes"] > maximum:
        raise ASRUnavailable
    try:
        with path.open("rb") as stream:
            audio = stream.read(maximum + 1)
    except OSError:
        raise ASRUnavailable from None
    if (
        len(audio) != artifact["size_bytes"]
        or len(audio) > maximum
        or hashlib.sha256(audio).hexdigest() != artifact["sha256"]
    ):
        raise ASRUnavailable
    return audio


def speech_provider(settings: Settings, adapter: ASRAdapter | None = None) -> ASRAdapter:
    return adapter or GroqAdapter(
        api_key=settings.groq_api_key.get_secret_value(),
        model=settings.groq_model,
        max_audio_bytes=settings.asr_audio_max_bytes,
        max_response_bytes=settings.asr_response_max_bytes,
        timeout_seconds=settings.asr_timeout_seconds,
        max_retry_after_seconds=settings.job_retry_max_backoff_seconds,
    )


def build_speech(
    settings: Settings,
    *,
    adapter: ASRAdapter | None = None,
    quota_clock: Callable[[], datetime] | None = None,
) -> JobHandler:
    provider = speech_provider(settings, adapter)

    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        identifier = _identifier(job.payload, "investigation_id")
        owner = _identifier(job.payload, "owner_id")
        if (
            not settings.asr_enabled
            or job.key != speech_stage_key(identifier)
            or job.payload.get("settings_sha256") != speech_settings_hash(settings)
        ):
            raise ASRUnavailable
        key = media_stage_key(identifier)
        async with context.queue.database.engine.connect() as connection:
            media = (
                await connection.execute(
                    select(job_results.c.result)
                    .join(jobs, jobs.c.id == job_results.c.job_id)
                    .where(
                        jobs.c.id == _identifier(job.payload, "media_job_id"),
                        jobs.c.version == key.version,
                        jobs.c.stage == key.stage,
                        jobs.c.input_hash == key.input_hash,
                        jobs.c.state == "published",
                    )
                )
            ).scalar_one_or_none()
            source = (
                await connection.execute(
                    select(uploads.c.declared_sha256)
                    .join(investigations, investigations.c.upload_id == uploads.c.id)
                    .where(
                        investigations.c.id == identifier,
                        investigations.c.owner_id == owner,
                        uploads.c.owner_id == owner,
                        uploads.c.state == "completed",
                    )
                )
            ).scalar_one_or_none()
        if media is None or source != media["source_sha256"] or media["audio_artifact"] is None:
            raise ASRUnavailable
        previous = await resume(job, context)
        if previous is not None:
            return previous
        artifact = media["audio_artifact"]
        audio = await asyncio.to_thread(
            _read_audio, settings.artifacts_dir, artifact, settings.asr_audio_max_bytes
        )
        request_id = await reserve(
            job, context, settings, artifact["duration_seconds"], quota_clock
        )
        await context.checkpoint(Checkpoint.BEFORE_PROVIDER_CALL, job)
        await context.heartbeat()
        try:
            segments = await _heartbeat_while(
                context,
                provider.transcribe(audio, duration_ms=round(artifact["duration_seconds"] * 1000)),
            )
        except (ASRUnavailable, RetryableError) as error:
            await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
            await complete(job, context, request_id, None, error=error)
            await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
            raise
        await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
        result = {
            "speech": {
                "status": "completed",
                "reason": None,
                "provider": "groq",
                "model": settings.groq_model,
                "processing_version": ASR_VERSION,
                "source_sha256": source,
                "audio_sha256": artifact["sha256"],
                "settings_sha256": speech_settings_hash(settings),
                "segments": [
                    {
                        "text": segment["text"],
                        "interval": {
                            "start_ms": segment["start_ms"],
                            "end_ms": segment["end_ms"],
                            "timebase": "media",
                        },
                    }
                    for segment in segments
                ],
            }
        }
        await complete(job, context, request_id, result)
        await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
        return result

    return handle
