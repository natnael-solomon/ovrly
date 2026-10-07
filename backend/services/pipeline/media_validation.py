"""Prepare owned uploaded media; only fenced job publication makes its result visible."""

import asyncio
import hashlib
import json
import math
import os
import stat
import tempfile
import uuid
import wave
from collections.abc import Coroutine
from fractions import Fraction
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import select

from services.jobs.handlers import JobContext, JobHandler
from services.jobs.queue import ClaimedJob, StageKey
from services.jobs.retries import NonRetriableInput, Transient
from services.media.runner import CommandLimits, CommandResult, MediaOutputTooLarge, run_command
from services.models import investigations, uploads
from services.settings import Settings
from services.storage import LocalFilesystemStore

MEDIA_STAGE = "media_validation"
MEDIA_VERSION = 1
_T = TypeVar("_T")
# No playlists or image-sequence demuxers; MOV external data references remain disabled.
_INPUT_OPTIONS = [
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "mov,matroska,webm,wav,mp3,flac,ogg,aac,avi",
    "-max_alloc",
    "67108864",
]


class InvalidMedia(NonRetriableInput):
    pass


class MediaTooLarge(NonRetriableInput):
    pass


class MediaTooLong(NonRetriableInput):
    pass


class MediaTimedOut(Transient):
    pass


def media_stage_key(investigation_id: uuid.UUID) -> StageKey:
    digest = hashlib.sha256(f"investigation:{investigation_id}".encode()).hexdigest()
    return StageKey(MEDIA_VERSION, MEDIA_STAGE, digest)


async def _heartbeat_while(context: JobContext, work: Coroutine[Any, Any, _T]) -> _T:
    async def maintain() -> None:
        while True:
            await context.heartbeat()
            await asyncio.sleep(context.lease_seconds / 3)

    task: asyncio.Task[_T] = asyncio.create_task(work)
    heartbeat = asyncio.create_task(maintain())
    try:
        done, _ = await asyncio.wait((task, heartbeat), return_when=asyncio.FIRST_COMPLETED)
        if heartbeat in done:
            await heartbeat
        result = await task
        await context.heartbeat()
        return result
    finally:
        task.cancel()
        heartbeat.cancel()
        await asyncio.gather(task, heartbeat, return_exceptions=True)


async def _finish_file_work(work: Coroutine[Any, Any, _T]) -> _T:
    task = asyncio.create_task(work)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # to_thread cancellation does not stop I/O; join before temporary files are removed.
        await asyncio.gather(task, return_exceptions=True)
        raise


async def _media_command(argv: list[str], limits: CommandLimits) -> CommandResult:
    try:
        result = await run_command(argv, limits)
    except TimeoutError:
        raise MediaTimedOut from None
    except MediaOutputTooLarge:
        raise InvalidMedia("Media command exceeded its output budget") from None
    return result


def _validate_riff(path: Path, size: int) -> None:
    with path.open("rb") as reader:
        header = reader.read(12)
        if header[:4] != b"RIFF":
            return
        end = int.from_bytes(header[4:8], "little") + 8
        if end > size or end < 12:
            raise InvalidMedia("Incomplete RIFF media")
        position = 12
        while position < end:
            reader.seek(position)
            chunk = reader.read(8)
            if len(chunk) != 8:
                raise InvalidMedia("Incomplete RIFF chunk")
            length = int.from_bytes(chunk[4:8], "little")
            position += 8 + length + length % 2
            if position > end:
                raise InvalidMedia("Incomplete RIFF chunk")


def _snapshot(source: Path, target: Path, size: int, digest: str, maximum: int) -> None:
    if size > maximum:
        raise MediaTooLarge
    count = 0
    hashed = hashlib.sha256()
    try:
        if not stat.S_ISREG(source.stat().st_mode):
            raise InvalidMedia("Stored input is not a regular file")
        with source.open("rb") as reader, target.open("xb") as writer:
            while block := reader.read(65536):
                count += len(block)
                if count > min(size, maximum):
                    raise MediaTooLarge
                hashed.update(block)
                writer.write(block)
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        raise InvalidMedia("Stored input is missing or unreadable") from None
    if count != size or hashed.hexdigest() != digest:
        raise InvalidMedia("Stored input no longer matches its completed upload")
    _validate_riff(target, count)


def _publish_audio(
    source: Path, root: Path, job: ClaimedJob, maximum: int, duration_limit: int
) -> dict[str, Any]:
    size = source.stat().st_size
    if size > maximum:
        raise MediaTooLarge
    try:
        with wave.open(str(source), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getframerate() != 16000
                or audio.getsampwidth() != 2
            ):
                raise InvalidMedia("Unexpected extracted audio format")
            duration = audio.getnframes() / 16000
            if duration <= 0:
                raise InvalidMedia("Empty audio")
            if duration > duration_limit:
                raise MediaTooLong
            expected_bytes = audio.getnframes() * 2
            if expected_bytes > size or len(audio.readframes(audio.getnframes())) != expected_bytes:
                raise InvalidMedia("Incomplete extracted audio")
    except (wave.Error, EOFError):
        raise InvalidMedia("Invalid extracted audio") from None
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    key = f"{job.id.hex}/{digest}.wav"
    destination = root / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError:
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise OSError("Stored artifact digest mismatch") from None
    return {
        "key": key,
        "sha256": digest,
        "size_bytes": size,
        "sample_rate_hz": 16000,
        "channels": 1,
        "duration_seconds": duration,
    }


def _decoded_video_end(report: bytes) -> float:
    """End time of the last decoded frame, from ffmpeg's version-stable framecrc rows.

    `-progress` out_time is the last frame's start on ffmpeg 6 but its end on 4.4.
    """
    timebase: Fraction | None = None
    end = Fraction(0)
    try:
        for line in report.splitlines():
            if line.startswith(b"#tb 0:"):
                numerator, denominator = line.split(b":", 1)[1].split(b"/")
                timebase = Fraction(int(numerator), int(denominator))
            elif line and not line.startswith(b"#"):
                if timebase is None:
                    raise InvalidMedia("Missing decoded video timebase")
                _, _, pts, frame_duration, *_ = line.split(b",")
                end = max(end, (int(pts) + max(int(frame_duration), 0)) * timebase)
    except (ValueError, ZeroDivisionError):
        raise InvalidMedia("Invalid decoded video frames") from None
    return float(end)


async def _prepare(
    job: ClaimedJob,
    context: JobContext,
    settings: Settings,
    source: Path,
    size: int,
    digest: str,
) -> dict[str, Any]:
    root = settings.artifacts_dir.resolve()
    await _finish_file_work(asyncio.to_thread(root.mkdir, parents=True, exist_ok=True))
    with tempfile.TemporaryDirectory(prefix=f"{job.id.hex}-", dir=root) as directory:
        snapshot = Path(directory) / "input"
        await _finish_file_work(
            asyncio.to_thread(_snapshot, source, snapshot, size, digest, settings.upload_max_bytes)
        )
        limits = CommandLimits(
            settings.media_probe_timeout_seconds,
            settings.media_cpu_seconds,
            settings.media_output_max_bytes,
            settings.asr_audio_max_bytes,
        )
        probe = await _media_command(
            [
                settings.ffprobe_path,
                "-v",
                "error",
                *_INPUT_OPTIONS,
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(snapshot),
            ],
            limits,
        )
        if probe.returncode != 0:
            raise InvalidMedia("Media could not be probed")
        try:
            data = json.loads(probe.stdout)
            duration = float(data["format"]["duration"])
            streams = {stream["codec_type"] for stream in data["streams"]}
        except (ValueError, KeyError, TypeError, OverflowError):
            raise InvalidMedia("Invalid media metadata") from None
        if not math.isfinite(duration) or duration <= 0 or not streams & {"audio", "video"}:
            raise InvalidMedia("Missing usable duration or streams")
        if duration > settings.max_shared_duration_seconds:
            raise MediaTooLong
        if "video" in streams:
            frames = Path(directory) / "frames.crc"
            decoded = await _media_command(
                [
                    settings.ffmpeg_path,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-threads",
                    "1",
                    *_INPUT_OPTIONS,
                    "-i",
                    str(snapshot),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-threads",
                    "1",
                    "-t",
                    str(settings.max_shared_duration_seconds + 0.1),
                    "-f",
                    "framecrc",
                    str(frames),
                ],
                CommandLimits(
                    settings.media_extract_timeout_seconds,
                    settings.media_cpu_seconds,
                    settings.media_output_max_bytes,
                    settings.asr_audio_max_bytes,
                ),
            )
            if decoded.returncode != 0:
                raise InvalidMedia("Video decoding failed")
            try:
                report = await _finish_file_work(asyncio.to_thread(frames.read_bytes))
            except FileNotFoundError:
                raise InvalidMedia("Missing decoded video frames") from None
            video_duration = _decoded_video_end(report)
            if video_duration <= 0:
                raise InvalidMedia("Empty video")
            if video_duration > settings.max_shared_duration_seconds:
                raise MediaTooLong
            duration = max(duration, video_duration)
        audio = None
        if "audio" in streams:
            output = Path(directory) / "audio.wav"
            extract = await _media_command(
                [
                    settings.ffmpeg_path,
                    "-nostdin",
                    "-v",
                    "error",
                    "-xerror",
                    "-err_detect",
                    "explode",
                    "-threads",
                    "1",
                    *_INPUT_OPTIONS,
                    "-i",
                    str(snapshot),
                    "-map",
                    "0:a:0",
                    "-vn",
                    # Keep leading silence and timestamp gaps on the original media timeline.
                    "-af",
                    "aresample=async=1:first_pts=0",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    "-fflags",
                    "+bitexact",
                    "-map_metadata",
                    "-1",
                    "-t",
                    str(settings.max_shared_duration_seconds + 0.1),
                    "-f",
                    "wav",
                    str(output),
                ],
                CommandLimits(
                    settings.media_extract_timeout_seconds,
                    settings.media_cpu_seconds,
                    settings.media_output_max_bytes,
                    settings.asr_audio_max_bytes,
                ),
            )
            if (
                extract.returncode != 0
                and output.exists()
                and output.stat().st_size >= settings.asr_audio_max_bytes
            ):
                raise MediaTooLarge
            if extract.returncode != 0:
                raise InvalidMedia("Audio extraction failed")
            # Retention takes the same job lock before removing its artifact directory.
            async with context.queue.database.engine.begin() as connection:
                await context.queue.lock_active(connection, job.lease)
                audio = await _finish_file_work(
                    asyncio.to_thread(
                        _publish_audio,
                        output,
                        root,
                        job,
                        settings.asr_audio_max_bytes,
                        settings.max_shared_duration_seconds,
                    )
                )
            duration = max(duration, audio["duration_seconds"])
        return {
            "coverage": {
                "status": "not_started",
                "total_ms": math.ceil(duration * 1000),
                "media": {
                    "has_audio": "audio" in streams,
                    "has_video": "video" in streams,
                    "speech_status": "pending" if "audio" in streams else "unavailable",
                    "text_status": "pending",
                    "speech_unavailable_reason": None if "audio" in streams else "no_audio_track",
                },
            },
            "audio_artifact": audio,
            "source_sha256": digest,
            "preparation": {
                "version": MEDIA_VERSION,
                "format": "pcm_s16le_wav_16000_mono",
                "upload_max_bytes": settings.upload_max_bytes,
                "max_duration_seconds": settings.max_shared_duration_seconds,
                "audio_max_bytes": settings.asr_audio_max_bytes,
                "ffprobe_path": settings.ffprobe_path,
                "ffmpeg_path": settings.ffmpeg_path,
            },
        }


def build_media_validation(settings: Settings) -> JobHandler:
    store = LocalFilesystemStore(settings.storage_dir)

    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        from services.pipeline.intake import _identifier

        identifier = _identifier(job.payload, "investigation_id")
        owner = _identifier(job.payload, "owner_id")
        if job.key != media_stage_key(identifier):
            raise InvalidMedia("Unexpected stage key")
        async with context.queue.database.engine.connect() as connection:
            source = (
                await connection.execute(
                    select(
                        uploads.c.storage_key,
                        uploads.c.declared_size_bytes,
                        uploads.c.declared_sha256,
                    )
                    .join(investigations, investigations.c.upload_id == uploads.c.id)
                    .where(
                        investigations.c.id == identifier,
                        investigations.c.owner_id == owner,
                        investigations.c.source_kind == "upload",
                        uploads.c.owner_id == owner,
                        uploads.c.state == "completed",
                    )
                )
            ).first()
        if source is None:
            raise InvalidMedia("Owned completed upload is missing")
        await context.heartbeat()
        result = await _heartbeat_while(
            context,
            _prepare(
                job,
                context,
                settings,
                await store.local_path(source.storage_key),
                source.declared_size_bytes,
                source.declared_sha256,
            ),
        )
        if settings.asr_enabled and result["audio_artifact"] is not None:
            from services.pipeline.speech import speech_settings_hash, speech_stage_key

            context.enqueue_after_publish(
                speech_stage_key(identifier),
                {
                    **job.payload,
                    "media_job_id": str(job.id),
                    "settings_sha256": speech_settings_hash(settings),
                },
            )
        else:
            result["speech"] = {
                "status": "unavailable",
                "reason": "no_audio_track" if result["audio_artifact"] is None else "disabled",
            }
        result["text_grace_seconds"] = settings.text_grace_seconds
        return result

    return handle
