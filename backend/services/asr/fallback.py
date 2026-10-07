"""RFC-D27 hosted speech policy (decision 0004): the primary model, then the fallback once.

``evaluation/asr_policy.py`` is the reference contract. Audio problems are refused by the
caller before any call. Any provider failure of the primary model (quota, rate limit,
outage, offline, invalid response or unknown outcome) tries the fallback model once; if that
also fails the chunk is ``ASR_UNAVAILABLE`` with the last attempt's reason. An observed empty
result is ``no_speech``, never a failure.

Each model reserves its own quota and has its own ledger row per run, written as
``uncertain`` before HTTP. A row that may have reached the provider is never sent again, so a
crash or interruption moves on to the fallback instead of silently repeating a model.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.asr.groq import (
    FAILURE_BY_REASON,
    ASRAdapter,
    ASRQuotaExhausted,
    ASRUnavailable,
    failure_reason,
)
from services.asr.reservations import attempts, begin, complete
from services.jobs.faults import Checkpoint
from services.jobs.handlers import JobContext
from services.jobs.queue import ClaimedJob
from services.jobs.retries import RetryableError
from services.pipeline.media_validation import _heartbeat_while
from services.settings import Settings


@dataclass(frozen=True)
class SpeechAudio:
    """Checked audio for one request; ``quota_seconds`` is what each reservation counts."""

    audio: bytes
    duration_ms: int
    quota_seconds: float
    sha256: str


def models(settings: Settings) -> tuple[str, str]:
    return settings.groq_model, settings.groq_fallback_model


async def transcribe(
    job: ClaimedJob,
    context: JobContext,
    settings: Settings,
    provider: ASRAdapter,
    *,
    load: Callable[[], Awaitable[SpeechAudio]],
    offset_ms: int,
    build: Callable[[str, list[dict[str, Any]], SpeechAudio], dict[str, Any]],
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Return the stored or new speech result, or raise the terminal ``ASRUnavailable``."""
    ledger = await attempts(job, context)
    for recorded in ledger.values():
        if recorded.outcome == "completed":
            stored: dict[str, Any] = recorded.result
            return stored
    speech = await load()
    order = models(settings)
    reasons: list[str] = []
    for position, model in enumerate(order):
        row = ledger.get(model)
        if row is None and any(later in ledger for later in order[position + 1 :]):
            # The run already moved past this model without a request: its quota was full.
            reasons.append("quota_exhausted")
            continue
        if row is not None and row.outcome != "reserved":
            reasons.append(failure_reason(row.outcome))
            continue
        try:
            request_id = await begin(job, context, settings, model, speech.quota_seconds, clock)
        except ASRQuotaExhausted:
            reasons.append("quota_exhausted")
            continue
        await context.checkpoint(Checkpoint.BEFORE_PROVIDER_CALL, job)
        await context.heartbeat()
        try:
            segments = await _heartbeat_while(
                context,
                provider.transcribe(
                    speech.audio,
                    duration_ms=speech.duration_ms,
                    offset_ms=offset_ms,
                    model=model,
                ),
            )
        except (ASRUnavailable, RetryableError) as error:
            await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
            await complete(job, context, request_id, None, error=error)
            await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
            reasons.append(failure_reason(type(error).__name__))
            continue
        await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
        result = build(model, segments, speech)
        await complete(job, context, request_id, result)
        await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
        return result
    raise FAILURE_BY_REASON[reasons[-1]]
