"""DEVELOPMENT ONLY: stub report versions built from the committed contract fixture.

Until the assessment pipeline (#27) produces real report content, ``OVRLY_STUB_REPORTS=1``
lets the worker publish one report version per investigation after its ``intake`` stage, so
the Android app and the demo rehearsal have a report to read, save and act on. The setting
is off by default and nothing here runs unless it is on. Every stub is provisional, its
change summary starts with "Development fixture" and says it is not a check of the
investigated media, and its row is stored with ``fixture`` true, which the version list
reports. With the same opt-in, ``reanalysis`` jobs publish a placeholder fixture version, and
live captures publish fixture versions as their chunks validate and when they close with
``continue_research`` (see the live-capture section below). #27 removes this module.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.schemas import Assessment, Interval, ReportVersion
from services.captures import CAPTURE_STAGE
from services.jobs.handlers import JobContext, JobHandler
from services.jobs.models import jobs
from services.jobs.queue import ClaimedJob
from services.jobs.retries import NonRetriableInput
from services.models import (
    capture_chunks,
    capture_sessions,
    investigations,
    reanalysis_requests,
    report_versions,
)
from services.pipeline.intake import INTAKE_STAGE, intake_stage
from services.reports import REANALYSIS_STAGE, NextVersion, publish_report_version

FIXTURE_PATH: Final = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "contracts"
    / "fixtures"
    / "results"
    / "complete.json"
)
STUB_CHANGE_SUMMARY: Final = (
    "Development fixture, not a check of this media: built from "
    "packages/contracts/fixtures/results/complete.json because OVRLY_STUB_REPORTS is on. "
    "The claims, evidence and assessments are synthetic."
)


class StubReportsDisabled(RuntimeError):
    """Raised when the stub path is reached without the explicit development opt-in."""


def stub_report(identity: NextVersion, fixture_path: Path = FIXTURE_PATH) -> ReportVersion:
    document = json.loads(fixture_path.read_text(encoding="utf-8"))
    template = ReportVersion.model_validate(document["investigation"]["report"])
    assessments = [
        assessment.model_copy(update={"version": identity.version, "provisional": True})
        for assessment in template.assessments
    ]
    return template.model_copy(
        update={
            "id": str(identity.id),
            "investigation_id": identity.investigation_id,
            "version": identity.version,
            "created_at": identity.created_at,
            "provisional": True,
            "change_summary": STUB_CHANGE_SUMMARY,
            "supersedes": identity.supersedes,
            "assessments": assessments,
        }
    )


async def publish_stub_report(
    connection: AsyncConnection, investigation_id: uuid.UUID, *, enabled: bool
) -> ReportVersion | None:
    """Publish the stub once per investigation; ``None`` when a version already exists."""
    if not enabled:
        raise StubReportsDisabled("stub reports require OVRLY_STUB_REPORTS=1")
    await connection.execute(
        select(investigations.c.id).where(investigations.c.id == investigation_id).with_for_update()
    )
    existing = (
        await connection.execute(
            select(report_versions.c.id)
            .where(report_versions.c.investigation_id == investigation_id)
            .limit(1)
        )
    ).first()
    if existing is not None:
        return None
    return await publish_report_version(connection, investigation_id, stub_report, fixture=True)


def enable_stub_reports(handlers: dict[str, JobHandler]) -> None:
    """Install the stub ``intake``, capture and ``reanalysis`` handlers; only when opted in."""
    if INTAKE_STAGE in handlers:
        handlers[INTAKE_STAGE] = intake_with_stub_report
    if CAPTURE_STAGE in handlers:
        handlers[CAPTURE_STAGE] = capture_with_stub_report(handlers[CAPTURE_STAGE])
    handlers[REANALYSIS_STAGE] = stub_reanalysis
    logging.getLogger(__name__).warning("Stub reports enabled; fixture reports will be published")


async def intake_with_stub_report(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
    """The ``intake`` stage followed by the stub publication; registered only when opted in."""
    result = await intake_stage(job, context)
    async with context.queue.database.engine.begin() as connection:
        await publish_stub_report(connection, uuid.UUID(result["investigation_id"]), enabled=True)
    return result


def stub_reanalysed(base: ReportVersion, reason: str, identity: NextVersion) -> ReportVersion:
    """The latest version again, with a placeholder assessment for every unassessed claim."""
    assessed = {item.claim_id for item in base.assessments}
    placeholders = [
        Assessment(
            id=f"asm_stub_{identity.id.hex}_{index}",
            claim_id=claim.id,
            version=identity.version,
            relations=[],
            overall="insufficient_evidence",
            provisional=True,
            summary="Development fixture: no assessment pipeline is available yet (#27).",
        )
        for index, claim in enumerate(base.claims)
        if claim.id not in assessed
    ]
    return base.model_copy(
        update={
            "id": str(identity.id),
            "version": identity.version,
            "created_at": identity.created_at,
            "provisional": True,
            "change_summary": (
                f"Development fixture, not a check of this media: placeholder {reason} "
                f"reanalysis of version {base.version} because OVRLY_STUB_REPORTS is on."
            ),
            "supersedes": identity.supersedes,
            "assessments": [
                *(
                    item.model_copy(update={"version": identity.version})
                    for item in base.assessments
                ),
                *placeholders,
            ],
        }
    )


async def stub_reanalysis(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
    """Development-only ``reanalysis`` handler; publishes one fixture version per request."""
    try:
        request_id = uuid.UUID(str(job.payload["request_id"]))
        investigation_id = uuid.UUID(str(job.payload["investigation_id"]))
        owner_id = uuid.UUID(str(job.payload["owner_id"]))
        reason = str(job.payload["reason"])
    except (KeyError, ValueError):
        raise NonRetriableInput("reanalysis payload is incomplete") from None
    async with context.queue.database.engine.begin() as connection:
        owned = await connection.scalar(
            select(investigations.c.id)
            .where(investigations.c.id == investigation_id, investigations.c.owner_id == owner_id)
            .with_for_update()
        )
        recorded = (
            await connection.execute(
                select(reanalysis_requests.c.result_version)
                .where(reanalysis_requests.c.id == request_id)
                .with_for_update()
            )
        ).first()
        if owned is None or recorded is None:
            raise NonRetriableInput("investigation or request is missing or not owned")
        if recorded.result_version is not None:
            # A re-leased attempt after the version committed: report the same result.
            return {"investigation_id": str(investigation_id), "version": recorded.result_version}
        latest: dict[str, Any] = (
            await connection.execute(
                select(report_versions.c.payload)
                .where(report_versions.c.investigation_id == investigation_id)
                .order_by(report_versions.c.version.desc())
                .limit(1)
            )
        ).scalar_one()
        base = ReportVersion.model_validate(latest)
        report = await publish_report_version(
            connection,
            investigation_id,
            lambda identity: stub_reanalysed(base, reason, identity),
            fixture=True,
        )
        await connection.execute(
            update(reanalysis_requests)
            .where(reanalysis_requests.c.id == request_id)
            .values(result_version=report.version)
        )
    return {"investigation_id": str(investigation_id), "version": report.version}


# ---- Live capture (development only) -------------------------------------------------
# With the opt-in, each validated capture chunk can publish a new fixture version on the
# capture timeline, so the overlay's live polling shows claims arriving: the first chunk
# adds claim 1, the second adds claim 2 and assesses claim 1, the third assesses claim 2,
# and a close with continue_research publishes the final version once every received chunk
# is validated. Content that would not change publishes nothing.

CaptureClaimStatus = Literal["checking", "partial", "complete", "cancelled"]


@dataclass(frozen=True)
class _CaptureStage:
    chunks: list[tuple[int, int]]  # (start_ms, end_ms) of the chunks that host a claim
    assessed: int
    final: bool


def _signature(report: ReportVersion) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    return (
        tuple(claim.id for claim in report.claims),
        tuple(item.claim_id for item in report.assessments),
        not report.provisional,
    )


def capture_report(
    stage: _CaptureStage, identity: NextVersion, fixture_path: Path = FIXTURE_PATH
) -> ReportVersion:
    template = stub_report(identity, fixture_path)
    claims = [
        claim.model_copy(
            update={
                "interval": Interval(start_ms=start, end_ms=end, timebase="capture"),
                "correction": None,
            }
        )
        for claim, (start, end) in zip(template.claims, stage.chunks, strict=False)
    ]
    assessed = {claim.id for claim in claims[: stage.assessed]}
    state = "final" if stage.final else "provisional"
    return template.model_copy(
        update={
            "provisional": not stage.final,
            "change_summary": (
                "Development fixture, not a check of this media: "
                f"{state} live-capture stub with {len(claims)} synthetic claims and "
                f"{len(assessed)} assessed, because OVRLY_STUB_REPORTS is on."
            ),
            "claims": claims,
            "evidence": [item for item in template.evidence if item.claim_id in assessed],
            "assessments": [
                item.model_copy(update={"provisional": not stage.final})
                for item in template.assessments
                if item.claim_id in assessed
            ],
        }
    )


async def _latest(connection: AsyncConnection, capture_id: uuid.UUID) -> Row[Any] | None:
    row: Row[Any] | None = (
        await connection.execute(
            select(report_versions.c.payload, report_versions.c.fixture)
            .where(report_versions.c.investigation_id == capture_id)
            .order_by(report_versions.c.version.desc())
            .limit(1)
        )
    ).first()
    return row


async def sync_capture_stub(
    connection: AsyncConnection,
    capture_id: uuid.UUID,
    *,
    validated_job: uuid.UUID | None = None,
) -> ReportVersion | None:
    """Publish the next fixture version of a live capture when its content would change.

    ``validated_job`` is the chunk job whose handler is running and so counts as validated
    before the worker publishes it. A real (non-fixture) report is never superseded.
    """
    session = (
        await connection.execute(
            select(capture_sessions).where(capture_sessions.c.id == capture_id).with_for_update()
        )
    ).first()
    if session is None:
        return None
    rows = (
        await connection.execute(
            select(
                capture_chunks.c.seq, capture_chunks.c.end_ms, capture_chunks.c.job_id, jobs.c.state
            )
            .select_from(capture_chunks.outerjoin(jobs, jobs.c.id == capture_chunks.c.job_id))
            .where(
                capture_chunks.c.session_id == capture_id,
                capture_chunks.c.received_at.is_not(None),
            )
            .order_by(capture_chunks.c.seq)
        )
    ).all()
    validated = [
        row
        for row in rows
        if row.state == "published" or (row.job_id is not None and row.job_id == validated_job)
    ]
    if not validated:
        return None
    claim_ids = _fixture_claim_ids()
    final = (
        session.state == "closed"
        and session.continue_research is True
        and len(validated) == len(rows)
    )
    hosts = [
        (row.seq * session.chunk_duration_ms, row.end_ms) for row in validated[: len(claim_ids)]
    ]
    assessed = len(hosts) if final else min(len(validated) - 1, len(hosts))
    stage = _CaptureStage(chunks=hosts, assessed=assessed, final=final)
    latest = await _latest(connection, capture_id)
    if latest is not None:
        if not latest.fixture:
            return None
        current = ReportVersion.model_validate(latest.payload)
        expected = (
            tuple(claim_ids[: len(hosts)]),
            tuple(claim_ids[:assessed]),
            final,
        )
        if _signature(current) == expected:
            return None
    return await publish_report_version(
        connection, capture_id, lambda identity: capture_report(stage, identity), fixture=True
    )


def _fixture_claim_ids() -> list[str]:
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [claim["id"] for claim in document["investigation"]["report"]["claims"]]


def capture_with_stub_report(handler: JobHandler) -> JobHandler:
    """Wrap the ``media_validation`` handler: validate first, then sync the fixture report."""

    async def run(job: ClaimedJob, context: JobContext) -> dict[str, Any]:
        result = await handler(job, context)
        async with context.queue.database.engine.begin() as connection:
            capture_id = await connection.scalar(
                select(capture_chunks.c.session_id).where(capture_chunks.c.job_id == job.id)
            )
            if capture_id is not None:
                await sync_capture_stub(connection, capture_id, validated_job=job.id)
        return result

    return run


async def stub_claim_progress(
    connection: AsyncConnection, capture_id: uuid.UUID, continue_research: bool | None
) -> tuple[list[tuple[str, CaptureClaimStatus]], Literal["partial", "complete"]] | None:
    """Per-claim progress of a live capture's fixture report, for the capture status poll."""
    latest = await _latest(connection, capture_id)
    if latest is None or not latest.fixture:
        return None
    report = ReportVersion.model_validate(latest.payload)
    if not report.provisional:
        return [(claim.id, "complete") for claim in report.claims], "complete"
    assessed = {item.claim_id for item in report.assessments}
    progress: list[tuple[str, CaptureClaimStatus]] = []
    for claim in report.claims:
        if claim.id in assessed:
            progress.append((claim.id, "partial"))
        else:
            progress.append((claim.id, "cancelled" if continue_research is False else "checking"))
    return progress, "partial"
