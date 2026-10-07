"""Durable extraction deadlines and truthful observations, independent of research."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.schemas import AnalysisRead
from services.jobs.models import jobs
from services.models import media_analysis, upload_text, uploads


def now() -> datetime:
    """Wall-clock boundary shared by terminal publication and due-work resolution."""
    return datetime.now(UTC)


async def record_terminal(
    connection: AsyncConnection, job_id: UUID, result: dict[str, Any] | None = None
) -> None:
    job = (await connection.execute(select(jobs).where(jobs.c.id == job_id))).one()
    if job.stage == "media_validation" and result is not None and "text_grace_seconds" in result:
        identifier = UUID(job.payload["investigation_id"])
        grace = result["text_grace_seconds"]
        await connection.execute(
            insert(media_analysis)
            .values(
                media_job_id=job_id,
                investigation_id=identifier,
                grace_seconds=grace,
                text_deadline=now() + timedelta(seconds=grace) if "speech" in result else None,
            )
            .on_conflict_do_nothing()
        )
    elif job.stage == "upload_asr" and job.payload.get("media_job_id"):
        media_id = UUID(job.payload["media_job_id"])
        state = (
            await connection.execute(
                select(media_analysis)
                .where(media_analysis.c.media_job_id == media_id)
                .with_for_update()
            )
        ).first()
        if state is not None and state.text_deadline is None:
            await connection.execute(
                update(media_analysis)
                .where(media_analysis.c.media_job_id == media_id)
                .values(text_deadline=now() + timedelta(seconds=state.grace_seconds))
            )


async def resolve_due(connection: AsyncConnection) -> None:
    await connection.execute(
        update(media_analysis)
        .where(
            media_analysis.c.text_expired.is_(False),
            media_analysis.c.text_deadline <= now(),
            ~select(upload_text.c.id)
            .where(
                upload_text.c.media_job_id == media_analysis.c.media_job_id,
                upload_text.c.completion["batch_count"].as_integer().is_not(None),
            )
            .exists(),
        )
        .values(text_expired=True)
    )


async def uploaded_analysis(
    connection: AsyncConnection,
    investigation: Row[Any],
    media: dict[str, Any],
    speech: dict[str, Any],
) -> AnalysisRead | None:
    state = (
        await connection.execute(
            select(media_analysis, jobs.c.state.label("job_state"))
            .join(jobs, jobs.c.id == media_analysis.c.media_job_id)
            .where(media_analysis.c.investigation_id == investigation.id)
        )
    ).first()
    if state is None or state.job_state != "published":
        return None
    document = (
        await connection.execute(select(upload_text).where(upload_text.c.id == investigation.id))
    ).first()
    text = None
    if document is not None:
        text_job = (
            await connection.execute(select(jobs).where(jobs.c.id == document.job_id))
        ).first()
        if (
            document.media_job_id != state.media_job_id
            or text_job is None
            or text_job.state in {"cancelled", "deleted"}
            or text_job.cancel_requested
        ):
            return None
        source = (
            await connection.execute(select(uploads).where(uploads.c.id == investigation.upload_id))
        ).one()
        text = {
            "investigation_id": str(investigation.id),
            "upload_id": str(source.id),
            "source_sha256": source.declared_sha256,
            "status": "completed" if document.completion is not None else "receiving",
            "job_id": str(document.job_id),
            "batches": document.batches,
            "completion": document.completion,
        }
    return summarize(media, speech, text, state.text_deadline, state.text_expired)


def speech_gap(reason: str | None) -> str:
    """Gap code for an unavailable speech reason; the reason itself stays on ``speech``."""
    return {
        "no_audio_track": "NO_AUDIO_TRACK",
        "quota_exhausted": "ASR_QUOTA_EXHAUSTED",
        "unknown_outcome": "ASR_OUTCOME_UNKNOWN",
        "disabled": "ASR_DISABLED",
    }.get(reason or "", "ASR_UNAVAILABLE")


def summarize(
    media: dict[str, Any],
    speech: dict[str, Any],
    text: dict[str, Any] | None,
    deadline: datetime | None,
    expired: bool,
) -> AnalysisRead:
    span = {"start_ms": 0, "end_ms": media["coverage"]["total_ms"], "timebase": "media"}
    analyzed: list[str] = []
    pending: list[str] = []
    unavailable: list[str] = []
    gaps: list[dict[str, Any]] = []
    usable = bool(speech.get("segments"))
    if speech["status"] == "completed":
        analyzed.append("speech")
    elif speech["status"] in {"pending", "running"}:
        pending.append("speech")
    else:
        unavailable.append("speech")
        reason = speech_gap(speech.get("reason"))
        gaps.append({"modality": "speech", "reason": reason, "interval": span})
    frames = [
        frame for batch in (text["batches"] if text else []) for frame in batch["body"]["frames"]
    ]
    if any(frame["status"] in {"recognized", "no_text_regions"} for frame in frames):
        analyzed.append("text")
    usable = usable or any(frame["text_observations"] for frame in frames)
    completion = text.get("completion") if text else None
    if completion is None:
        if expired:
            unavailable.append("text")
            gaps.append(
                {
                    "modality": "text",
                    "reason": "DEVICE_TEXT_INCOMPLETE" if frames else "DEVICE_TEXT_MISSING",
                    "interval": span,
                }
            )
        else:
            pending.append("text")
    for frame in frames:
        if frame["status"] == "failed" or frame["failed_regions"]:
            gaps.append(
                {"modality": "text", "reason": "DEVICE_TEXT_FRAME_FAILED", "interval": None}
            )
    if completion is not None and any(
        completion[name] for name in ("dropped_frames", "capped_frames", "unfinished_frames")
    ):
        gaps.append({"modality": "text", "reason": "DEVICE_TEXT_INCOMPLETE", "interval": span})
    status = (
        ("partial" if gaps or pending else "complete")
        if usable
        else ("pending" if pending else "no_usable")
    )
    return AnalysisRead.model_validate(
        {
            "status": status,
            "text_deadline": deadline,
            "text_expired": expired,
            "analyzed_modalities": analyzed,
            "pending_modalities": pending,
            "unavailable_modalities": unavailable,
            "gaps": gaps,
            "text": text,
            "captions": [],
        }
    )
