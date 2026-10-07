"""Settled-input reconciliation through the existing durable job and report seams."""

import hashlib
import uuid
from collections.abc import Awaitable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.errors import extraction_failure_code, safe_error
from services.api.schemas import (
    Claim,
    ClaimCorrection,
    ProcessingAttempt,
    ReconciliationProgress,
    ReconciliationSummary,
    ReportVersion,
)
from services.captures import chunk_jobs
from services.claims import Reconciliation
from services.jobs.faults import Checkpoint
from services.jobs.handlers import CancellationRequested, JobContext, StageResult
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, JobQueue, PublishRejected, StageKey
from services.jobs.retries import NonRetriableInput, ProviderCooldown, RateLimited, UnknownOutcome
from services.models import capture_chunks, capture_sessions, extraction_runs, investigations
from services.pipeline.capture import lock_extraction_capture
from services.pipeline.extraction import Observation
from services.pipeline.incremental import extraction_progress, record_request
from services.pipeline.llm import (
    ExtractionBudgetExceeded,
    ExtractionInvalid,
    ExtractionUnavailable,
    GatewayFailure,
    LlmAdapter,
    parse_typed_completion,
)
from services.pipeline.provider_recovery import invalid_feedback, wait_for_provider
from services.provider_budgets import LlmAdmission
from services.reports import NextVersion, latest_reports, publish_report_version

STAGE = "reconciliation"
T = TypeVar("T")
TERMINAL = {"published", "failed", "cancelled", "deleted"}


async def request_reconciliation(
    connection: AsyncConnection,
    investigation_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    accepted_jobs: list[uuid.UUID],
) -> None:
    """Register the producer's accepted work; closing observations alone never seals upstream."""
    if not await lock_extraction_capture(connection, investigation_id):
        raise NonRetriableInput("Capture was stopped without continued research")
    owned = await connection.scalar(
        select(investigations.c.id)
        .where(investigations.c.id == investigation_id, investigations.c.owner_id == owner_id)
        .with_for_update()
    )
    if owned is None:
        raise NonRetriableInput("Investigation is missing or not owned")
    data = await connection.scalar(
        select(extraction_runs.c.data)
        .where(extraction_runs.c.investigation_id == investigation_id)
        .with_for_update()
    )
    if data is None:
        raise NonRetriableInput("Reconciliation requires admitted source observations")
    accepted = sorted({str(identifier) for identifier in accepted_jobs})
    if len(accepted) > 4096:
        raise NonRetriableInput("Accepted work exceeds the reconciliation bound")
    rows: Sequence[uuid.UUID] = (
        (
            await connection.execute(
                select(jobs.c.id).where(
                    jobs.c.id.in_(accepted_jobs),
                    jobs.c.owner_id == owner_id,
                    (
                        (jobs.c.payload["investigation_id"].astext == str(investigation_id))
                        | (jobs.c.payload["capture_id"].astext == str(investigation_id))
                        | (jobs.c.payload["session_id"].astext == str(investigation_id))
                    ),
                    jobs.c.stage != STAGE,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != len(accepted):
        raise NonRetriableInput("Accepted jobs must exist and belong to the input owner")
    if "reconciliation" in data:
        if data["reconciliation"]["accepted_jobs"] != accepted:
            raise NonRetriableInput("Accepted upstream work is already sealed")
        return
    data["reconciliation"] = {"accepted_jobs": accepted, "job_id": None}
    await connection.execute(
        update(extraction_runs)
        .where(extraction_runs.c.investigation_id == investigation_id)
        .values(data=data)
    )


async def schedule_reconciliation(queue: JobQueue) -> None:
    from services.pipeline.producers import settle_inputs

    await settle_inputs(queue)
    async with queue.database.engine.connect() as connection:
        candidates: Sequence[uuid.UUID] = (
            (
                await connection.execute(
                    select(extraction_runs.c.investigation_id).where(
                        extraction_runs.c.data["reconciliation"].is_not(None),
                        extraction_runs.c.data["reconciliation"]["job_id"].astext.is_(None),
                        extraction_runs.c.data["closed"].astext == "true",
                    )
                )
            )
            .scalars()
            .all()
        )
    for identifier in candidates:
        async with queue.database.engine.begin() as connection:
            if not await lock_extraction_capture(connection, identifier):
                continue
            capture = (
                await connection.execute(
                    select(capture_sessions).where(capture_sessions.c.id == identifier)
                )
            ).first()
            if capture is not None and capture.state != "closed":
                continue
            owner = await connection.scalar(
                select(investigations.c.owner_id)
                .where(investigations.c.id == identifier)
                .with_for_update()
            )
            data = await connection.scalar(
                select(extraction_runs.c.data)
                .where(extraction_runs.c.investigation_id == identifier)
                .with_for_update()
            )
            if data is None or not data["closed"] or data["reconciliation"]["job_id"]:
                continue
            ids = data["reconciliation"]["accepted_jobs"] + data["windows"]
            if capture is not None:
                chunks = (
                    await connection.execute(
                        select(capture_chunks).where(
                            capture_chunks.c.session_id == identifier,
                            capture_chunks.c.received_at.is_not(None),
                        )
                    )
                ).all()
                capture_work: Sequence[uuid.UUID] = (
                    (
                        await connection.execute(
                            select(jobs.c.id).where(
                                chunk_jobs(identifier, [chunk.seq for chunk in chunks])
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                ids += [str(value) for value in capture_work]
                ids += [str(chunk.job_id) for chunk in chunks if chunk.job_id is not None]
            states: Sequence[str] = (
                (
                    await connection.execute(
                        select(jobs.c.state).where(
                            jobs.c.id.in_([uuid.UUID(value) for value in ids])
                        )
                    )
                )
                .scalars()
                .all()
            )
            if any(state not in TERMINAL for state in states):
                continue
            previous = (await latest_reports(connection, [identifier])).get(identifier)
            key = StageKey(1, STAGE, hashlib.sha256(str(identifier).encode()).hexdigest())
            queued = await queue.enqueue(
                connection,
                key,
                {
                    "investigation_id": str(identifier),
                    "incremental": True,
                    "report_id": previous.id if previous else None,
                    "upstream_limited": len(states) < len(set(ids))
                    or any(state != "published" for state in states),
                },
                owner_id=owner,
            )
            data["reconciliation"]["job_id"] = str(queued.job_id)
            await connection.execute(
                update(extraction_runs)
                .where(extraction_runs.c.investigation_id == identifier)
                .values(data=data)
            )


async def reconciliation_progress(
    connection: AsyncConnection,
    identifier: uuid.UUID,
) -> ReconciliationProgress | None:
    data = await connection.scalar(
        select(extraction_runs.c.data).where(extraction_runs.c.investigation_id == identifier)
    )
    if data is None or "reconciliation" not in data:
        return None
    stopped = await connection.scalar(
        select(capture_sessions.c.continue_research).where(capture_sessions.c.id == identifier)
    )
    if stopped is False:
        return ReconciliationProgress(status="cancelled", error=None)
    job_id = data["reconciliation"]["job_id"]
    if job_id is None:
        return ReconciliationProgress(status="waiting", error=None)
    job = (await connection.execute(select(jobs).where(jobs.c.id == uuid.UUID(job_id)))).first()
    if job is None:
        return ReconciliationProgress(status="failed", error=safe_error("PROCESSING_FAILED"))
    status: Literal["waiting", "checking", "complete", "failed", "cancelled"] = (
        "complete"
        if job.state == "published"
        else "failed"
        if job.state == "failed"
        else "cancelled"
        if job.state in {"cancelled", "deleted"} or job.cancel_requested
        else "waiting"
        if job.state == "queued"
        else "checking"
    )
    return ReconciliationProgress(
        status=status,
        error=safe_error(extraction_failure_code(job.failure)) if status == "failed" else None,
    )


class ReconciliationStage:
    def __init__(self, llm: LlmAdapter, admission: LlmAdmission | None = None):
        if llm.task != STAGE or llm.provider != "scholarxiv":
            raise ValueError("Reconciliation requires Scholarxiv quality routing")
        self.llm = llm
        self.admission = admission

    async def _wait(self, pending: Awaitable[T], context: JobContext) -> T:
        try:
            return await wait_for_provider(pending, context)
        except ProviderCooldown as cooldown:
            # A provider 429 limits the whole account: hold its shared bucket too.
            if self.admission is not None:
                await self.admission.block(
                    context.queue.database, self.llm.provider, cooldown.retry_after_seconds
                )
            raise

    async def run(self, job: ClaimedJob, context: JobContext) -> StageResult:
        try:
            identifier = uuid.UUID(job.payload["investigation_id"])
        except (KeyError, TypeError, ValueError):
            raise NonRetriableInput("Invalid reconciliation input") from None
        if job.key != StageKey(1, STAGE, hashlib.sha256(str(identifier).encode()).hexdigest()):
            raise NonRetriableInput("Reconciliation input does not match its stage key")
        await context.heartbeat()
        async with context.queue.database.engine.connect() as connection:
            run: dict[str, Any] | None = (
                await connection.execute(
                    select(extraction_runs.c.data)
                    .join(investigations)
                    .join(jobs, jobs.c.owner_id == investigations.c.owner_id)
                    .where(
                        extraction_runs.c.investigation_id == identifier,
                        jobs.c.id == job.id,
                    )
                )
            ).scalar_one_or_none()
            previous = (await latest_reports(connection, [identifier])).get(identifier)
            progress = await extraction_progress(connection, identifier)
        if (
            run is None
            or not run["closed"]
            or not run["hosted_processing_approved"]
            or run.get("reconciliation", {}).get("job_id") != str(job.id)
        ):
            raise NonRetriableInput("Reconciliation is not registered for this owned input")
        if previous is None or progress is None:
            raise NonRetriableInput("No provisional extraction is available")
        if (
            previous.id != job.payload["report_id"]
            or previous.reconciliation is not None
            or not previous.provisional
        ):
            raise NonRetriableInput("Reconciliation snapshot was superseded")
        source = {
            "observations": [
                Observation.model_validate(item["source"]).model_dump(mode="json")
                for item in run["observations"]
            ],
            "claims": [claim.model_dump(mode="json") for claim in previous.claims],
        }
        data = dict(job.stage_data)

        async def account(provider: str, operation: str, size: int, output: int) -> None:
            # Shared provider units are taken first and may wait; nothing is recorded yet.
            costs = (
                await self.admission.acquire(context.queue.database, provider, size, output)
                if self.admission is not None
                else None
            )
            data.setdefault("requests", []).append(
                {
                    "provider": provider,
                    "operation": operation,
                    "input_bytes": size,
                    "max_output_tokens": output,
                }
            )
            try:
                async with context.queue.database.engine.begin() as connection:
                    if not await lock_extraction_capture(connection, identifier):
                        raise CancellationRequested()
                    await record_request(connection, identifier, size, output, reconciliation=True)
                    # in_flight becomes durable only with an admitted request, just before
                    # the send, so a refused reservation can never leave an unknown outcome.
                    if operation != "feedback":
                        data["in_flight"] = True
                    await context.queue.save_stage_data(job.lease, data, connection=connection)
            except (ExtractionBudgetExceeded, CancellationRequested) as refused:
                if self.admission is not None and costs is not None:
                    await self.admission.refund(context.queue.database, provider, costs)
                if isinstance(refused, CancellationRequested):
                    raise
                data["requests"].pop()
                data.update(in_flight=False, budget_exhausted=True)
                await context.queue.save_stage_data(job.lease, data)
                raise

        if data.get("budget_exhausted"):
            raise ExtractionBudgetExceeded("Reconciliation budget previously exhausted")
        if data.get("in_flight"):
            raise UnknownOutcome("Earlier reconciliation request has no durable outcome")
        if "artifact" not in data:
            if sum(item.get("valid") is False for item in data.get("attempts", [])) >= 2:
                raise ExtractionInvalid("One repair exhausted")
            if data.get("feedback_blocked"):
                raise ExtractionUnavailable("Reconciliation feedback blocked further inference")
            if data.get("availability_failures", 0) > self.llm.recovery_attempts:
                raise ExtractionUnavailable("Quality-route recovery exhausted")
            remaining = data.get("not_before", 0) - datetime.now(UTC).timestamp()
            if remaining > 0:
                raise ProviderCooldown(remaining)
            if data.get("needs_route"):
                try:
                    models = await self._wait(self.llm.fallbacks(source, account=account), context)
                    await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
                except (RateLimited, GatewayFailure):
                    data["in_flight"] = False
                    data["availability_failures"] = self.llm.recovery_attempts + 1
                    await context.queue.save_stage_data(job.lease, data)
                    raise ExtractionUnavailable("Quality routing is unavailable") from None
                data.update(in_flight=False, needs_route=False)
                used = data.get("used_models", [])
                available = [model for model in models if model not in used]
                if not available:
                    data["availability_failures"] = self.llm.recovery_attempts + 1
                    await context.queue.save_stage_data(job.lease, data)
                    raise ExtractionUnavailable("No unused quality route remains")
                data.update(model=available[0], used_models=[*used, available[0]])
                await context.queue.save_stage_data(job.lease, data)
            while sum(item.get("valid") is False for item in data.get("attempts", [])) < 2:
                await context.heartbeat()
                invalid_attempts = [
                    item for item in data.get("attempts", []) if item.get("valid") is False
                ]
                repair = bool(invalid_attempts)
                attempt: dict[str, Any] = {
                    "provider": "scholarxiv",
                    "task": STAGE,
                    "repair": repair,
                }
                try:
                    completion = await self._wait(
                        self.llm.complete(
                            source,
                            repair=repair,
                            model=data.get("model"),
                            account=account,
                            repair_reason=(
                                invalid_attempts[-1].get("reason") if invalid_attempts else None
                            ),
                        ),
                        context,
                    )
                    await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
                    attempt.update(
                        model=completion.model,
                        decision_id=completion.decision_id,
                        usage=completion.usage,
                        thinking_leaked="<think>" in completion.content
                        or "</think>" in completion.content,
                        fenced="```" in completion.content,
                    )
                    output, flags = parse_typed_completion(completion, Reconciliation)
                    attempt.update(flags)
                    claims, invalidated, reassessment = reconcile_claims(output, previous, source)
                except (RateLimited, GatewayFailure) as unavailable:
                    data.setdefault("attempts", []).append(
                        {**attempt, "outcome": unavailable.retry_class.value}
                    )
                    data.update(
                        in_flight=False,
                        availability_failures=data.get("availability_failures", 0) + 1,
                        needs_route=isinstance(unavailable, GatewayFailure),
                    )
                    if isinstance(unavailable, RateLimited):
                        data["not_before"] = datetime.now(UTC).timestamp() + (
                            unavailable.retry_after_seconds or 0
                        )
                    await context.queue.save_stage_data(job.lease, data)
                    if data["availability_failures"] > self.llm.recovery_attempts:
                        raise ExtractionUnavailable("Quality-route recovery exhausted") from None
                    raise
                except ExtractionInvalid as invalid:
                    attempt.update(invalid.provenance, reason=invalid.repair_reason)
                    data.setdefault("attempts", []).append({**attempt, "valid": False})
                    data["in_flight"] = False
                    await context.queue.save_stage_data(job.lease, data)
                    await invalid_feedback(self.llm, data, job, context, account)
                    continue
                data.setdefault("attempts", []).append({**attempt, "valid": True})
                data.update(
                    in_flight=False,
                    artifact=output.model_dump(mode="json"),
                    prompt_version="whole-input-v1",
                    schema_version=1,
                )
                await context.queue.save_stage_data(job.lease, data)
                await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
                break
            else:
                raise ExtractionInvalid("One repair exhausted")
        output = Reconciliation.model_validate(data["artifact"])
        claims, invalidated, reassessment = reconcile_claims(output, previous, source)
        provenance = [
            ProcessingAttempt(
                provider=item["provider"],
                model=item.get("model"),
                decision_id=item.get("decision_id"),
                task=STAGE,
                outcome=item.get("outcome", "valid" if item.get("valid") else "invalid"),
                prompt_tokens=item.get("usage", {}).get("prompt_tokens"),
                completion_tokens=item.get("usage", {}).get("completion_tokens"),
                total_tokens=item.get("usage", {}).get("total_tokens"),
                thinking_leaked=item.get("thinking_leaked", False),
                fenced=item.get("fenced", False),
                repair=item.get("repair", False),
                feedback=item.get("feedback"),
            )
            for item in data.get("attempts", [])
        ]

        async def publish(connection: AsyncConnection) -> None:
            await connection.execute(
                select(investigations.c.id)
                .where(investigations.c.id == identifier)
                .with_for_update()
            )
            latest = (await latest_reports(connection, [identifier])).get(identifier)
            if latest is None or latest.id != previous.id:
                raise PublishRejected(job.id, "reconciliation snapshot was superseded")

            def build(identity: NextVersion) -> ReportVersion:
                return previous.model_copy(
                    update={
                        "id": str(identity.id),
                        "version": identity.version,
                        "created_at": identity.created_at,
                        "supersedes": identity.supersedes,
                        "provisional": True,
                        "change_summary": output.explanation,
                        "claims": claims,
                        "processing_attempts": [*(previous.processing_attempts or []), *provenance],
                        "evidence": [
                            item for item in previous.evidence if item.claim_id not in invalidated
                        ],
                        "assessments": [
                            item.model_copy(update={"version": identity.version})
                            for item in previous.assessments
                            if item.claim_id not in invalidated
                        ],
                        "reconciliation": ReconciliationSummary(
                            status="complete",
                            coverage_limited=job.payload["upstream_limited"]
                            or any(item.status != "processed" for item in progress.observations),
                            reassessment_claim_ids=reassessment,
                        ),
                    }
                )

            await publish_report_version(connection, identifier, build, fixture=previous.fixture)
            await connection.execute(
                update(investigations)
                .where(investigations.c.id == identifier)
                .values(stage=STAGE, state="running", error_code=None, updated_at=func.now())
            )

        return StageResult({"investigation_id": str(identifier)}, publish)


def _precedes(earlier: Claim, later: Claim) -> bool:
    if earlier.interval.end_ms <= later.interval.start_ms:
        return True
    if earlier.interpretation is None or later.interpretation is None:
        return False
    first = earlier.interpretation.source_refs
    second = later.interpretation.source_refs
    return len({ref.observation_id for ref in [*first, *second]}) == 1 and max(
        ref.end_char for ref in first
    ) <= min(ref.start_char for ref in second)


def reconcile_claims(
    output: Reconciliation,
    previous: ReportVersion,
    source: dict[str, Any],
) -> tuple[list[Claim], set[str], list[str]]:
    if len(output.updates) != len(previous.claims) or {
        item.claim_id for item in output.updates
    } != {claim.id for claim in previous.claims}:
        raise ExtractionInvalid("Reconciliation must preserve every occurrence")
    original = {claim.id: claim for claim in previous.claims}
    updates = {item.claim_id: item for item in output.updates}
    superseded = {}
    changed = set()
    for item in output.updates:
        claim = original[item.claim_id]
        if claim.interpretation is None or (
            item.interpretation.source_refs != claim.interpretation.source_refs
        ):
            raise ExtractionInvalid("Reconciliation cannot change an occurrence's source")
        if item.corrects is not None:
            earlier = original.get(item.corrects)
            if (
                earlier is None
                or not _precedes(earlier, claim)
                or earlier.id == claim.id
                or earlier.id in superseded
            ):
                raise ExtractionInvalid("Correction must name one earlier occurrence")
            superseded[earlier.id] = claim.id
        if item.proposition != claim.proposition or item.interpretation != claim.interpretation:
            changed.add(claim.id)
    invalidated = changed | superseded.keys()
    claims = []
    observations = {item["id"]: item for item in source["observations"]}
    for claim in previous.claims:
        item = updates[claim.id]
        if len(item.proposition) > 2000:
            raise ExtractionInvalid("Reconciled proposition exceeds the public limit")
        for refs in (item.interpretation.source_refs, item.interpretation.context_refs):
            seen = set()
            for ref in refs:
                observation = observations.get(ref.observation_id)
                identity = (ref.observation_id, ref.start_char, ref.end_char)
                if (
                    observation is None
                    or ref.end_char > len(observation["text"])
                    or not observation["text"][ref.start_char : ref.end_char].strip()
                    or identity in seen
                ):
                    raise ExtractionInvalid("Invalid reconciliation source reference")
                seen.add(identity)
        claims.append(
            claim.model_copy(
                update={
                    "proposition": item.proposition,
                    "interpretation": item.interpretation,
                    "corrects_occurrence_id": (
                        original[item.corrects].occurrence_id if item.corrects is not None else None
                    ),
                    "superseded_by_occurrence_id": (
                        original[superseded[claim.id]].occurrence_id
                        if claim.id in superseded
                        else None
                    ),
                    "correction": ClaimCorrection(
                        attributed_to="pipeline",
                        corrected_at=datetime.now(UTC),
                        superseded_proposition=claim.proposition,
                    )
                    if claim.id in changed
                    else claim.correction,
                }
            )
        )
    return (
        claims,
        invalidated,
        [claim.id for claim in claims if claim.id in changed and claim.id not in superseded],
    )
