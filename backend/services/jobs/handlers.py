"""Stage handler protocol and the per-job context handed to handlers."""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from services.asr.groq import ASRAdapter
from services.jobs.faults import Checkpoint, FaultInjector, NoFaults
from services.jobs.queue import ClaimedJob, JobQueue, Lease, StageKey
from services.settings import Settings
from services.storage import UploadStore


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


JobHandler = Callable[[ClaimedJob, JobContext], Awaitable[dict[str, Any]]]


def default_handlers(
    store: UploadStore | None = None,
    *,
    settings: Settings | None = None,
    asr_adapter: ASRAdapter | None = None,
    quota_clock: Callable[[], datetime] | None = None,
) -> Mapping[str, JobHandler]:
    """Intake, capture byte validation, and configured uploaded-media preparation."""
    # Imported here because the stage modules import JobContext from this module.
    from services.captures import CAPTURE_STAGE, CaptureProcessor
    from services.pipeline.capture_media import build_capture_speech, build_capture_text
    from services.pipeline.intake import INTAKE_STAGE, intake_stage
    from services.pipeline.media_validation import MEDIA_STAGE, build_media_validation
    from services.pipeline.speech import ASR_STAGE, build_speech

    handlers: dict[str, JobHandler] = {INTAKE_STAGE: intake_stage}
    if store is not None:
        handlers[CAPTURE_STAGE] = CaptureProcessor(store).run
    if settings is not None:
        upload_media = build_media_validation(settings)
        capture_media = handlers.get(CAPTURE_STAGE)

        async def media(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
            if capture_media is not None and (
                "capture_id" in job.payload or "session_id" in job.payload
            ):
                return await capture_media(job, context)
            return await upload_media(job, context)

        handlers[MEDIA_STAGE] = media
        handlers[ASR_STAGE] = build_speech(settings, adapter=asr_adapter, quota_clock=quota_clock)
        if store is not None:
            handlers["asr"] = build_capture_speech(
                settings, store, adapter=asr_adapter, quota_clock=quota_clock
            )
            handlers["device_text"] = build_capture_text(store)
    return handlers
