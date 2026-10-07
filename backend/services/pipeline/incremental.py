"""Observation admission, overlapping batches and the durable extraction ledger."""

import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.errors import extraction_failure_code
from services.api.schemas import ExtractionProgress, ObservationProgress
from services.claims import StrictModel
from services.jobs.models import jobs
from services.jobs.queue import Enqueued, JobQueue
from services.jobs.retries import NonRetriableInput
from services.models import capture_sessions, extraction_runs, investigations
from services.pipeline.capture import lock_extraction_capture
from services.pipeline.extraction import Observation, ObservationWindow, enqueue_extraction
from services.pipeline.llm import ExtractionBudgetExceeded
from services.reports import latest_reports


class ExtractionPolicy(StrictModel):
    max_requests: Annotated[int, Field(ge=1, le=10000)]
    max_tokens: Annotated[int, Field(ge=1, le=2147483647)]
    reconciliation_requests: Annotated[int, Field(ge=1)]
    reconciliation_tokens: Annotated[int, Field(ge=1)]
    batch_observations: Annotated[int, Field(ge=1, le=64)]
    overlap_observations: Annotated[int, Field(ge=1, le=32)]
    max_observations: Annotated[int, Field(ge=1, le=4096)]

    @model_validator(mode="after")
    def reserves(self) -> Self:
        if (
            self.reconciliation_requests >= self.max_requests
            or self.reconciliation_tokens >= self.max_tokens
        ):
            raise ValueError("Extraction requires capacity outside the reconciliation reserve")
        return self


async def _save(connection: AsyncConnection, identifier: uuid.UUID, data: dict[str, Any]) -> None:
    await connection.execute(
        update(extraction_runs)
        .where(extraction_runs.c.investigation_id == identifier)
        .values(data=data)
    )


async def submit_observations(
    connection: AsyncConnection,
    queue: JobQueue,
    investigation_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    policy: ExtractionPolicy,
    observations: list[Observation],
    closed: bool,
    hosted_processing_approved: bool,
    groq_processing_approved: bool = False,
    producer: dict[str, Any] | None = None,
) -> list[Enqueued]:
    """Atomically accept stable observations and queue ready windows; no public intake route.

    Policy and authorization are immutable for an investigation. Duplicate observations
    replay; changed content under an existing identifier is rejected. A real upstream
    ``producer`` never raises for late or over-bound input: it is recorded as unadmitted and
    reported as skipped, so a publication callback cannot lose or hide accepted content.
    """
    if not await lock_extraction_capture(connection, investigation_id):
        raise NonRetriableInput("Capture was stopped without continued research")
    owned = await connection.scalar(
        select(investigations.c.id)
        .where(
            investigations.c.id == investigation_id,
            investigations.c.owner_id == owner_id,
        )
        .with_for_update()
    )
    if owned is None or not hosted_processing_approved:
        raise NonRetriableInput("Owned investigation and hosted authorization are required")
    previous_report = (await latest_reports(connection, [investigation_id])).get(investigation_id)
    finalized = previous_report is not None and (
        not previous_report.provisional or previous_report.reconciliation is not None
    )
    if finalized and producer is None:
        raise NonRetriableInput("Incremental extraction cannot reopen a finalized report")
    stored = await connection.scalar(
        select(extraction_runs.c.data)
        .where(extraction_runs.c.investigation_id == investigation_id)
        .with_for_update()
    )
    config = {
        "policy": policy.model_dump(),
        "hosted_processing_approved": hosted_processing_approved,
        "groq_processing_approved": groq_processing_approved,
        **({"producer": producer} if producer is not None else {}),
    }
    data: dict[str, Any] = (
        dict(stored)
        if stored is not None
        else {
            **config,
            "closed": False,
            "observations": [],
            "windows": [],
            "requests_used": 0,
            "tokens_reserved": 0,
        }
    )
    if any(data.get(key) != value for key, value in config.items()):
        raise NonRetriableInput("Extraction policy and authorization cannot change during a run")
    existing = {item["source"]["id"]: item for item in data["observations"]}
    unadmitted = {item["id"]: item for item in data.get("unadmitted", [])}
    for observation in observations:
        source = observation.model_dump(mode="json")
        previous = existing.get(observation.id)
        if previous is not None:
            if previous["source"] != source:
                raise NonRetriableInput("Observation identifier was reused for changed content")
            continue
        if producer is not None and observation.id not in unadmitted:
            reason = (
                "input_closed"
                if data["closed"] or finalized
                else "budget_exhausted"
                if len(existing) >= policy.max_observations
                else None
            )
            if reason is not None:
                unadmitted[observation.id] = {
                    "id": observation.id,
                    "start_ms": observation.start_ms,
                    "end_ms": observation.end_ms,
                    "timebase": observation.timebase,
                    "reason": reason,
                }
                continue
        elif observation.id in unadmitted:
            continue
        if data["closed"]:
            raise NonRetriableInput("Observation input is closed")
        existing[observation.id] = {"source": source, "job_id": None, "window_ids": []}
    if unadmitted:
        data["unadmitted"] = sorted(
            unadmitted.values(), key=lambda item: (item["start_ms"], item["end_ms"], item["id"])
        )
    if len(existing) > policy.max_observations:
        raise NonRetriableInput("Observation admission exceeds the explicit run bound")
    ordered = sorted(
        existing.values(),
        key=lambda item: (
            item["source"]["start_ms"],
            item["source"]["end_ms"],
            item["source"]["id"],
        ),
    )
    if len({item["source"]["timebase"] for item in ordered}) > 1:
        raise NonRetriableInput("Observation input must use one original timebase")
    data["observations"] = ordered
    data["closed"] = data["closed"] or closed
    if stored is None:
        await connection.execute(
            insert(extraction_runs).values(investigation_id=investigation_id, data=data)
        )
    pending = (
        [item for item in ordered if item["job_id"] is None and not item.get("reason")]
        if not finalized
        else []
    )
    result: list[Enqueued] = []
    while pending:
        pressure = max(
            max(data["requests_used"], len(data["windows"]))
            / (policy.max_requests - policy.reconciliation_requests),
            data["tokens_reserved"] / (policy.max_tokens - policy.reconciliation_tokens),
        )
        threshold = min(
            64, policy.batch_observations * (4 if pressure >= 0.75 else 2 if pressure >= 0.5 else 1)
        )
        if len(pending) < threshold and not data["closed"]:
            break
        start = ordered.index(pending[0])
        overlap = [item for item in ordered[:start] if item["job_id"] is not None][
            -policy.overlap_observations :
        ]
        while (
            overlap
            and sum(len(item["source"]["text"]) for item in overlap)
            + len(pending[0]["source"]["text"])
            > 24000
        ):
            overlap.pop(0)
        characters = sum(len(item["source"]["text"]) for item in overlap)
        selected = []
        first_target = next(
            (index for index, item in enumerate(pending) if item["source"]["role"] == "target"),
            0,
        )
        batch_size = min(64, max(threshold, first_target + 1))
        for item in pending[:batch_size]:
            if characters + len(item["source"]["text"]) > 24000:
                break
            selected.append(item)
            characters += len(item["source"]["text"])
        if not any(item["source"]["role"] == "target" for item in overlap + selected):
            if not data["closed"] and not any(
                item["source"]["role"] == "target" for item in pending[len(selected) :]
            ):
                break
            for item in selected:
                item["reason"] = "context_without_target"
            pending = pending[len(selected) :]
            continue
        window = ObservationWindow(
            version=1,
            window_id=f"incremental-v1-{len(data['windows']) + 1}",
            hosted_processing_approved=hosted_processing_approved,
            groq_processing_approved=groq_processing_approved,
            observations=[
                Observation.model_validate(item["source"]) for item in overlap + selected
            ],
        )
        queued = await enqueue_extraction(
            connection, queue, investigation_id, owner_id, window, incremental=True
        )
        data["windows"].append(str(queued.job_id))
        for item in overlap + selected:
            item["window_ids"].append(str(queued.job_id))
        for item in selected:
            item["job_id"] = str(queued.job_id)
        pending = pending[len(selected) :]
        result.append(queued)
    await _save(connection, investigation_id, data)
    return result


async def extraction_progress(
    connection: AsyncConnection, investigation_id: uuid.UUID
) -> ExtractionProgress | None:
    data = await connection.scalar(
        select(extraction_runs.c.data).where(extraction_runs.c.investigation_id == investigation_id)
    )
    if data is None or not (data["observations"] or data.get("unadmitted")):
        return None
    records = await connection.execute(
        select(jobs.c.id, jobs.c.state, jobs.c.failure).where(
            jobs.c.id.in_([uuid.UUID(value) for value in data["windows"]])
        )
    )
    outcomes = {str(row.id): row for row in records}
    progress = []
    stopped = (
        await connection.scalar(
            select(capture_sessions.c.continue_research).where(
                capture_sessions.c.id == investigation_id
            )
        )
        is False
    )
    for item in data["observations"]:
        source = item["source"]
        job = outcomes.get(item["job_id"])
        processed = any(
            outcomes[identifier].state == "published"
            for identifier in item["window_ids"]
            if identifier in outcomes
        )
        status: Literal["pending", "processed", "skipped", "failed"] = "pending"
        reason = None
        if processed:
            status = "processed"
        elif stopped:
            status, reason = "skipped", "cancelled"
        elif item.get("reason"):
            status, reason = "skipped", item["reason"]
        elif job is None and item["job_id"] is not None:
            status, reason = "skipped", "job_unavailable"
        elif job is not None and job.state in {"failed", "cancelled", "deleted"}:
            status = "failed" if job.state == "failed" else "skipped"
            reason = extraction_failure_code(job.failure) if job.state == "failed" else job.state
            if job.failure == "ExtractionBudgetExceeded":
                status, reason = "skipped", "budget_exhausted"
        progress.append(
            ObservationProgress(
                observation_id=source["id"],
                start_ms=source["start_ms"],
                end_ms=source["end_ms"],
                timebase=source["timebase"],
                status=status,
                reason=reason,
            )
        )
    progress.extend(
        ObservationProgress(
            observation_id=item["id"],
            start_ms=item["start_ms"],
            end_ms=item["end_ms"],
            timebase=item["timebase"],
            status="skipped",
            reason=item["reason"],
        )
        for item in data.get("unadmitted", [])
    )
    progress.sort(key=lambda item: (item.start_ms, item.end_ms, item.observation_id))
    return ExtractionProgress(
        closed=data["closed"] or stopped,
        requests_used=data["requests_used"],
        tokens_reserved=data["tokens_reserved"],
        **{
            key: data["policy"][key]
            for key in (
                "max_requests",
                "max_tokens",
                "reconciliation_requests",
                "reconciliation_tokens",
            )
        },
        observations=progress,
    )


async def record_request(
    connection: AsyncConnection,
    investigation_id: uuid.UUID,
    input_bytes: int,
    output_tokens: int,
    *,
    reconciliation: bool = False,
) -> None:
    data: dict[str, Any] = dict(
        (
            await connection.execute(
                select(extraction_runs.c.data)
                .where(extraction_runs.c.investigation_id == investigation_id)
                .with_for_update()
            )
        ).scalar_one()
    )
    tokens = input_bytes + output_tokens + 256
    policy = ExtractionPolicy.model_validate(data["policy"])
    if data["requests_used"] + 1 > policy.max_requests - (
        0 if reconciliation else policy.reconciliation_requests
    ) or data["tokens_reserved"] + tokens > policy.max_tokens - (
        0 if reconciliation else policy.reconciliation_tokens
    ):
        raise ExtractionBudgetExceeded("Extraction budget exhausted; reconciliation is reserved")
    data["requests_used"] += 1
    data["tokens_reserved"] += tokens
    await _save(connection, investigation_id, data)
