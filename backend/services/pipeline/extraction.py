"""One source-grounded window, with durable attempts and fenced report publication."""

import hashlib
import json
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self, TypeVar

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.schemas import Claim, Interval, ProcessingAttempt, ReportVersion
from services.claims import Extraction, Interpretation, StrictModel, Text
from services.jobs.faults import Checkpoint
from services.jobs.handlers import CancellationRequested, JobContext, StageResult
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob, Enqueued, JobQueue, PublishRejected, StageKey
from services.jobs.retries import (
    NonRetriableInput,
    ProviderCooldown,
    RateLimited,
    UnknownOutcome,
)
from services.models import investigations
from services.pipeline.capture import lock_extraction_capture
from services.pipeline.llm import (
    PROMPT_VERSION,
    ExtractionBudgetExceeded,
    ExtractionDenied,
    ExtractionInvalid,
    ExtractionUnavailable,
    GatewayFailure,
    LlmAdapter,
    RequestAccount,
    parse_completion,
)
from services.pipeline.provider_recovery import invalid_feedback, wait_for_provider
from services.reports import NextVersion, latest_reports, publish_report_version

STAGE = "claim_extraction"
T = TypeVar("T")


class Observation(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    role: Literal["target", "context"]
    text: Annotated[str, Field(min_length=1, max_length=8000)]
    modality: Literal["speech", "text"]
    timebase: Literal["capture", "media"]
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(gt=0)]
    speaker_id: Text | None

    @model_validator(mode="after")
    def interval(self) -> Self:
        if self.end_ms <= self.start_ms or not self.text.strip():
            raise ValueError("Observation must have text and an ordered interval")
        return self


class ObservationWindow(StrictModel):
    version: Literal[1]
    window_id: Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
    hosted_processing_approved: bool
    groq_processing_approved: bool = False
    observations: Annotated[list[Observation], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len({item.id for item in self.observations}) != len(self.observations):
            raise ValueError("Observation identifiers must be unique")
        if len({item.timebase for item in self.observations}) != 1:
            raise ValueError("A window must use one original timebase")
        if not any(item.role == "target" for item in self.observations):
            raise ValueError("A window needs target observations")
        if sum(len(item.text) for item in self.observations) > 24000:
            raise ValueError("Window exceeds the bounded input size")
        return self


def extraction_key(
    investigation_id: uuid.UUID, window: ObservationWindow, *, incremental: bool = False
) -> StageKey:
    source = window.model_dump()
    if not window.groq_processing_approved:
        source.pop("groq_processing_approved")
    canonical = json.dumps(
        {
            "investigation_id": str(investigation_id),
            "window": source,
            "prompt_version": PROMPT_VERSION,
            **({"incremental": True} if incremental else {}),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return StageKey(1, STAGE, hashlib.sha256(canonical.encode()).hexdigest())


async def enqueue_extraction(
    connection: AsyncConnection,
    queue: JobQueue,
    investigation_id: uuid.UUID,
    owner_id: uuid.UUID,
    window: ObservationWindow,
    *,
    incremental: bool = False,
) -> Enqueued:
    owned = await connection.scalar(
        select(investigations.c.id)
        .where(
            investigations.c.id == investigation_id,
            investigations.c.owner_id == owner_id,
        )
        .with_for_update()
    )
    if owned is None:
        raise NonRetriableInput("Investigation is missing or not owned")
    return await queue.enqueue(
        connection,
        extraction_key(investigation_id, window, incremental=incremental),
        {
            "investigation_id": str(investigation_id),
            "window": window.model_dump(mode="json"),
            **({"incremental": True} if incremental else {}),
        },
        owner_id=owner_id,
    )


def grounded_claims(
    extraction: Extraction,
    window: ObservationWindow,
    investigation_id: uuid.UUID,
) -> list[Claim]:
    if len(extraction.occurrences) > 64:
        raise ExtractionInvalid("Too many occurrences")
    observations = {item.id: item for item in window.observations}
    claims: list[Claim] = []
    seen: set[str] = set()
    for index, occurrence in enumerate(extraction.occurrences):
        # A target cited as clarifying context is already analyzed as a target in this window.
        occurrence = occurrence.model_copy(
            update={
                "context_refs": [
                    ref
                    for ref in occurrence.context_refs
                    if ref.observation_id not in observations
                    or observations[ref.observation_id].role == "context"
                ]
            }
        )
        for refs, role, field in (
            (occurrence.source_refs, "target", "source_refs"),
            (occurrence.context_refs, "context", "context_refs"),
        ):
            identities: set[tuple[str, int, int]] = set()
            for ref in refs:
                item = observations.get(ref.observation_id)
                identity = (ref.observation_id, ref.start_char, ref.end_char)
                if (
                    item is None
                    or item.role != role
                    or ref.end_char > len(item.text)
                    or not item.text[ref.start_char : ref.end_char].strip()
                    or identity in identities
                ):
                    raise ExtractionInvalid(
                        "Invalid source reference",
                        repair_reason=f"occurrences.{index}.{field}: cite {role} observations once",
                    )
                identities.add(identity)
        sources = [observations[ref.observation_id] for ref in occurrence.source_refs]
        if len({item.speaker_id for item in sources}) != 1:
            raise ExtractionInvalid(
                "An occurrence cannot silently combine different speakers",
                repair_reason=f"occurrences.{index}.source_refs: use one speaker",
            )
        original = "\n".join(
            observations[ref.observation_id].text[ref.start_char : ref.end_char]
            for ref in occurrence.source_refs
        )
        if len(original) > 2000 or len(occurrence.proposition) > 2000:
            raise ExtractionInvalid("Occurrence exceeds the public claim text limit")
        provenance = sorted(
            (ref.observation_id, ref.start_char, ref.end_char) for ref in occurrence.source_refs
        )
        identifier = str(uuid.uuid5(investigation_id, json.dumps(provenance)))
        if identifier in seen:
            raise ExtractionInvalid(
                "Duplicate occurrence", repair_reason=f"occurrences.{index}: duplicate source span"
            )
        seen.add(identifier)
        claims.append(
            Claim(
                id=identifier,
                occurrence_id=identifier,
                interval=Interval(
                    start_ms=min(item.start_ms for item in sources),
                    end_ms=max(item.end_ms for item in sources),
                    timebase=sources[0].timebase,
                ),
                modality=sources[0].modality
                if len({item.modality for item in sources}) == 1
                else "both",
                original_text=original,
                proposition=occurrence.proposition,
                correction=None,
                interpretation=Interpretation.model_validate(
                    occurrence.model_dump(exclude={"proposition"})
                ),
            )
        )
    return claims


class ExtractionStage:
    def __init__(self, llm: LlmAdapter, groq: LlmAdapter | None = None):
        if (
            llm.task != STAGE
            or llm.provider != "scholarxiv"
            or (groq is not None and (groq.task != STAGE or groq.provider != "groq"))
        ):
            raise ValueError("Extraction route policy does not permit this task or provider")
        self.llm = llm
        self.groq = groq

    def _use_groq(self, data: dict[str, Any], window: ObservationWindow) -> LlmAdapter:
        if (
            self.groq is None
            or not window.groq_processing_approved
            or data.get("provider") == "groq"
            or any(item.get("valid") is False for item in data.get("attempts", []))
        ):
            raise ExtractionUnavailable("No authorized availability fallback remains")
        data.update(provider="groq", model=None, needs_route=False, availability_failures=0)
        return self.groq

    async def _wait(self, pending: Awaitable[T], context: JobContext) -> T:
        return await wait_for_provider(pending, context)

    async def _feedback(
        self,
        active: LlmAdapter,
        data: dict[str, Any],
        job: ClaimedJob,
        context: JobContext,
        account: RequestAccount,
    ) -> None:
        await invalid_feedback(active, data, job, context, account)

    async def run(self, job: ClaimedJob, context: JobContext) -> StageResult:
        try:
            investigation_id = uuid.UUID(job.payload["investigation_id"])
            window = ObservationWindow.model_validate(job.payload["window"])
        except (KeyError, ValueError, TypeError, ValidationError):
            raise NonRetriableInput("Invalid observation window") from None
        if job.key != extraction_key(
            investigation_id, window, incremental=bool(job.payload.get("incremental"))
        ):
            raise NonRetriableInput("Window does not match its stage key")
        if not window.hosted_processing_approved:
            raise NonRetriableInput("Hosted processing has not been authorized for this window")
        await context.heartbeat()
        async with context.queue.database.engine.connect() as connection:
            owned = await connection.scalar(
                select(investigations.c.id)
                .join(
                    jobs,
                    jobs.c.owner_id == investigations.c.owner_id,
                )
                .where(investigations.c.id == investigation_id, jobs.c.id == job.id)
            )
        if owned is None:
            raise NonRetriableInput("Investigation is missing or not owned by this job")
        if job.payload.get("incremental"):
            async with context.queue.database.engine.connect() as connection:
                previous = (await latest_reports(connection, [investigation_id])).get(
                    investigation_id
                )
            if previous is not None and (
                not previous.provisional or previous.reconciliation is not None
            ):
                raise NonRetriableInput("Incremental extraction cannot reopen a finalized report")
        data = dict(job.stage_data)

        async def account(
            provider: str, operation: str, input_bytes: int, output_tokens: int
        ) -> None:
            data.setdefault("requests", []).append(
                {
                    "provider": provider,
                    "operation": operation,
                    "input_bytes": input_bytes,
                    "max_output_tokens": output_tokens,
                }
            )
            try:
                async with context.queue.database.engine.begin() as connection:
                    if job.payload.get("incremental") and not await lock_extraction_capture(
                        connection, investigation_id
                    ):
                        raise CancellationRequested()
                    if job.payload.get("incremental"):
                        from services.pipeline.incremental import record_request

                        await record_request(
                            connection, investigation_id, input_bytes, output_tokens
                        )
                    # in_flight becomes durable only with an admitted request, just before
                    # the send, so a refused reservation can never leave an unknown outcome.
                    if operation != "feedback":
                        data["in_flight"] = True
                    await context.queue.save_stage_data(job.lease, data, connection=connection)
            except ExtractionBudgetExceeded:
                data["requests"].pop()
                data.update(in_flight=False, budget_exhausted=True)
                await context.queue.save_stage_data(job.lease, data)
                raise

        if data.get("budget_exhausted"):
            raise ExtractionBudgetExceeded("Extraction budget previously exhausted")
        if data.get("in_flight"):
            raise UnknownOutcome("Earlier provider attempt has no durable outcome")
        if "artifact" not in data:
            attempts = data.get("attempts", [])
            if sum(item.get("valid") is False for item in attempts) >= 2:
                raise ExtractionInvalid("One repair exhausted")
            if data.get("feedback_blocked") == "ExtractionDenied":
                raise ExtractionDenied(
                    "Feedback authorization denied; no further inference permitted"
                )
            if data.get("feedback_blocked"):
                raise ExtractionUnavailable(
                    "Feedback cooldown invalid; no further inference permitted"
                )
            active = self.groq if data.get("provider") == "groq" else self.llm
            if active is None or (
                active.provider == "groq" and not window.groq_processing_approved
            ):
                raise ExtractionUnavailable("Previously selected provider is not authorized")
            if data.get("availability_failures", 0) > active.recovery_attempts:
                self._use_groq(data, window)
                data.pop("not_before", None)
                await context.queue.save_stage_data(job.lease, data)
            remaining = data.get("not_before", 0) - datetime.now(UTC).timestamp()
            if remaining > 0:
                raise ProviderCooldown(remaining)
            if data.get("needs_route"):
                if "fallbacks" not in data:
                    try:
                        data["fallbacks"] = await self._wait(
                            self.llm.fallbacks(window.model_dump(mode="json"), account=account),
                            context,
                        )
                        await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
                    except (RateLimited, GatewayFailure) as unavailable:
                        data["fallbacks"] = []
                        data["routing_failure"] = unavailable.retry_class.value
                    data["in_flight"] = False
                    await context.queue.save_stage_data(job.lease, data)
                if not data["fallbacks"]:
                    self._use_groq(data, window)
                else:
                    data["model"] = data["fallbacks"].pop(0)
                data["needs_route"] = False
                await context.queue.save_stage_data(job.lease, data)
            active = self.groq if data.get("provider") == "groq" else self.llm
            if active is None or (
                active.provider == "groq" and not window.groq_processing_approved
            ):
                raise ExtractionUnavailable("Previously selected provider is not authorized")
            while sum(item.get("valid") is False for item in attempts) < 2:
                await context.heartbeat()
                data = {**data, "attempts": attempts}
                repair = any(item.get("valid") is False for item in attempts)
                reasons = [item["reason"] for item in attempts if item.get("reason")]
                attempt: dict[str, Any] = {"repair": repair, "provider": active.provider}
                try:
                    completion = await self._wait(
                        active.complete(
                            window.model_dump(mode="json"),
                            repair=repair,
                            model=data.get("model"),
                            account=account,
                            repair_reason=reasons[-1] if repair and reasons else None,
                        ),
                        context,
                    )
                    await context.checkpoint(Checkpoint.AFTER_PROVIDER_CALL, job)
                    attempt.update(
                        model=completion.model,
                        decision_id=completion.decision_id,
                        usage=completion.usage,
                        task=STAGE,
                        thinking_leaked="<think>" in completion.content
                        or "</think>" in completion.content,
                        fenced="```" in completion.content,
                    )
                    output, flags = parse_completion(
                        completion, {item.id: item.text for item in window.observations}
                    )
                    attempt.update(flags)
                    claims = grounded_claims(output, window, investigation_id)
                except (RateLimited, GatewayFailure) as unavailable:
                    attempts = [
                        *attempts,
                        {**attempt, "outcome": unavailable.retry_class.value},
                    ]
                    data.update(
                        attempts=attempts,
                        in_flight=False,
                        needs_route=isinstance(unavailable, GatewayFailure)
                        and active.provider == "scholarxiv",
                        availability_failures=data.get("availability_failures", 0) + 1,
                    )
                    if isinstance(unavailable, RateLimited):
                        data["not_before"] = datetime.now(UTC).timestamp() + (
                            unavailable.retry_after_seconds or 0
                        )
                    await context.queue.save_stage_data(job.lease, data)
                    if data["availability_failures"] > active.recovery_attempts:
                        active = self._use_groq(data, window)
                        data.pop("not_before", None)
                        await context.queue.save_stage_data(job.lease, data)
                        continue
                    raise
                except ExtractionInvalid as invalid:
                    attempt.update(invalid.provenance, reason=invalid.repair_reason)
                    attempts = [*attempts, {**attempt, "valid": False}]
                    data = {**data, "attempts": attempts, "in_flight": False}
                    await context.queue.save_stage_data(job.lease, data)
                    await self._feedback(active, data, job, context, account)
                    continue
                attempts = [*attempts, {**attempt, "valid": True}]
                data = {
                    **data,
                    "attempts": attempts,
                    "in_flight": False,
                    "artifact": [claim.model_dump(mode="json") for claim in claims],
                    "prompt_version": PROMPT_VERSION,
                    "schema_version": 1,
                }
                await context.queue.save_stage_data(job.lease, data)
                await context.checkpoint(Checkpoint.AFTER_ARTIFACT_STORE, job)
                break
            else:
                raise ExtractionInvalid("One repair exhausted")
        claims = [Claim.model_validate(item) for item in data["artifact"]]
        provenance = [
            ProcessingAttempt(
                provider=item.get("provider", "scholarxiv"),
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
            published_claims = claims
            published_provenance = provenance
            previous = None
            if job.payload.get("incremental"):
                await connection.execute(
                    select(investigations.c.id)
                    .where(investigations.c.id == investigation_id)
                    .with_for_update()
                )
                previous = (await latest_reports(connection, [investigation_id])).get(
                    investigation_id
                )
                if previous is not None:
                    if not previous.provisional or previous.reconciliation is not None:
                        raise PublishRejected(job.id, "incremental report was finalized")
                    known = {claim.occurrence_id for claim in previous.claims}
                    published_claims = previous.claims + [
                        claim for claim in claims if claim.occurrence_id not in known
                    ]
                    published_provenance = (previous.processing_attempts or []) + provenance
                published_claims = sorted(
                    published_claims,
                    key=lambda claim: (claim.interval.start_ms, claim.interval.end_ms, claim.id),
                )

            def build(identity: NextVersion) -> ReportVersion:
                return ReportVersion(
                    id=str(identity.id),
                    investigation_id=investigation_id,
                    version=identity.version,
                    created_at=identity.created_at,
                    provisional=True,
                    supersedes=identity.supersedes,
                    change_summary=(
                        "Cumulative provisional extraction; newly added claims are not assessed "
                        "or reconciled. Intervals are source observation envelopes."
                        if job.payload.get("incremental")
                        else "Provisional claim extraction from one observation window; "
                        "not assessed or reconciled. Intervals are source observation envelopes."
                    ),
                    claims=published_claims,
                    evidence=previous.evidence if previous is not None else [],
                    assessments=[
                        item.model_copy(update={"version": identity.version})
                        for item in previous.assessments
                    ]
                    if previous is not None
                    else [],
                    processing_attempts=published_provenance,
                )

            await publish_report_version(
                connection,
                investigation_id,
                build,
                fixture=previous.fixture if previous is not None else False,
            )
            await connection.execute(
                update(investigations)
                .where(investigations.c.id == investigation_id)
                .values(stage=STAGE, state="running", error_code=None, updated_at=func.now())
            )

        return StageResult({"investigation_id": str(investigation_id), **data}, publish)
