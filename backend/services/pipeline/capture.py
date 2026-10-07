"""Fenced capture-stage publication and idempotent fan-out/fan-in."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.captures import (
    CAPTURE_STAGE,
    CAPTURE_STAGES,
    capture_reference,
    chunk_jobs,
    stage_key,
)
from services.jobs.handlers import StageResult
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, JobQueue, PublishedResult, PublishRejected
from services.models import capture_chunks, capture_sessions


async def lock_extraction_capture(connection: AsyncConnection, identifier: UUID) -> bool:
    """Serialize fixture-backed extraction with Stop without impersonating chunk work."""
    session = (
        await connection.execute(
            select(capture_sessions.c.continue_research)
            .where(capture_sessions.c.id == identifier)
            .with_for_update()
        )
    ).first()
    return session is None or session.continue_research is not False


async def publish_capture_stage(
    queue: JobQueue, job: ClaimedJob, result: dict[str, Any] | StageResult
) -> PublishedResult | None:
    on_publish = result.on_publish if isinstance(result, StageResult) else None
    payload = result.result if isinstance(result, StageResult) else result
    if job.key.stage in {"claim_extraction", "reconciliation"} and job.payload.get("incremental"):
        async with queue.database.engine.begin() as connection:
            if not await lock_extraction_capture(connection, UUID(job.payload["investigation_id"])):
                await queue.request_cancel(job.id, connection=connection)
                return None
            return await queue.publish(
                job.lease, payload, connection=connection, on_publish=on_publish
            )
    if job.key.stage not in CAPTURE_STAGES or not (
        "capture_id" in job.payload or "session_id" in job.payload
    ):
        return await queue.publish(job.lease, payload, on_publish=on_publish)
    capture_id, seq = capture_reference(job)
    if job.key != stage_key(capture_id, seq, job.key.stage):
        raise PublishRejected(job.id, "capture stage key does not match its payload")
    async with queue.database.engine.begin() as connection:
        # Same lock order as upload, Stop and retention: session before jobs. It also
        # serializes the two observation publishers, so neither can miss the fan-in.
        session = (
            await connection.execute(
                select(capture_sessions)
                .where(
                    capture_sessions.c.id == capture_id,
                    capture_sessions.c.owner_id
                    == select(jobs.c.owner_id).where(jobs.c.id == job.id).scalar_subquery(),
                )
                .with_for_update()
            )
        ).first()
        chunk = (
            await connection.execute(
                select(capture_chunks).where(
                    capture_chunks.c.session_id == capture_id,
                    capture_chunks.c.seq == seq,
                    capture_chunks.c.received_at.is_not(None),
                )
            )
        ).first()
        stages = {
            row.stage: row
            for row in (
                await connection.execute(
                    select(jobs)
                    .where(chunk_jobs(capture_id, [seq]))
                    .order_by(jobs.c.id)
                    .with_for_update()
                )
            ).all()
        }
        root = stages.get(CAPTURE_STAGE)
        if (
            session is None
            or session.continue_research is False
            or chunk is None
            or root is None
            or chunk.job_id != root.id
            or root.state in {"cancelled", "deleted", "failed"}
            or root.cancel_requested
        ):
            await queue.request_cancel(job.id, connection=connection)
            return None
        if any(row.owner_id != session.owner_id for row in stages.values()):
            raise PublishRejected(job.id, "capture stages have inconsistent ownership")
        prerequisites = (
            ("asr", "device_text")
            if job.key.stage == "claim_extraction"
            else (CAPTURE_STAGE,)
            if job.key.stage != CAPTURE_STAGE
            else ()
        )
        if any(name not in stages or stages[name].state != "published" for name in prerequisites):
            raise PublishRejected(job.id, "capture prerequisites are not published")
        published = await queue.publish(
            job.lease, payload, connection=connection, on_publish=on_publish
        )
        successors: tuple[str, ...] = ()
        if job.key.stage == CAPTURE_STAGE:
            successors = ("asr", "device_text")
        elif job.key.stage in {"asr", "device_text"}:
            other = "device_text" if job.key.stage == "asr" else "asr"
            if other in stages and stages[other].state == "published":
                successors = ("claim_extraction",)
        for stage in successors:
            await queue.enqueue(
                connection,
                stage_key(capture_id, seq, stage),
                {"capture_id": str(capture_id), "seq": seq},
                owner_id=session.owner_id,
            )
        return published
