"""Per-chunk speech and device text for live capture, on the session's capture timebase.

A chunk is the ``application/zip`` package Android produces (AN-04/AN-06): ``chunk.json``
describing the chunk and its on-device text, plus optional 16 kHz mono PCM audio. Text is
complete when the chunk arrives: frames still being read when a chunk seals are dropped on
the device, so captures have no late-text channel and no text grace period.
"""

import asyncio
import hashlib
import io
import json
import uuid
import wave
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.schemas import AnalysisRead
from services.api.text_schemas import (
    Coordinate,
    Count,
    TextFrame,
    TextModel,
    TextRecognizer,
    TextSampling,
)
from services.asr.groq import ASRAdapter, ASRUnavailable
from services.asr.reservations import complete, reserve, resume
from services.jobs.faults import Checkpoint
from services.jobs.handlers import JobContext, JobHandler
from services.jobs.models import job_results, jobs
from services.jobs.queue import ClaimedJob
from services.jobs.retries import NonRetriableInput, RetryableError
from services.models import capture_chunks, capture_sessions
from services.settings import Settings
from services.storage import UploadStore

PACKAGE_TYPE = "application/zip"
PACKAGE_VERSION = 1
CAPTURE_ASR_VERSION = 1
MANIFEST_ENTRY = "chunk.json"
AUDIO_ENTRY = "audio-16000-mono-s16le.pcm"
MANIFEST_MAX_BYTES = 2_097_152
BYTES_PER_MS = 32
# Android attributes each whole audio buffer to the chunk its first sample falls in.
AUDIO_OVERRUN_MS = 1000
_OBSERVATION_IDS = uuid.UUID("5d4f0f43-9a52-4b8e-9b1e-0c6f1f6a7c01")


class InvalidPackage(NonRetriableInput):
    pass


class PackageAudio(TextModel):
    file: Literal["audio-16000-mono-s16le.pcm"]
    encoding: Literal["pcm_s16le"]
    sample_rate: Literal[16000]
    channels: Literal[1]
    bytes: int = Field(gt=0)


class PackageObservation(TextModel):
    text: str = Field(min_length=1, max_length=4096)
    box: list[Coordinate] = Field(min_length=4, max_length=4)
    frame_pts: Count


class PackageFrame(TextModel):
    frame_pts: Count
    status: Literal["recognized", "no_text_regions", "failed"]
    regions: int = Field(ge=0, le=100)
    recognition_ms: Count
    failed_regions: int = Field(ge=0, le=100)


class ChunkManifest(TextModel):
    seq: int = Field(ge=0)
    start_ms: Count
    end_ms: Count
    timebase: Literal["capture"]
    modality: Literal["speech", "text", "both"]
    audio: PackageAudio | None
    frames_uploaded: Literal[False]
    frames: Annotated[list[PackageFrame], Field(max_length=200)]
    text_observations: Annotated[list[PackageObservation], Field(max_length=5000)]
    sampling: TextSampling
    recognizer: TextRecognizer

    @model_validator(mode="after")
    def ordered(self) -> "ChunkManifest":
        if self.start_ms >= self.end_ms:
            raise ValueError("A chunk interval must be nonempty")
        return self


@dataclass(frozen=True)
class Package:
    manifest: ChunkManifest
    frames: list[dict[str, Any]]
    audio: bytes | None


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate JSON field")
    return value


def _entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, maximum: int) -> bytes:
    if info.is_dir() or info.file_size > maximum:
        raise InvalidPackage("Capture package entry exceeds its limit")
    with archive.open(info) as stream:
        data = stream.read(maximum + 1)
    if len(data) != info.file_size:
        raise InvalidPackage("Capture package entry is truncated")
    return data


def read_package(path: Path, chunk: Row[Any], chunk_duration_ms: int) -> Package:
    """Validate a stored chunk package against its accepted transport identity."""
    try:
        with path.open("rb") as stream:
            data = stream.read(chunk.size_bytes + 1)
    except OSError:
        raise InvalidPackage("Capture bytes are missing or unreadable") from None
    if len(data) != chunk.size_bytes or hashlib.sha256(data).hexdigest() != chunk.sha256:
        raise InvalidPackage("Capture bytes are missing or corrupt")
    start = chunk.seq * chunk_duration_ms
    length = chunk.end_ms - start
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [info.filename for info in entries]
            if (
                len(set(names)) != len(names)
                or MANIFEST_ENTRY not in names
                or not set(names) <= {MANIFEST_ENTRY, AUDIO_ENTRY}
                or any(
                    info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    for info in entries
                )
            ):
                raise InvalidPackage("Capture package has unexpected entries")
            by_name = {info.filename: info for info in entries}
            raw = _entry(archive, by_name[MANIFEST_ENTRY], MANIFEST_MAX_BYTES)
            audio = (
                _entry(archive, by_name[AUDIO_ENTRY], (length + AUDIO_OVERRUN_MS) * BYTES_PER_MS)
                if AUDIO_ENTRY in by_name
                else None
            )
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError, OSError):
        raise InvalidPackage("Capture package is not a readable archive") from None
    try:
        json.loads(raw, object_pairs_hook=_unique)
        manifest = ChunkManifest.model_validate_json(raw)
    except (ValueError, RecursionError, ValidationError):
        raise InvalidPackage("Capture package description is invalid") from None
    speech = chunk.modality in {"speech", "both"}
    text = chunk.modality in {"text", "both"}
    if (
        manifest.seq != chunk.seq
        or manifest.start_ms != start
        or manifest.end_ms != chunk.end_ms
        or manifest.modality != chunk.modality
        or (manifest.audio is not None) != speech
        or (audio is not None) != speech
        or bool(manifest.frames) != text
    ):
        raise InvalidPackage("Capture package does not match its accepted chunk")
    if audio is not None and (
        manifest.audio is None or len(audio) != manifest.audio.bytes or len(audio) % 2
    ):
        raise InvalidPackage("Capture package audio does not match its description")
    times = [frame.frame_pts for frame in manifest.frames]
    if len(set(times)) != len(times) or any(not start <= pts < chunk.end_ms for pts in times):
        raise InvalidPackage("Frame timestamps must be unique and inside the chunk")
    observations: dict[int, list[dict[str, Any]]] = {pts: [] for pts in times}
    for index, item in enumerate(manifest.text_observations):
        if item.frame_pts not in observations:
            raise InvalidPackage("Text observation does not belong to a frame in the chunk")
        observations[item.frame_pts].append(
            {
                "id": uuid.uuid5(_OBSERVATION_IDS, f"{chunk.sha256}:{index}"),
                "text": item.text,
                "box": item.box,
                "frame_pts": item.frame_pts,
            }
        )
    try:
        frames = [
            TextFrame.model_validate(
                {**frame.model_dump(), "text_observations": observations[frame.frame_pts]}
            ).model_dump(mode="json")
            for frame in sorted(manifest.frames, key=lambda frame: frame.frame_pts)
        ]
    except ValidationError:
        raise InvalidPackage("Frame outcome is inconsistent with its text") from None
    return Package(manifest, frames, audio)


def package_summary(package: Package) -> dict[str, Any]:
    audio = package.audio
    return {
        "version": PACKAGE_VERSION,
        "modality": package.manifest.modality,
        "audio": (
            {"bytes": len(audio), "duration_ms": len(audio) // BYTES_PER_MS}
            if audio is not None
            else None
        ),
        "frames": len(package.frames),
    }


def _wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    return output.getvalue()


@dataclass(frozen=True)
class PreparedChunk:
    capture_id: uuid.UUID
    chunk: Row[Any]
    chunk_duration_ms: int
    package: dict[str, Any]


async def _prepared(job: ClaimedJob, context: JobContext, stage: str) -> PreparedChunk:
    from services.captures import CAPTURE_STAGE, capture_reference, stage_key

    capture_id, seq = capture_reference(job)
    if job.key != stage_key(capture_id, seq, stage):
        raise NonRetriableInput("Capture stage key does not match its payload")
    validation = jobs.alias("validation")
    key = stage_key(capture_id, seq, CAPTURE_STAGE)
    async with context.queue.database.engine.connect() as connection:
        row = (
            await connection.execute(
                select(
                    capture_chunks,
                    capture_sessions.c.chunk_duration_ms,
                    job_results.c.result.label("prepared"),
                )
                .select_from(
                    capture_chunks.join(capture_sessions)
                    .join(validation, validation.c.id == capture_chunks.c.job_id)
                    .join(job_results, job_results.c.job_id == validation.c.id)
                )
                .where(
                    capture_chunks.c.session_id == capture_id,
                    capture_chunks.c.seq == seq,
                    capture_chunks.c.received_at.is_not(None),
                    capture_sessions.c.owner_id
                    == select(jobs.c.owner_id).where(jobs.c.id == job.id).scalar_subquery(),
                    validation.c.owner_id == capture_sessions.c.owner_id,
                    validation.c.version == key.version,
                    validation.c.stage == key.stage,
                    validation.c.input_hash == key.input_hash,
                    validation.c.state == "published",
                )
            )
        ).first()
    if row is None:
        raise NonRetriableInput("Validated capture chunk is missing or unowned")
    package = row.prepared.get("package")
    if package is None:
        raise NonRetriableInput("Capture chunk is not a processable package")
    return PreparedChunk(capture_id, row, row.chunk_duration_ms, package)


async def _load(store: UploadStore, prepared: PreparedChunk) -> Package:
    path = await store.local_path(prepared.chunk.storage_key)
    return await asyncio.to_thread(read_package, path, prepared.chunk, prepared.chunk_duration_ms)


def _unavailable(reason: str) -> dict[str, Any]:
    return {"speech": {"status": "unavailable", "reason": reason}}


def build_capture_speech(
    settings: Settings,
    store: UploadStore,
    *,
    adapter: ASRAdapter | None = None,
    quota_clock: Callable[[], datetime] | None = None,
) -> JobHandler:
    from services.pipeline.media_validation import _heartbeat_while as heartbeat_while
    from services.pipeline.speech import speech_provider, speech_settings_hash

    provider = speech_provider(settings, adapter)

    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        prepared = await _prepared(job, context, "asr")
        if prepared.package["audio"] is None:
            return _unavailable("no_audio_track")
        if not settings.asr_enabled:
            return _unavailable("disabled")
        if not settings.asr_configured:
            return _unavailable("provider_unavailable")
        previous = await resume(job, context)
        if previous is not None:
            return previous
        package = await _load(store, prepared)
        if package.audio is None:
            raise InvalidPackage("Capture package audio disappeared")
        audio = _wav(package.audio)
        if len(audio) > settings.asr_audio_max_bytes:
            raise ASRUnavailable
        duration_ms = len(package.audio) // BYTES_PER_MS
        start = prepared.chunk.seq * prepared.chunk_duration_ms
        end = prepared.chunk.end_ms
        request_id = await reserve(job, context, settings, duration_ms / 1000, quota_clock)
        await context.checkpoint(Checkpoint.BEFORE_PROVIDER_CALL, job)
        await context.heartbeat()
        try:
            segments = await heartbeat_while(
                context,
                provider.transcribe(audio, duration_ms=max(duration_ms, 1), offset_ms=start),
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
                "processing_version": CAPTURE_ASR_VERSION,
                "source_sha256": prepared.chunk.sha256,
                "audio_sha256": hashlib.sha256(package.audio).hexdigest(),
                "settings_sha256": speech_settings_hash(settings),
                "segments": [
                    {
                        "text": segment["text"],
                        # Audio overrun past the chunk is attributed to its final millisecond.
                        "interval": {
                            "start_ms": min(segment["start_ms"], end - 1),
                            "end_ms": min(segment["end_ms"], end),
                            "timebase": "capture",
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


def build_capture_text(store: UploadStore) -> JobHandler:
    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        prepared = await _prepared(job, context, "device_text")
        if not prepared.package["frames"]:
            return {"text": {"status": "unavailable", "reason": "no_frames"}}
        package = await _load(store, prepared)
        await context.heartbeat()
        return {
            "text": {
                "status": "completed",
                "reason": None,
                "source_sha256": prepared.chunk.sha256,
                "sampling": package.manifest.sampling.model_dump(mode="json"),
                "recognizer": package.manifest.recognizer.model_dump(mode="json"),
                "frames": package.frames,
            }
        }

    return handle


_SPEECH_GAPS = {
    "no_audio_track": "NO_AUDIO_TRACK",
    "disabled": "ASR_DISABLED",
    "ASRQuotaExhausted": "ASR_QUOTA_EXHAUSTED",
    "ASRUnknownOutcome": "ASR_OUTCOME_UNKNOWN",
}
_SPEECH_REASONS = {
    "NO_AUDIO_TRACK": "no_audio_track",
    "ASR_DISABLED": "disabled",
    "ASR_QUOTA_EXHAUSTED": "quota_exhausted",
    "ASR_OUTCOME_UNKNOWN": "unknown_outcome",
}
_ACTIVE = {"queued", "leased", "running"}


async def capture_analysis(
    connection: AsyncConnection, capture_id: uuid.UUID
) -> tuple[dict[str, Any], AnalysisRead] | None:
    """Aggregate per-chunk speech and text; missing chunks stay gaps, never coverage."""
    from services.captures import chunk_jobs, interval, manifest, stage_key

    session = (
        await connection.execute(
            select(capture_sessions).where(capture_sessions.c.id == capture_id)
        )
    ).first()
    if session is None:
        return None
    chunks: Sequence[Row[Any]] = (
        await connection.execute(
            select(capture_chunks)
            .where(capture_chunks.c.session_id == capture_id)
            .order_by(capture_chunks.c.seq)
        )
    ).all()
    received = [chunk for chunk in chunks if chunk.received_at is not None]
    if not received:
        return None
    rows = (
        await connection.execute(
            select(jobs, job_results.c.result)
            .outerjoin(job_results, job_results.c.job_id == jobs.c.id)
            .where(
                chunk_jobs(capture_id, [chunk.seq for chunk in received]),
                jobs.c.owner_id == session.owner_id,
            )
        )
    ).all()
    gaps: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    text_chunks: list[dict[str, Any]] = []
    pending: set[str] = set()
    running = False
    analyzed: set[str] = set()
    speech_meta: dict[str, Any] | None = None
    first_speech_gap: str | None = None
    for chunk in received:
        span = interval(chunk.seq * session.chunk_duration_ms, chunk.end_ms).model_dump(mode="json")
        digest = stage_key(capture_id, chunk.seq).input_hash
        stages = {row.stage: row for row in rows if row.input_hash == digest}
        validation = stages.get("media_validation")
        if validation is None or validation.id != chunk.job_id or validation.state in _ACTIVE:
            pending |= {"speech", "text"}
            continue
        if validation.state != "published":
            for modality in ("speech", "text"):
                gaps.append(
                    {"modality": modality, "reason": "CAPTURE_CHUNK_INVALID", "interval": span}
                )
            first_speech_gap = first_speech_gap or "CAPTURE_CHUNK_INVALID"
            continue
        speech = stages.get("asr")
        if speech is None or speech.state in _ACTIVE:
            pending.add("speech")
            running = running or (speech is not None and speech.state == "running")
        elif speech.state == "published" and speech.result["speech"]["status"] == "completed":
            analyzed.add("speech")
            result = speech.result["speech"]
            speech_meta = speech_meta or result
            segments.extend(result["segments"])
        else:
            key = (
                speech.result["speech"]["reason"]
                if speech.state == "published"
                else speech.failure or ""
            )
            reason = _SPEECH_GAPS.get(key, "ASR_UNAVAILABLE")
            first_speech_gap = first_speech_gap or reason
            gaps.append({"modality": "speech", "reason": reason, "interval": span})
        text = stages.get("device_text")
        if text is None or text.state in _ACTIVE:
            pending.add("text")
        elif text.state == "published" and text.result["text"]["status"] == "completed":
            value = text.result["text"]
            frames = value["frames"]
            if any(frame["status"] in {"recognized", "no_text_regions"} for frame in frames):
                analyzed.add("text")
            gaps.extend(
                {"modality": "text", "reason": "DEVICE_TEXT_FRAME_FAILED", "interval": None}
                for frame in frames
                if frame["status"] == "failed" or frame["failed_regions"]
            )
            text_chunks.append(
                {
                    "seq": chunk.seq,
                    "interval": span,
                    "source_sha256": value["source_sha256"],
                    "sampling": value["sampling"],
                    "recognizer": value["recognizer"],
                    "frames": frames,
                }
            )
        else:
            gaps.append({"modality": "text", "reason": "DEVICE_TEXT_MISSING", "interval": span})
    for missing in manifest(session, chunks).missing_intervals:
        for modality in ("speech", "text"):
            gaps.append(
                {
                    "modality": modality,
                    "reason": "CAPTURE_CHUNK_MISSING",
                    "interval": missing.model_dump(mode="json"),
                }
            )
    segments.sort(key=lambda item: (item["interval"]["start_ms"], item["interval"]["end_ms"]))
    if "speech" in pending:
        speech_read: dict[str, Any] = {"status": "running" if running else "pending"}
    elif "speech" in analyzed and speech_meta is not None:
        speech_read = {
            "status": "completed",
            "provider": speech_meta["provider"],
            "model": speech_meta["model"],
            "processing_version": speech_meta["processing_version"],
            "settings_sha256": speech_meta["settings_sha256"],
        }
    else:
        speech_read = {
            "status": "unavailable",
            "reason": _SPEECH_REASONS.get(first_speech_gap or "", "provider_unavailable"),
        }
    speech_read["segments"] = segments
    observations = any(
        frame["text_observations"] for item in text_chunks for frame in item["frames"]
    )
    usable = bool(segments) or observations
    unavailable = [
        modality
        for modality in ("speech", "text")
        if modality not in analyzed and modality not in pending
    ]
    status = (
        ("partial" if gaps or pending else "complete")
        if usable
        else ("pending" if pending else "no_usable")
    )
    return speech_read, AnalysisRead.model_validate(
        {
            "status": status,
            "text_deadline": None,
            "text_expired": False,
            "analyzed_modalities": [m for m in ("speech", "text") if m in analyzed],
            "pending_modalities": [m for m in ("speech", "text") if m in pending],
            "unavailable_modalities": unavailable,
            "gaps": gaps,
            "text": {"timebase": "capture", "chunks": text_chunks},
            "captions": [],
        }
    )
