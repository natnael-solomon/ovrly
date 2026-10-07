"""Lease-safe provider waits and durable invalid-output feedback shared by LLM stages."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any

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

logger = logging.getLogger(__name__)


async def wait_for_provider[T](pending: Awaitable[T], context: JobContext) -> T:
    """Await a provider call while keeping its lease alive.

    The lease is renewed just before the call starts, then every third of the lease measured
    from the start of each renewal, so database latency cannot stretch the gap. The first
    renewal completes before the call begins, so it never competes with the call's own
    accounting transaction for a pooled connection. Renewal is strictly fenced on an
    unexpired lease, which leaves at least two thirds of the lease for renewal latency and
    scheduling delay.
    """
    loop = asyncio.get_running_loop()
    interval = context.lease_seconds / 3
    next_renewal = loop.time() + interval
    try:
        await context.heartbeat()
    except BaseException:
        if inspect.iscoroutine(pending):
            pending.close()
        raise
    task = asyncio.ensure_future(pending)
    try:
        while not task.done():
            delay = next_renewal - loop.time()
            if delay > 0:
                done, _ = await asyncio.wait({task}, timeout=delay)
                if done:
                    break
            next_renewal = loop.time() + interval
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
