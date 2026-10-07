"""Stage publication for the worker: each pipeline's fenced publish with successors."""

from collections.abc import Sequence
from typing import Any

from services.evidence.stages import ASSESSMENT_STAGE, RETRIEVAL_STAGE, publish_evidence_stage
from services.jobs.handlers import StageResult
from services.jobs.queue import ClaimedJob, JobQueue, PublishedResult, StageKey
from services.pipeline.capture import publish_capture_stage
from services.reports import REANALYSIS_STAGE

EVIDENCE_STAGES = frozenset({RETRIEVAL_STAGE, ASSESSMENT_STAGE, REANALYSIS_STAGE})


async def publish_stage(
    queue: JobQueue,
    job: ClaimedJob,
    result: dict[str, Any] | StageResult,
    *,
    successors: Sequence[tuple[StageKey, dict[str, Any]]] = (),
) -> PublishedResult | None:
    """``None`` means the stage was cancelled instead of published."""
    if job.key.stage in EVIDENCE_STAGES:
        if isinstance(result, StageResult):
            raise TypeError("Evidence stages publish plain results")
        return await publish_evidence_stage(queue, job, result)
    if successors:
        if isinstance(result, StageResult):
            return await queue.publish(
                job.lease, result.result, on_publish=result.on_publish, successors=successors
            )
        return await queue.publish(job.lease, result, successors=successors)
    return await publish_capture_stage(queue, job, result)
