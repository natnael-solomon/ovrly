"""Queue stages ``retrieval`` and ``assessment``, plus the production ``reanalysis`` handler.

Flow: the stage that publishes claims (``claim_extraction``, #25) publishes a report version
with claims and calls :func:`enqueue_retrieval`. ``retrieval`` searches, ranks and fetches
passages for the claims in scope and stores them as its job result, then enqueues
``assessment``, which labels relations, validates citations and publishes the next version.
A failed assessment never redoes retrieval. A version published in between (a user
correction, say) supersedes the run, which then publishes nothing.
"""

import hashlib
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.schemas import ReportVersion
from services.database import Database
from services.evidence.assessment import Assessor, CitationInvalid, validate_citations
from services.evidence.retrieval import Budget, ClaimRetrieval, Retriever
from services.jobs.handlers import JobContext, JobHandler
from services.jobs.models import job_results, jobs
from services.jobs.queue import ClaimedJob, Enqueued, JobQueue, StageKey
from services.jobs.retries import NonRetriableInput, Transient
from services.models import (
    capture_sessions,
    investigations,
    reanalysis_requests,
    report_versions,
)
from services.providers.budget import TokenBucket
from services.providers.crossref import CrossrefClient
from services.providers.fulltext import FullTextClient
from services.providers.http import ProviderRejected
from services.providers.papers import PapersClient
from services.providers.router import RouterClient
from services.reports import REANALYSIS_STAGE, NextVersion, publish_report_version, report_from_row
from services.settings import Settings

RETRIEVAL_STAGE: Final = "retrieval"
ASSESSMENT_STAGE: Final = "assessment"
SCHOLARXIV_BUCKET: Final = "scholarxiv"
Depth = Literal["standard", "deeper"]
logger = logging.getLogger(__name__)


class StagePayload(BaseModel):
    investigation_id: uuid.UUID
    owner_id: uuid.UUID
    base_version: int
    claim_ids: list[str] | None = None
    depth: Depth = "standard"
    reanalysis_request_id: uuid.UUID | None = None
    retrieval_job_id: uuid.UUID | None = None


class RetrievalArtifact(BaseModel):
    investigation_id: uuid.UUID
    base_version: int
    claims: list[ClaimRetrieval]
    superseded: bool = False


def _digest(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def retrieval_key(payload: StagePayload) -> StageKey:
    scope = ",".join(sorted(payload.claim_ids)) if payload.claim_ids is not None else "*"
    return StageKey(
        1,
        RETRIEVAL_STAGE,
        _digest(
            "retrieval",
            payload.investigation_id,
            payload.base_version,
            scope,
            payload.depth,
            payload.reanalysis_request_id or "",
        ),
    )


def assessment_key(retrieval_job_id: uuid.UUID) -> StageKey:
    return StageKey(1, ASSESSMENT_STAGE, _digest("assessment", retrieval_job_id))


async def enqueue_retrieval(
    connection: AsyncConnection,
    queue: JobQueue,
    *,
    investigation_id: uuid.UUID,
    owner_id: uuid.UUID,
    base_version: int,
    claim_ids: list[str] | None = None,
    depth: Depth = "standard",
    reanalysis_request_id: uuid.UUID | None = None,
) -> Enqueued:
    """Queue retrieval for the claims of one published version, inside the caller's
    transaction. ``claim_ids`` ``None`` means every claim of that version."""
    payload = StagePayload(
        investigation_id=investigation_id,
        owner_id=owner_id,
        base_version=base_version,
        claim_ids=claim_ids,
        depth=depth,
        reanalysis_request_id=reanalysis_request_id,
    )
    return await queue.enqueue(
        connection, retrieval_key(payload), payload.model_dump(mode="json"), owner_id=owner_id
    )


def _payload(job: ClaimedJob) -> StagePayload:
    try:
        return StagePayload.model_validate(job.payload)
    except ValidationError:
        raise NonRetriableInput("evidence stage payload is invalid") from None


async def _latest(connection: AsyncConnection, investigation_id: uuid.UUID) -> Row[Any] | None:
    row: Row[Any] | None = (
        await connection.execute(
            select(report_versions.c.version, report_versions.c.payload, report_versions.c.fixture)
            .where(report_versions.c.investigation_id == investigation_id)
            .order_by(report_versions.c.version.desc())
            .limit(1)
        )
    ).first()
    return row


async def _owned(
    connection: AsyncConnection, investigation_id: uuid.UUID, owner_id: uuid.UUID, lock: bool
) -> bool:
    query = select(investigations.c.id).where(
        investigations.c.id == investigation_id, investigations.c.owner_id == owner_id
    )
    if lock:
        query = query.with_for_update()
    return (await connection.execute(query)).first() is not None


async def _capture_open(connection: AsyncConnection, investigation_id: uuid.UUID) -> bool:
    state = await connection.scalar(
        select(capture_sessions.c.state).where(capture_sessions.c.id == investigation_id)
    )
    return state == "open"


@dataclass
class EvidenceStages:
    database: Database
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None

    def handlers(self) -> Mapping[str, JobHandler]:
        return {
            RETRIEVAL_STAGE: self.retrieval,
            ASSESSMENT_STAGE: self.assessment,
            REANALYSIS_STAGE: self.reanalysis,
        }

    def budget(self, depth: Depth) -> Budget:
        settings = self.settings
        budget = Budget(
            max_queries=settings.evidence_max_queries_per_claim,
            results_per_query=settings.evidence_results_per_query,
            max_candidates=settings.evidence_max_candidates_per_claim,
            max_passages=settings.evidence_max_passages_per_claim,
            max_full_text=settings.evidence_max_full_text_per_claim,
            max_llm_calls=settings.evidence_max_llm_calls_per_claim,
        )
        return budget.deeper() if depth == "deeper" else budget

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.evidence_provider_timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        )

    def _clients(self, client: httpx.AsyncClient) -> tuple[RouterClient, PapersClient]:
        key = self.settings.scholarxiv_api_key
        if key is None:
            raise NonRetriableInput("evidence stages need OVRLY_SCHOLARXIV_API_KEY")
        bucket = TokenBucket(
            self.database, SCHOLARXIV_BUCKET, self.settings.scholarxiv_requests_per_hour
        )
        base = self.settings.scholarxiv_base_url
        return (
            RouterClient(client, base, key.get_secret_value(), bucket),
            PapersClient(
                client, base, key.get_secret_value(), bucket, self.settings.scholarxiv_federated
            ),
        )

    async def retrieval(self, job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        payload = _payload(job)
        async with self.database.engine.connect() as connection:
            if not await _owned(connection, payload.investigation_id, payload.owner_id, False):
                raise NonRetriableInput("investigation is missing or not owned by the job owner")
            latest = await _latest(connection, payload.investigation_id)
        if latest is None:
            raise NonRetriableInput("no published report version to retrieve evidence for")
        if latest.version != payload.base_version:
            return RetrievalArtifact(
                investigation_id=payload.investigation_id,
                base_version=payload.base_version,
                claims=[],
                superseded=True,
            ).model_dump(mode="json")
        report = report_from_row(latest.payload, latest.fixture)
        scope = (
            [c for c in report.claims if c.id in set(payload.claim_ids)]
            if payload.claim_ids is not None
            else list(report.claims)
        )
        if not scope:
            # None of the named claims is in the latest version: nothing to assess.
            return RetrievalArtifact(
                investigation_id=payload.investigation_id,
                base_version=payload.base_version,
                claims=[],
            ).model_dump(mode="json")
        budget = self.budget(payload.depth)
        results: list[ClaimRetrieval] = []
        async with self._client() as client:
            router, papers = self._clients(client)
            retriever = Retriever(
                papers=papers,
                router=router,
                crossref=CrossrefClient(client, self.settings.crossref_mailto),
                full_text=FullTextClient(client),
                query_route=self.settings.evidence_query_route,
            )
            for index, claim in enumerate(scope):
                await context.heartbeat()
                if index >= self.settings.evidence_max_claims:
                    results.append(
                        ClaimRetrieval(
                            claim_id=claim.id, status="unassessed", reason="claim_budget"
                        )
                    )
                    continue
                try:
                    results.append(
                        await retriever.retrieve(
                            claim.id, claim.proposition, budget, context.heartbeat
                        )
                    )
                except ProviderRejected:
                    raise NonRetriableInput("Scholarxiv refused the credentials or plan") from None
        if results and all(r.reason == "retrieval_unavailable" for r in results):
            # Nothing could be searched at all: retry the stage rather than publish a hollow
            # version. Partial failures are published with the affected claims unassessed.
            raise Transient("every search failed")
        artifact = RetrievalArtifact(
            investigation_id=payload.investigation_id,
            base_version=payload.base_version,
            claims=results,
        )
        async with self.database.engine.begin() as connection:
            await context.queue.enqueue(
                connection,
                assessment_key(job.id),
                payload.model_copy(update={"retrieval_job_id": job.id}).model_dump(mode="json"),
                owner_id=payload.owner_id,
            )
        return artifact.model_dump(mode="json")

    async def assessment(self, job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        payload = _payload(job)
        if payload.retrieval_job_id is None:
            raise NonRetriableInput("assessment payload names no retrieval job")
        async with self.database.engine.connect() as connection:
            source = (
                await connection.execute(
                    select(jobs.c.state, job_results.c.result)
                    .select_from(jobs.outerjoin(job_results, job_results.c.job_id == jobs.c.id))
                    .where(jobs.c.id == payload.retrieval_job_id)
                )
            ).first()
        if source is None or source.state in {"cancelled", "deleted", "failed"}:
            return {"skipped": "retrieval_unavailable"}
        if source.result is None:
            # Retrieval enqueued this job just before the worker published its result.
            raise Transient("retrieval result not yet published")
        artifact = RetrievalArtifact.model_validate(source.result)
        if artifact.superseded:
            return {"skipped": "superseded"}
        async with self.database.engine.connect() as connection:
            latest = await _latest(connection, payload.investigation_id)
            capture_open = await _capture_open(connection, payload.investigation_id)
        if latest is None or latest.version != payload.base_version:
            return {"skipped": "superseded"}
        base = report_from_row(latest.payload, latest.fixture)
        propositions = {claim.id: claim.proposition for claim in base.claims}
        version = payload.base_version + 1
        outcomes = []
        async with self._client() as client:
            router, _ = self._clients(client)
            assessor = Assessor(
                router,
                self.settings.evidence_relation_route,
                self.budget(payload.depth).max_llm_calls,
            )
            for retrieval in artifact.claims:
                await context.heartbeat()
                if retrieval.claim_id not in propositions:
                    continue
                try:
                    outcomes.append(
                        await assessor.assess(
                            propositions[retrieval.claim_id], retrieval, version, capture_open
                        )
                    )
                except ProviderRejected:
                    raise NonRetriableInput("the router refused the credentials or plan") from None
        # A claim not re-assessed in this run keeps its previous assessment, if it had one.
        scope = {outcome.claim_id for outcome in outcomes if outcome.assessment is not None}
        build = merged_version(base, outcomes, scope, capture_open, payload.depth)
        async with self.database.engine.begin() as connection:
            if not await _owned(connection, payload.investigation_id, payload.owner_id, True):
                return {"skipped": "investigation_missing"}
            current = await _latest(connection, payload.investigation_id)
            if current is None or current.version != payload.base_version:
                return {"skipped": "superseded"}
            try:
                published = await publish_report_version(
                    connection, payload.investigation_id, build, fixture=current.fixture
                )
            except CitationInvalid:
                raise NonRetriableInput("citation validation failed; nothing published") from None
            if payload.reanalysis_request_id is not None:
                await connection.execute(
                    update(reanalysis_requests)
                    .where(reanalysis_requests.c.id == payload.reanalysis_request_id)
                    .values(result_version=published.version)
                )
        assessed = sum(1 for outcome in outcomes if outcome.assessment is not None)
        logger.info(
            "Evidence assessment published version %d (%d of %d claims assessed)",
            published.version,
            assessed,
            len(base.claims),
        )
        return {"version": published.version, "assessed": assessed}

    async def reanalysis(self, job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        try:
            request_id = uuid.UUID(str(job.payload["request_id"]))
            investigation_id = uuid.UUID(str(job.payload["investigation_id"]))
            owner_id = uuid.UUID(str(job.payload["owner_id"]))
            reason = str(job.payload["reason"])
            base_version = int(job.payload["supersedes_version"])
            claim_ids = [str(c) for c in job.payload.get("claim_ids") or []]
            source = job.payload.get("source_investigation_id")
        except (KeyError, ValueError, TypeError):
            raise NonRetriableInput("reanalysis payload is incomplete") from None
        if reason == "expansion":
            if not source:
                raise NonRetriableInput("expansion names no source investigation")
            return await self._expand(
                request_id, investigation_id, owner_id, base_version, uuid.UUID(str(source))
            )
        if reason not in {"correction", "deeper"}:
            raise NonRetriableInput("unknown reanalysis reason")
        async with self.database.engine.begin() as connection:
            if not await _owned(connection, investigation_id, owner_id, True):
                raise NonRetriableInput("investigation is missing or not owned by the job owner")
            enqueued = await enqueue_retrieval(
                connection,
                context.queue,
                investigation_id=investigation_id,
                owner_id=owner_id,
                base_version=base_version,
                claim_ids=claim_ids if reason == "correction" else None,
                depth="deeper" if reason == "deeper" else "standard",
                reanalysis_request_id=request_id,
            )
        return {"retrieval_job_id": str(enqueued.job_id)}

    async def _expand(
        self,
        request_id: uuid.UUID,
        investigation_id: uuid.UUID,
        owner_id: uuid.UUID,
        base_version: int,
        source_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Bring the confirmed full video's latest report into the clip as a new version."""
        async with self.database.engine.begin() as connection:
            if not await _owned(connection, investigation_id, owner_id, True):
                raise NonRetriableInput("investigation is missing or not owned by the job owner")
            if not await _owned(connection, source_id, owner_id, False):
                raise NonRetriableInput("the full-video investigation is missing or not owned")
            current = await _latest(connection, investigation_id)
            if current is None or current.version != base_version:
                return {"skipped": "superseded"}
            source = await _latest(connection, source_id)
            if source is None:
                # The full video has not published a report yet; check again later.
                raise Transient("the full-video investigation has no report yet")
            clip = report_from_row(current.payload, current.fixture)
            full = report_from_row(source.payload, source.fixture)
            try:
                published = await publish_report_version(
                    connection,
                    investigation_id,
                    lambda identity: expanded_version(clip, full, source_id, identity),
                    fixture=bool(current.fixture or source.fixture),
                )
            except CitationInvalid:
                raise NonRetriableInput("citation validation failed; nothing published") from None
            await connection.execute(
                update(reanalysis_requests)
                .where(reanalysis_requests.c.id == request_id)
                .values(result_version=published.version)
            )
        return {"version": published.version}


def merged_version(
    base: ReportVersion,
    outcomes: list[Any],
    scope: set[str],
    capture_open: bool,
    depth: Depth,
) -> Callable[[NextVersion], ReportVersion]:
    """Build the next version: claims in ``scope`` (newly assessed) get the new outcome;
    every other claim keeps its previous assessment and evidence, if any."""

    def build(identity: NextVersion) -> ReportVersion:
        assessments = [
            a.model_copy(update={"version": identity.version, "provisional": capture_open})
            for a in base.assessments
            if a.claim_id not in scope
        ]
        evidence = [e for e in base.evidence if e.claim_id not in scope]
        unassessed: dict[str, int] = {}
        for outcome in outcomes:
            if outcome.assessment is None:
                unassessed[outcome.reason or "unknown"] = (
                    unassessed.get(outcome.reason or "unknown", 0) + 1
                )
                continue
            assessments.append(
                outcome.assessment.model_copy(
                    update={"version": identity.version, "provisional": capture_open}
                )
            )
            evidence.extend(outcome.evidence)
        assessed = {a.claim_id for a in assessments}
        provisional = capture_open or any(c.id not in assessed for c in base.claims)
        done = sum(1 for o in outcomes if o.assessment is not None)
        summary = (
            f"Evidence check{' (deeper search)' if depth == 'deeper' else ''}: "
            f"{done} of {len(outcomes)} claims in scope assessed against Scholarxiv results."
        )
        if unassessed:
            reasons = ", ".join(
                f"{n} {reason.replace('_', ' ')}" for reason, n in sorted(unassessed.items())
            )
            summary += f" Kept visible but unassessed: {reasons}."
        if provisional:
            summary += " The report stays provisional."
        report = base.model_copy(
            update={
                "id": str(identity.id),
                "version": identity.version,
                "created_at": identity.created_at,
                "provisional": provisional,
                "change_summary": summary[:600],
                "supersedes": identity.supersedes,
                "evidence": evidence,
                "assessments": assessments,
            }
        )
        validate_citations(report)
        return report

    return build


def _prefixed(value: str, prefix: str) -> str:
    return f"{prefix}{value}"[:128]


def expanded_version(
    clip: ReportVersion, full: ReportVersion, source_id: uuid.UUID, identity: NextVersion
) -> ReportVersion:
    """The clip's claims plus the full video's, re-identified so no identifier collides."""
    prefix = f"fv{full.version}_"
    claims = [
        claim.model_copy(
            update={
                "id": _prefixed(claim.id, prefix),
                "occurrence_id": _prefixed(claim.occurrence_id, prefix),
            }
        )
        for claim in full.claims
    ]
    evidence = [
        item.model_copy(
            update={"id": _prefixed(item.id, prefix), "claim_id": _prefixed(item.claim_id, prefix)}
        )
        for item in full.evidence
    ]
    assessments = [
        item.model_copy(
            update={
                "id": _prefixed(item.id, prefix),
                "claim_id": _prefixed(item.claim_id, prefix),
                "version": identity.version,
                "relations": [
                    relation.model_copy(
                        update={"evidence_id": _prefixed(relation.evidence_id, prefix)}
                    )
                    for relation in item.relations
                ],
            }
        )
        for item in full.assessments
    ]
    report = clip.model_copy(
        update={
            "id": str(identity.id),
            "version": identity.version,
            "created_at": identity.created_at,
            "provisional": clip.provisional or full.provisional,
            "change_summary": (
                f"Expanded to the confirmed full video: added {len(claims)} claims from "
                f"investigation {source_id} report version {full.version}. The clip's own "
                f"claims are kept; version {clip.version} is unchanged."
            ),
            "supersedes": identity.supersedes,
            "claims": [*clip.claims, *claims],
            "evidence": [*clip.evidence, *evidence],
            "assessments": [
                *(a.model_copy(update={"version": identity.version}) for a in clip.assessments),
                *assessments,
            ],
        }
    )
    validate_citations(report)
    return report
