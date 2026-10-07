"""Lease-safe provider waits and durable invalid-output feedback shared by LLM stages."""

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, TypeVar

from services.jobs.handlers import JobContext
from services.jobs.queue import ClaimedJob
from services.jobs.retries import RateLimited, RetryableError, UnknownOutcome
from services.pipeline.llm import (
    ExtractionBudgetExceeded,
    ExtractionDenied,
    InvalidCooldown,
    LlmAdapter,
    RequestAccount,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def wait_for_provider(pending: Awaitable[T], context: JobContext) -> T:
    task = asyncio.ensure_future(pending)
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=context.lease_seconds / 3)
            if not done:
                await context.heartbeat()
        return await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def invalid_feedback(
    active: LlmAdapter,
    data: dict[str, Any],
    job: ClaimedJob,
    context: JobContext,
    account: RequestAccount,
) -> None:
    attempt = data["attempts"][-1]
    stop: RetryableError | None = None
    if active.provider != "scholarxiv":
        attempt["feedback"] = "not_applicable"
    elif not attempt.get("decision_id"):
        attempt["feedback"] = "feedback_missing"
    else:
        attempt["feedback"] = "feedback_unknown"
        await context.queue.save_stage_data(job.lease, data)
        try:
            await wait_for_provider(
                active.feedback(attempt["decision_id"], account=account), context
            )
        except UnknownOutcome:
            attempt["feedback"] = "feedback_unknown"
        except RetryableError as error:
            attempt["feedback"] = "feedback_failed"
            if isinstance(error, (ExtractionDenied, InvalidCooldown, ExtractionBudgetExceeded)):
                data["feedback_blocked"] = type(error).__name__
                stop = error
            elif isinstance(error, RateLimited):
                data["not_before"] = datetime.now(UTC).timestamp() + (
                    error.retry_after_seconds or 0
                )
                stop = error
        else:
            attempt["feedback"] = "feedback_sent"
    logger.warning("LLM job %s diagnostic %s", job.id, attempt["feedback"])
    await context.queue.save_stage_data(job.lease, data)
    if stop is not None and sum(item.get("valid") is False for item in data["attempts"]) < 2:
        raise stop
