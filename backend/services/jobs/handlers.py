"""Stage handler protocol and the per-job context handed to handlers."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncConnection

from services.asr.groq import ASRAdapter
from services.jobs.faults import Checkpoint, FaultInjector, NoFaults
from services.jobs.queue import ClaimedJob, JobQueue, Lease, StageKey
from services.settings import Settings
from services.storage import UploadStore

if TYPE_CHECKING:
    from services.pipeline.llm import LlmAdapter


class CancellationRequested(Exception):
    """Raised by :meth:`JobContext.heartbeat` once a cancellation request is observed."""


class JobContext:
    def __init__(
        self,
        queue: JobQueue,
        lease: Lease,
        lease_seconds: float,
        faults: FaultInjector | None = None,
        *,
        on_heartbeat: Callable[[], None] | None = None,
    ):
        self.queue = queue
        self.lease = lease
        self.lease_seconds = lease_seconds
        self.faults: FaultInjector = faults if faults is not None else NoFaults()
        self.on_heartbeat = on_heartbeat
        self.successors: list[tuple[StageKey, dict[str, Any]]] = []

    def enqueue_after_publish(self, key: StageKey, payload: dict[str, Any]) -> None:
        """Stage a handoff for the same fenced transaction as publication."""
        self.successors.append((key, payload))

    async def heartbeat(self) -> None:
        """Extend the lease; raise if it was lost or cancellation was requested."""
        cancel_requested = await self.queue.heartbeat(self.lease, self.lease_seconds)
        if self.on_heartbeat is not None:
            self.on_heartbeat()
        if cancel_requested:
            raise CancellationRequested(f"Cancellation requested for job {self.lease.job_id}")

    async def record_request_id(self, request_id: str) -> None:
        """Record the provider request id *before* calling the provider.

        An :class:`~services.jobs.retries.UnknownOutcome` is only retried when this was
        called; the next attempt sees the id on ``ClaimedJob.provider_request_id`` and
        reconciles instead of calling the provider again.
        """
        await self.queue.record_request_id(self.lease, request_id)

    async def checkpoint(self, name: Checkpoint, job: ClaimedJob) -> None:
        """Let the fault injector act at a stage-level checkpoint (tests only)."""
        await self.faults.checkpoint(name, job)


@dataclass(frozen=True)
class StageResult:
    result: dict[str, Any]
    on_publish: Callable[[AsyncConnection], Awaitable[None]]


JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[dict[str, Any] | StageResult]]


def default_handlers(
    store: UploadStore | None = None,
    *,
    llm: "LlmAdapter | None" = None,
    fallback_llm: "LlmAdapter | None" = None,
    reconciliation_llm: "LlmAdapter | None" = None,
    settings: Settings | None = None,
    asr_adapter: ASRAdapter | None = None,
    quota_clock: Callable[[], datetime] | None = None,
) -> Mapping[str, JobHandler]:
    """Intake, capture validation, configured media preparation, extraction and reconciliation."""
    # Imported here because the stage modules import JobContext from this module.
    from services.captures import CAPTURE_STAGE, CaptureProcessor
    from services.pipeline.capture_media import build_capture_speech, build_capture_text
    from services.pipeline.extraction import ExtractionStage
    from services.pipeline.intake import INTAKE_STAGE, intake_stage
    from services.pipeline.llm import configured_groq, configured_llm
    from services.pipeline.media_validation import MEDIA_STAGE, build_media_validation
    from services.pipeline.producers import admit_upload_speech, build_capture_extraction
    from services.pipeline.reconciliation import ReconciliationStage
    from services.pipeline.speech import ASR_STAGE, build_speech
    from services.provider_budgets import llm_admission

    admission = llm_admission(settings)
    handlers: dict[str, JobHandler] = {INTAKE_STAGE: intake_stage}
    if store is not None:
        handlers[CAPTURE_STAGE] = CaptureProcessor(store).run
    extraction = ExtractionStage(
        llm if llm is not None else configured_llm(settings),
        fallback_llm if fallback_llm is not None else configured_groq(settings),
        admission,
    ).run
    handlers["claim_extraction"] = extraction
    handlers["reconciliation"] = ReconciliationStage(
        reconciliation_llm
        if reconciliation_llm is not None
        else configured_llm(settings, task="reconciliation"),
        admission,
    ).run
    if settings is not None:
        upload_media = admit_upload_speech(build_media_validation(settings), settings)
        capture_media = handlers.get(CAPTURE_STAGE)
        capture_extraction = build_capture_extraction(settings)

        async def media(job: ClaimedJob, context: JobContext) -> dict[str, Any] | StageResult:
            if capture_media is not None and (
                "capture_id" in job.payload or "session_id" in job.payload
            ):
                return await capture_media(job, context)
            return await upload_media(job, context)

        async def claim_extraction(
            job: ClaimedJob, context: JobContext
        ) -> dict[str, Any] | StageResult:
            if "capture_id" in job.payload or "session_id" in job.payload:
                return await capture_extraction(job, context)
            return await extraction(job, context)

        handlers[MEDIA_STAGE] = media
        handlers["claim_extraction"] = claim_extraction
        handlers[ASR_STAGE] = admit_upload_speech(
            build_speech(settings, adapter=asr_adapter, quota_clock=quota_clock), settings
        )
        if store is not None:
            handlers["asr"] = build_capture_speech(
                settings, store, adapter=asr_adapter, quota_clock=quota_clock
            )
            handlers["device_text"] = build_capture_text(store)
    return handlers
