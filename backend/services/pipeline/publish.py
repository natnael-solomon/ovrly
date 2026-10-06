"""Stage publication for the worker: each pipeline's fenced publish with successors."""

from typing import Any

from services.evidence.stages import ASSESSMENT_STAGE, RETRIEVAL_STAGE, publish_evidence_stage
from services.jobs.queue import ClaimedJob, JobQueue, PublishedResult
from services.pipeline.capture import publish_capture_stage
from services.reports import REANALYSIS_STAGE

EVIDENCE_STAGES = frozenset({RETRIEVAL_STAGE, ASSESSMENT_STAGE, REANALYSIS_STAGE})


async def publish_stage(
    queue: JobQueue, job: ClaimedJob, result: dict[str, Any]
) -> PublishedResult | None:
    """``None`` means the stage was cancelled instead of published."""
    if job.key.stage in EVIDENCE_STAGES:
        return await publish_evidence_stage(queue, job, result)
    return await publish_capture_stage(queue, job, result)
