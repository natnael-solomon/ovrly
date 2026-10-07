"""Committed upstream speech and device text admitted to the extraction ledger, then settled.

Uploads admit ``upload_asr`` segments in the same fenced transaction that publishes them and
open the ledger even without speech, fixing policy at that first admission; received device
text batches are admitted when the upload settles (text completed or its grace expired).
Captures admit each chunk's published ``asr`` segments and ``device_text`` observations from
the chunk ``claim_extraction`` fan-in job that BE-06 enqueues once both are published.
Admission reads committed artifacts only, so a failed or restarted extraction never repeats
ASR or recognition. Speech and text stay distinct observations in one time-ordered ledger;
neither overrides the other, so disagreement reaches extraction and reconciliation intact.
"""

import logging
import uuid
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.captures import capture_reference, chunk_jobs, stage_key
from services.jobs.handlers import JobContext, JobHandler, StageResult
from services.jobs.models import job_results, jobs
from services.jobs.queue import ClaimedJob, JobQueue, PublishRejected
from services.jobs.retries import NonRetriableInput
from services.models import (
    capture_chunks,
    capture_sessions,
    extraction_runs,
    investigations,
    media_analysis,
    upload_text,
)
from services.pipeline.capture import lock_extraction_capture
from services.pipeline.extraction import Observation
from services.pipeline.incremental import ExtractionPolicy, submit_observations
from services.settings import Settings

STAGE = "claim_extraction"
OBSERVATION_MAX_CHARS = 8000
TERMINAL = frozenset({"published", "failed", "cancelled", "deleted"})
Source = Literal["capture", "upload"]
logger = logging.getLogger(__name__)


def extraction_policy(settings: Settings) -> ExtractionPolicy:
    return ExtractionPolicy(
        max_requests=settings.extraction_budget_requests,
        max_tokens=settings.extraction_budget_tokens,
        reconciliation_requests=settings.extraction_reserved_requests,
        reconciliation_tokens=settings.extraction_reserved_tokens,
        batch_observations=settings.extraction_batch_observations,
        overlap_observations=settings.extraction_overlap_observations,
        max_observations=settings.extraction_max_observations,
    )


def speech_observations(speech: dict[str, Any], prefix: str) -> list[Observation]:
    """Target observations from completed timed speech; original text and timebase kept.

    No speaker attribution is invented. A zero-length provider interval gets the smallest
    nonempty extent; text over the observation limit is split under the same interval.
    """
    if speech.get("status") != "completed":
        return []
    observations: list[Observation] = []
    for index, segment in enumerate(speech.get("segments", [])):
        text = segment["text"]
        interval = segment["interval"]
        start = interval["start_ms"]
        end = max(interval["end_ms"], start + 1)
        pieces = [
            text[offset : offset + OBSERVATION_MAX_CHARS]
            for offset in range(0, len(text), OBSERVATION_MAX_CHARS)
        ]
        for part, piece in enumerate(pieces):
            if not piece.strip():
                continue
            observations.append(
                Observation(
                    id=f"{prefix}:speech:{index}" + (f":{part}" if len(pieces) > 1 else ""),
                    role="target",
                    text=piece,
                    modality="speech",
                    timebase=interval["timebase"],
                    start_ms=start,
                    end_ms=end,
                    speaker_id=None,
                )
            )
    return observations


def text_observations(
    frames: list[dict[str, Any]], prefix: str, timebase: Literal["capture", "media"]
) -> list[Observation]:
    """Target observations from recognized device text at its sampled frame instant.

    The interval is the frame timestamp with the smallest nonempty extent: sampling cannot
    show how long the text stayed on screen, so no persistence is inferred. Stable producer
    identifiers make replayed frames idempotent.
    """
    return [
        Observation(
            id=f"{prefix}:text:{item['id']}",
            role="target",
            text=item["text"],
            modality="text",
            timebase=timebase,
            start_ms=item["frame_pts"],
            end_ms=item["frame_pts"] + 1,
            speaker_id=None,
        )
        for frame in frames
        for item in frame.get("text_observations", [])
        if item["text"].strip()
    ]


async def admit(
    connection: AsyncConnection,
    queue: JobQueue,
    job: ClaimedJob,
    investigation_id: uuid.UUID,
    settings: Settings,
    observations: list[Observation],
    source: Source,
    *,
    open_ledger: bool = False,
) -> None:
    """Admit inside the publishing transaction; the first run fixes policy and approvals."""
    owner = await connection.scalar(
        select(investigations.c.owner_id).where(investigations.c.id == investigation_id)
    )
    stored = await connection.scalar(
        select(extraction_runs.c.data).where(extraction_runs.c.investigation_id == investigation_id)
    )
    if owner is None or (stored is None and not observations and not open_ledger):
        return
    producer = (
        stored["producer"]
        if stored is not None and "producer" in stored
        else {"source": source, "reconcile": settings.reconciliation_enabled}
    )
    try:
        async with connection.begin_nested():
            await submit_observations(
                connection,
                queue,
                investigation_id,
                owner,
                policy=ExtractionPolicy.model_validate(stored["policy"])
                if stored is not None
                else extraction_policy(settings),
                observations=observations,
                closed=False,
                hosted_processing_approved=stored["hosted_processing_approved"]
                if stored is not None
                else settings.extraction_enabled,
                groq_processing_approved=stored["groq_processing_approved"]
                if stored is not None
                else settings.groq_extraction_enabled,
                producer=producer,
            )
    except NonRetriableInput as error:
        raise PublishRejected(job.id, "upstream observations were not admitted") from error


async def _published(
    connection: AsyncConnection, job: ClaimedJob, capture_id: uuid.UUID, seq: int, stage: str
) -> dict[str, Any]:
    key = stage_key(capture_id, seq, stage)
    result: dict[str, Any] | None = (
        await connection.execute(
            select(job_results.c.result)
            .join(jobs, jobs.c.id == job_results.c.job_id)
            .where(
                jobs.c.version == key.version,
                jobs.c.stage == key.stage,
                jobs.c.input_hash == key.input_hash,
                jobs.c.state == "published",
                jobs.c.owner_id
                == select(jobs.c.owner_id).where(jobs.c.id == job.id).scalar_subquery(),
            )
        )
    ).scalar_one_or_none()
    return result or {}


def build_capture_extraction(settings: Settings) -> JobHandler:
    """The chunk fan-in ``claim_extraction`` job: admit that chunk's committed speech and text."""

    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any] | StageResult:
        capture_id, seq = capture_reference(job)
        if job.key != stage_key(capture_id, seq, STAGE):
            raise NonRetriableInput("Capture stage key does not match its payload")
        async with context.queue.database.engine.connect() as connection:
            speech = (await _published(connection, job, capture_id, seq, "asr")).get("speech")
            text = (await _published(connection, job, capture_id, seq, "device_text")).get("text")
        result: dict[str, Any] = {"capture_id": str(capture_id), "seq": seq}
        if not settings.extraction_enabled:
            return {**result, "admitted": "disabled"}
        spoken = speech_observations(speech or {}, f"capture:{seq}")
        shown = (
            text_observations(text.get("frames", []), f"capture:{seq}", "capture")
            if text is not None and text.get("status") == "completed"
            else []
        )

        async def on_publish(connection: AsyncConnection) -> None:
            await admit(
                connection, context.queue, job, capture_id, settings, spoken + shown, "capture"
            )

        return StageResult(
            {**result, "speech_observations": len(spoken), "text_observations": len(shown)},
            on_publish,
        )

    return handle


def admit_upload_speech(handler: JobHandler, settings: Settings) -> JobHandler:
    """Wrap ``upload_asr`` (or media validation) so committed speech is admitted on publish.

    The ledger opens whenever a result settles uploaded speech, even as unavailable, so
    device text received later can still settle into extraction.
    """

    async def handle(job: ClaimedJob, context: JobContext) -> dict[str, Any] | StageResult:
        outcome = await handler(job, context)
        if (
            not settings.extraction_enabled
            or isinstance(outcome, StageResult)
            or "speech" not in outcome
        ):
            return outcome
        identifier = uuid.UUID(job.payload["investigation_id"])
        observations = speech_observations(outcome["speech"], "upload")

        async def on_publish(connection: AsyncConnection) -> None:
            await admit(
                connection,
                context.queue,
                job,
                identifier,
                settings,
                observations,
                "upload",
                open_ledger=True,
            )

        return StageResult(outcome, on_publish)

    return handle


async def _capture_settled(
    connection: AsyncConnection, identifier: uuid.UUID
) -> list[uuid.UUID] | None:
    session = (
        await connection.execute(
            select(capture_sessions).where(capture_sessions.c.id == identifier)
        )
    ).first()
    if session is None or session.state != "closed" or session.continue_research is not True:
        return None
    chunks = (
        await connection.execute(
            select(capture_chunks.c.seq, capture_chunks.c.job_id).where(
                capture_chunks.c.session_id == identifier,
                capture_chunks.c.received_at.is_not(None),
            )
        )
    ).all()
    states: list[str] = list(
        (
            await connection.execute(
                select(jobs.c.state).where(
                    chunk_jobs(identifier, [chunk.seq for chunk in chunks])
                    | jobs.c.id.in_([chunk.job_id for chunk in chunks if chunk.job_id is not None])
                )
            )
        ).scalars()
    )
    if any(state not in TERMINAL for state in states):
        return None
    # Chunk work is found by key when reconciliation is scheduled; nothing extra to accept.
    return []


async def _upload_settled(
    connection: AsyncConnection, identifier: uuid.UUID
) -> list[uuid.UUID] | None:
    """Speech settled and device text completed or expired; returns the accepted upstream jobs.

    No ``upload_asr`` job means media validation already published speech as unavailable
    (it opened this ledger), so only device text can still be pending.
    """
    from services.pipeline.speech import speech_stage_key

    key = speech_stage_key(identifier)
    speech = (
        await connection.execute(
            select(jobs.c.id, jobs.c.state).where(
                jobs.c.version == key.version,
                jobs.c.stage == key.stage,
                jobs.c.input_hash == key.input_hash,
            )
        )
    ).first()
    if speech is not None and speech.state not in TERMINAL:
        return None
    text = (
        await connection.execute(
            select(
                media_analysis.c.media_job_id,
                media_analysis.c.text_expired,
                upload_text.c.completion,
            )
            .outerjoin(upload_text, upload_text.c.media_job_id == media_analysis.c.media_job_id)
            .where(media_analysis.c.investigation_id == identifier)
        )
    ).first()
    if text is None:
        return None
    if not text.text_expired and text.completion is None:
        return None
    return [*([speech.id] if speech is not None else []), text.media_job_id]


async def _upload_text(connection: AsyncConnection, identifier: uuid.UUID) -> list[Observation]:
    """Received device text batches, admitted once text completed or its grace expired."""
    batches = await connection.scalar(
        select(upload_text.c.batches).where(upload_text.c.id == identifier)
    )
    frames = [frame for batch in batches or [] for frame in batch["body"]["frames"]]
    return text_observations(frames, "upload", "media")


async def admit_late_text(
    connection: AsyncConnection, queue: JobQueue, identifier: uuid.UUID
) -> None:
    """Device text received after its upload settled is reported ``input_closed``, not hidden.

    Runs in the batch-receiving transaction after the batch is written. Locking the
    investigation first orders it against settlement, which reads batches under that lock.
    """
    owner = await connection.scalar(
        select(investigations.c.owner_id).where(investigations.c.id == identifier).with_for_update()
    )
    data = await connection.scalar(
        select(extraction_runs.c.data).where(extraction_runs.c.investigation_id == identifier)
    )
    if owner is None or data is None or "producer" not in data or not data["closed"]:
        return
    try:
        async with connection.begin_nested():
            await submit_observations(
                connection,
                queue,
                identifier,
                owner,
                policy=ExtractionPolicy.model_validate(data["policy"]),
                observations=await _upload_text(connection, identifier),
                closed=True,
                hosted_processing_approved=data["hosted_processing_approved"],
                groq_processing_approved=data["groq_processing_approved"],
                producer=data["producer"],
            )
    except NonRetriableInput as error:
        logger.warning("Extraction input %s not settled (%s)", identifier, type(error).__name__)


async def settle_inputs(queue: JobQueue) -> None:
    """Close producer ledgers whose upstream work is terminal and register reconciliation.

    Captures settle only after a continue-in-queue close once every received chunk's work is
    terminal; uploads after speech is terminal and device text completed or its grace
    expired. Keep-only-results captures never settle, so no new inference starts.
    """
    from services.pipeline.reconciliation import request_reconciliation

    async with queue.database.engine.connect() as connection:
        candidates: list[uuid.UUID] = list(
            (
                await connection.execute(
                    select(extraction_runs.c.investigation_id).where(
                        extraction_runs.c.data["producer"].is_not(None),
                        extraction_runs.c.data["closed"].astext == "false",
                    )
                )
            )
            .scalars()
            .all()
        )
    for identifier in candidates:
        async with queue.database.engine.begin() as connection:
            if not await lock_extraction_capture(connection, identifier):
                continue
            owner = await connection.scalar(
                select(investigations.c.owner_id)
                .where(investigations.c.id == identifier)
                .with_for_update()
            )
            data = await connection.scalar(
                select(extraction_runs.c.data)
                .where(extraction_runs.c.investigation_id == identifier)
                .with_for_update()
            )
            if owner is None or data is None or data["closed"]:
                continue
            producer = data["producer"]
            accepted = await (
                _capture_settled(connection, identifier)
                if producer["source"] == "capture"
                else _upload_settled(connection, identifier)
            )
            if accepted is None:
                continue
            observations = (
                await _upload_text(connection, identifier) if producer["source"] == "upload" else []
            )
            try:
                async with connection.begin_nested():
                    await submit_observations(
                        connection,
                        queue,
                        identifier,
                        owner,
                        policy=ExtractionPolicy.model_validate(data["policy"]),
                        observations=observations,
                        closed=True,
                        hosted_processing_approved=data["hosted_processing_approved"],
                        groq_processing_approved=data["groq_processing_approved"],
                        producer=producer,
                    )
                    admitted = await connection.scalar(
                        select(extraction_runs.c.data["observations"]).where(
                            extraction_runs.c.investigation_id == identifier
                        )
                    )
                    if producer.get("reconcile") and admitted:
                        await request_reconciliation(
                            connection, identifier, owner, accepted_jobs=accepted
                        )
            except NonRetriableInput as error:
                logger.warning(
                    "Extraction input %s not settled (%s)", identifier, type(error).__name__
                )
