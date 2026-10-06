"""Reanalysis requests (BE-10, #33): correction, confirmed full-video expansion and deeper search.

A correction publishes a new immutable version at once: the corrected claim keeps its
original wording, records the superseded proposition with user attribution, and loses its
assessment and evidence until the reanalysis job reruns that claim alone; every other claim
keeps its assessment. Expansion (which requires ``match_confirmed`` and names the caller's
own investigation of the confirmed full video, ``source_investigation_id``) and deeper search
change no content yet. Every reason enqueues one owner-scoped ``reanalysis`` job whose payload names
the version it must supersede. The assessment pipeline (#27) registers the production
handler; until then the job stays queued and cancellable, or, with the development-only
``OVRLY_STUB_REPORTS``, a stub handler publishes a fixture version.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import Row, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import Principal, load_owned
from services.api.errors import ApiError
from services.api.schemas import (
    ClaimCorrection,
    CorrectionReanalysis,
    DeeperReanalysis,
    ExpansionReanalysis,
    JobSummary,
    ReanalysisResponse,
    ReportVersion,
)
from services.jobs.models import jobs
from services.jobs.queue import JobQueue, StageKey
from services.models import investigations, reanalysis_requests, report_versions
from services.quotas import charge, lock_owner
from services.reports import (
    REANALYSIS_STAGE,
    NextVersion,
    publish_report_version,
    summarize_job,
)
from services.settings import Settings

REANALYSIS_VERSION: Final = 1

Reanalysis = CorrectionReanalysis | ExpansionReanalysis | DeeperReanalysis


def reanalysis_stage_key(request_id: uuid.UUID) -> StageKey:
    digest = hashlib.sha256(f"reanalysis:{request_id}".encode()).hexdigest()
    return StageKey(REANALYSIS_VERSION, REANALYSIS_STAGE, digest)


def request_hash(investigation_id: uuid.UUID, body: Reanalysis) -> str:
    canonical = json.dumps(
        # exclude_none keeps the hash of requests made before optional fields existed.
        {
            "investigation_id": str(investigation_id),
            **body.model_dump(mode="json", exclude_none=True),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message, action="fix_request")


async def latest_version(connection: AsyncConnection, investigation_id: uuid.UUID) -> Row[Any]:
    row: Row[Any] | None = (
        await connection.execute(
            select(report_versions)
            .where(report_versions.c.investigation_id == investigation_id)
            .order_by(report_versions.c.version.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise ApiError(
            409,
            "REPORT_NOT_AVAILABLE",
            "The check has no published report to reanalyse yet",
            action="retry",
        )
    return row


def corrected_report(
    base: ReportVersion, body: CorrectionReanalysis, identity: NextVersion
) -> ReportVersion:
    claim = next((item for item in base.claims if item.id == body.claim_id), None)
    if claim is None:
        raise _error(422, "CLAIM_NOT_IN_VERSION", "The claim is not part of this report version")
    if claim.proposition == body.proposition:
        raise _error(422, "CORRECTION_UNCHANGED", "The corrected meaning equals the current one")
    corrected = claim.model_copy(
        update={
            "proposition": body.proposition,
            "correction": ClaimCorrection(
                attributed_to="user",
                corrected_at=identity.created_at,
                superseded_proposition=claim.proposition,
            ),
        }
    )
    return base.model_copy(
        update={
            "id": str(identity.id),
            "version": identity.version,
            "created_at": identity.created_at,
            "provisional": True,
            "change_summary": (
                f"Correction: the user corrected the normalized meaning of claim {claim.id}; "
                f"its evidence and assessment will be rerun. Version {base.version} is kept."
            ),
            "supersedes": identity.supersedes,
            "claims": [corrected if item.id == claim.id else item for item in base.claims],
            "evidence": [item for item in base.evidence if item.claim_id != claim.id],
            "assessments": [
                item.model_copy(update={"version": identity.version})
                for item in base.assessments
                if item.claim_id != claim.id
            ],
        }
    )


async def confirmed_source(
    connection: AsyncConnection,
    principal: Principal,
    investigation_id: uuid.UUID,
    body: ExpansionReanalysis,
) -> uuid.UUID:
    """The caller's shared full-video investigation an expansion names, validated.

    Another caller's or a missing investigation is the same 404 as every owned route.
    """
    # Imported here: the investigation routes import the report helpers this module uses.
    from services.api.routes.investigations import investigation_response, job_states

    source_id = body.source_investigation_id
    if source_id is None:
        raise _error(
            422,
            "EXPANSION_SOURCE_REQUIRED",
            "Expansion must name the investigation of the confirmed full video",
        )
    if source_id == investigation_id:
        raise _error(
            422,
            "EXPANSION_SOURCE_SELF",
            "The full video must be a different investigation from the one being expanded",
        )
    source = await load_owned(connection, investigations, source_id, principal)
    if source.source_kind not in {"url", "upload"}:
        raise _error(
            422,
            "EXPANSION_SOURCE_UNSUPPORTED",
            "The full video must be a shared video or upload, not a live capture",
        )
    state = investigation_response(
        source, (await job_states(connection, [source_id])).get(source_id)
    ).state
    if state in {"failed", "cancelled"}:
        raise _error(
            409,
            "EXPANSION_SOURCE_UNAVAILABLE",
            "The investigation of the full video failed or was cancelled",
        )
    return source_id


async def job_summary(connection: AsyncConnection, job_id: uuid.UUID) -> JobSummary:
    return summarize_job((await connection.execute(select(jobs).where(jobs.c.id == job_id))).one())


async def replay(
    connection: AsyncConnection, principal: Principal, key: str, digest: str
) -> dict[str, Any] | None:
    stored = (
        await connection.execute(
            select(reanalysis_requests.c.request_hash, reanalysis_requests.c.response).where(
                reanalysis_requests.c.owner_id == principal.id,
                reanalysis_requests.c.idempotency_key == key,
            )
        )
    ).first()
    if stored is None:
        return None
    if stored.request_hash != digest:
        raise _error(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "The Idempotency-Key was already used with a different request",
        )
    response: dict[str, Any] = stored.response
    return response


async def request_reanalysis(
    connection: AsyncConnection,
    queue: JobQueue,
    principal: Principal,
    investigation_id: uuid.UUID,
    body: Reanalysis,
    key: str,
    config: Settings,
) -> dict[str, Any]:
    """Validate, publish (corrections only), enqueue and record one reanalysis request."""
    digest = request_hash(investigation_id, body)
    await lock_owner(connection, principal, config)
    await load_owned(connection, investigations, investigation_id, principal, for_update=True)
    replayed = await replay(connection, principal, key, digest)
    if replayed is not None:
        return replayed
    if isinstance(body, ExpansionReanalysis) and not body.match_confirmed:
        raise _error(
            422,
            "MATCH_CONFIRMATION_REQUIRED",
            "Expansion to the full video requires the user to confirm the match",
        )
    source_id = (
        await confirmed_source(connection, principal, investigation_id, body)
        if isinstance(body, ExpansionReanalysis)
        else None
    )
    latest = await latest_version(connection, investigation_id)
    if body.base_version != latest.version:
        raise _error(
            409,
            "REPORT_VERSION_STALE",
            f"Reanalysis must start from the latest report version, {latest.version}",
        )
    await charge(connection, principal, config, checks=1, investigation_id=investigation_id)
    published_version = None
    target_version = latest.version
    pending_claims: list[str] = []
    if isinstance(body, CorrectionReanalysis):
        base = ReportVersion.model_validate(latest.payload)
        published = await publish_report_version(
            connection,
            investigation_id,
            lambda identity: corrected_report(base, body, identity),
            fixture=latest.fixture,
        )
        published_version = target_version = published.version
        pending_claims = [body.claim_id]
    request_id = uuid.uuid4()
    enqueued = await queue.enqueue(
        connection,
        reanalysis_stage_key(request_id),
        {
            "request_id": str(request_id),
            "investigation_id": str(investigation_id),
            "owner_id": str(principal.id),
            "reason": body.reason,
            "supersedes_version": target_version,
            "claim_ids": pending_claims,
            # An identifier only; the worker loads the source under the same owner.
            "source_investigation_id": None if source_id is None else str(source_id),
        },
        owner_id=principal.id,
    )
    now = datetime.now(UTC)
    response = ReanalysisResponse(
        id=request_id,
        investigation_id=investigation_id,
        reason=body.reason,
        base_version=body.base_version,
        published_version=published_version,
        source_investigation_id=source_id,
        job=await job_summary(connection, enqueued.job_id),
        created_at=now,
    ).model_dump(mode="json")
    await connection.execute(
        insert(reanalysis_requests).values(
            id=request_id,
            owner_id=principal.id,
            investigation_id=investigation_id,
            idempotency_key=key,
            request_hash=digest,
            reason=body.reason,
            base_version=body.base_version,
            published_version=published_version,
            result_version=None,
            job_id=enqueued.job_id,
            response=response,
            created_at=now,
            source_investigation_id=source_id,
        )
    )
    return response
