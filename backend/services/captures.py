"""Capture manifest arithmetic and the first incremental queue stage."""

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, and_, select

from services.api.capture_schemas import (
    CaptureClaimState,
    CaptureInterval,
    CaptureManifest,
    CaptureSession,
    ModalityCoverage,
    SeqRange,
)
from services.api.errors import safe_error
from services.api.schemas import CoverageStatus, ProcessingStatus, ReportVersion, SafeError
from services.jobs.handlers import JobContext
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, StageKey
from services.jobs.retries import NonRetriableInput
from services.models import capture_chunks, capture_sessions
from services.pipeline.capture_media import PACKAGE_TYPE, package_summary, read_package
from services.storage import UploadStore

CAPTURE_STAGE = "media_validation"
CAPTURE_STAGES = (CAPTURE_STAGE, "asr", "device_text", "claim_extraction")


def stage_key(session_id: UUID, seq: int, stage: str = CAPTURE_STAGE) -> StageKey:
    digest = hashlib.sha256(f"capture:{session_id}:{seq}".encode()).hexdigest()
    return StageKey(1, stage, digest)


def capture_reference(job: ClaimedJob) -> tuple[UUID, int]:
    """Read new payloads and the session_id spelling used by already queued validations."""
    try:
        identifier = UUID(str(job.payload.get("capture_id", job.payload.get("session_id"))))
        seq = job.payload["seq"]
        if type(seq) is not int or seq < 0:
            raise ValueError
    except (KeyError, ValueError):
        raise NonRetriableInput("Capture job has no valid capture or sequence") from None
    return identifier, seq


def chunk_jobs(capture_id: UUID, sequences: Sequence[int]) -> ColumnElement[bool]:
    return and_(
        jobs.c.version == 1,
        jobs.c.stage.in_(CAPTURE_STAGES),
        jobs.c.input_hash.in_([stage_key(capture_id, seq).input_hash for seq in sequences]),
    )


def claim_progress(
    report: ReportVersion | None, continue_research: bool | None
) -> tuple[list[CaptureClaimState], CoverageStatus]:
    if report is None:
        return [], "not_started"
    assessments = {item.claim_id: item for item in report.assessments}
    claims = []
    for claim in report.claims:
        assessment = assessments.get(claim.id)
        state: ProcessingStatus
        error = None
        if assessment is not None:
            state = "partial" if assessment.provisional else "complete"
        elif continue_research is False or claim.superseded_by_occurrence_id is not None:
            # A superseded appearance is never assessed; its correction carries the result.
            state = "cancelled"
        elif not report.provisional:
            # A final version keeps a claim it could not assess visible, with the reason in
            # its change summary (#127), so polling ends instead of waiting for it.
            state = "failed"
            error = SafeError.model_validate(safe_error("CLAIM_UNASSESSED"))
        else:
            state = "checking"
        claims.append(CaptureClaimState(claim_id=claim.id, processing_status=state, error=error))
    return claims, "partial" if report.provisional else "complete"


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
        capture_id, seq = capture_reference(job)
        await context.heartbeat()
        async with context.queue.database.engine.begin() as connection:
            row = (
                await connection.execute(
                    select(capture_chunks, capture_sessions.c.chunk_duration_ms)
                    .select_from(
                        capture_chunks.join(capture_sessions).join(
                            jobs, jobs.c.id == capture_chunks.c.job_id
                        )
                    )
                    .where(
                        capture_chunks.c.job_id == job.id,
                        capture_chunks.c.session_id == capture_id,
                        capture_chunks.c.seq == seq,
                        capture_chunks.c.received_at.is_not(None),
                        capture_sessions.c.owner_id == jobs.c.owner_id,
                    )
                    .with_for_update(of=capture_sessions)
                )
            ).first()
            if row is None:
                raise NonRetriableInput("Capture chunk is missing or unowned")
            if await self.store.digest(row.storage_key) != (row.size_bytes, row.sha256):
                raise NonRetriableInput("Capture bytes are missing or corrupt")
        await context.heartbeat()
        package = None
        if row.content_type == PACKAGE_TYPE:
            path = await self.store.local_path(row.storage_key)
            package = package_summary(
                await asyncio.to_thread(read_package, path, row, row.chunk_duration_ms)
            )
            await context.heartbeat()
        return {"seq": row.seq, "media_processing": "not_started", "package": package}
