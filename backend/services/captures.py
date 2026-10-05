"""Capture manifest arithmetic and the first incremental queue stage."""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select

from services.api.capture_schemas import (
    CaptureInterval,
    CaptureManifest,
    CaptureSession,
    ModalityCoverage,
    SeqRange,
)
from services.jobs.handlers import JobContext
from services.jobs.queue import ClaimedJob, StageKey
from services.jobs.retries import NonRetriableInput
from services.models import capture_chunks, capture_sessions
from services.storage import UploadStore

CAPTURE_STAGE = "media_validation"


def stage_key(session_id: UUID, seq: int) -> StageKey:
    digest = hashlib.sha256(f"capture:{session_id}:{seq}".encode()).hexdigest()
    return StageKey(1, CAPTURE_STAGE, digest)


def interval(start: int, end: int) -> CaptureInterval:
    return CaptureInterval(start_ms=start, end_ms=end, timebase="capture")


def gaps(sequences: list[int]) -> list[SeqRange]:
    result = []
    expected = 0
    for seq in sorted(sequences):
        if seq > expected:
            result.append(SeqRange(from_seq=expected, to_seq=seq - 1))
        expected = seq + 1
    return result


def session_response(
    session: Row[Any], chunks: Sequence[Row[Any]], now: datetime
) -> CaptureSession:
    received = [chunk for chunk in chunks if chunk.received_at is not None]
    sequences = [chunk.seq for chunk in received]
    expired = session.state == "open" and session.expires_at <= now
    return CaptureSession(
        id=session.id,
        investigation_id=session.id,
        state="abandoned" if expired else session.state,
        started_at=session.started_at,
        closed_at=session.expires_at if expired else session.closed_at,
        chunk_duration_ms=session.chunk_duration_ms,
        chunks_received=len(received),
        highest_seq=max(sequences) if sequences else None,
        received_ms=sum(c.end_ms - c.seq * session.chunk_duration_ms for c in received),
        gaps=gaps(sequences),
    )


def manifest(session: Row[Any], chunks: Sequence[Row[Any]]) -> CaptureManifest:
    received = sorted(
        (chunk for chunk in chunks if chunk.received_at is not None), key=lambda c: c.seq
    )
    duration = session.duration_ms
    if duration is None:
        duration = max((c.end_ms for c in received), default=0)
    missing = []
    speech = []
    text = []
    end = 0
    for chunk in received:
        start = chunk.seq * session.chunk_duration_ms
        if start > end:
            missing.append(interval(end, start))
        span = interval(start, chunk.end_ms)
        if chunk.modality in {"speech", "both"}:
            speech.append(span)
        if chunk.modality in {"text", "both"}:
            text.append(span)
        end = chunk.end_ms
    if end < duration:
        missing.append(interval(end, duration))
    return CaptureManifest(
        duration_ms=duration,
        missing_intervals=missing,
        declared_coverage=ModalityCoverage(speech=speech, text=text),
    )


class CaptureProcessor:
    def __init__(self, store: UploadStore):
        self.store = store

    async def run(self, job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        """Verify durable bytes before the later ASR/OCR pipeline consumes them."""
        try:
            owner = UUID(str(job.payload["owner_id"]))
        except (KeyError, ValueError):
            raise NonRetriableInput("Capture job has no valid owner") from None
        await context.heartbeat()
        async with context.queue.database.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(capture_chunks)
                    .select_from(capture_chunks.join(capture_sessions))
                    .where(
                        capture_chunks.c.job_id == job.id,
                        capture_chunks.c.received_at.is_not(None),
                        capture_sessions.c.owner_id == owner,
                    )
                    .with_for_update(of=capture_sessions)
                )
            ).first()
            if row is None:
                raise NonRetriableInput("Capture chunk is missing or unowned")
            if await self.store.digest(row.storage_key) != (row.size_bytes, row.sha256):
                raise NonRetriableInput("Capture bytes are missing or corrupt")
        await context.heartbeat()
        return {"seq": row.seq, "media_processing": "not_started"}
