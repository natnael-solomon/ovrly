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

from services.asr.fallback import SpeechAudio, transcribe
from services.asr.groq import (
    ASRAdapter,
    ASRChunkTooLarge,
    ASRMissingAudio,
    ASRUnavailable,
    GroqAdapter,
    transcription_parameters,
)
from services.jobs.handlers import JobContext, JobHandler
from services.jobs.models import job_results, jobs
from services.jobs.queue import ClaimedJob, StageKey
from services.models import investigations, uploads
from services.pipeline.intake import _identifier
from services.pipeline.media_validation import media_stage_key
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
                "fallback_model": settings.groq_fallback_model,
                "audio_max_bytes": settings.asr_audio_max_bytes,
                "response_max_bytes": settings.asr_response_max_bytes,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def _read_audio(root: Path, artifact: dict[str, Any], maximum: int) -> bytes:
    """Missing, changed or escaping audio is a gap; over-cap audio is refused before any call."""
    path = (root / str(artifact["key"])).resolve()
    if artifact["size_bytes"] > maximum:
        raise ASRChunkTooLarge
    if not path.is_relative_to(root.resolve()):
        raise ASRMissingAudio
    try:
        with path.open("rb") as stream:
            audio = stream.read(maximum + 1)
    except OSError:
        raise ASRMissingAudio from None
    if len(audio) > maximum:
        raise ASRChunkTooLarge
    if (
        len(audio) != artifact["size_bytes"]
        or hashlib.sha256(audio).hexdigest() != artifact["sha256"]
    ):
        raise ASRMissingAudio
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
            or not settings.asr_configured
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
        if media is None or source != media["source_sha256"]:
            raise ASRUnavailable
        if media["audio_artifact"] is None:
            raise ASRMissingAudio
        artifact = media["audio_artifact"]

        async def load() -> SpeechAudio:
            audio = await asyncio.to_thread(
                _read_audio, settings.artifacts_dir, artifact, settings.asr_audio_max_bytes
            )
            return SpeechAudio(
                audio,
                round(artifact["duration_seconds"] * 1000),
                artifact["duration_seconds"],
                artifact["sha256"],
            )

        def build(
            model: str, segments: list[dict[str, Any]], speech: SpeechAudio
        ) -> dict[str, Any]:
            return {
                "speech": {
                    "status": "completed",
                    "reason": None if segments else "no_speech",
                    "provider": "groq",
                    "model": model,
                    "processing_version": ASR_VERSION,
                    "source_sha256": source,
                    "audio_sha256": speech.sha256,
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

        return await transcribe(
            job, context, settings, provider, load=load, offset_ms=0, build=build, clock=quota_clock
        )

    return handle
