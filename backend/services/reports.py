"""Immutable report versions and explicit saves (BE-10, #33).

A version is written once by ``publish_report_version`` and never edited; the table's trigger
rejects ``UPDATE``. A save stores the caller's snapshot of one version, so a save survives the
expiry of the workspace that produced it once BC-D07 has moved it to an account.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from services.api.auth import Principal, load_owned, lock_active_principal
from services.api.schemas import JobSummary, ReportVersion, SavedReport
from services.models import investigations, report_versions, saved_reports


@dataclass(frozen=True)
class NextVersion:
    """Identity the next version of an investigation's report must carry."""

    id: uuid.UUID
    investigation_id: uuid.UUID
    version: int
    supersedes: str | None
    created_at: datetime


async def publish_report_version(
    connection: AsyncConnection,
    investigation_id: uuid.UUID,
    build: Callable[[NextVersion], ReportVersion],
    *,
    fixture: bool = False,
) -> ReportVersion:
    """Insert the next immutable version, built by ``build`` for the identity it is given.

    The investigation row lock serialises publishers, so versions are 1, 2, 3 without gaps
    and each one names the version it supersedes. ``fixture`` marks a development stub.
    """
    owner_id = (
        await connection.execute(
            select(investigations.c.owner_id)
            .where(investigations.c.id == investigation_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if owner_id is None:
        raise LookupError("investigation is missing")
    latest = (
        await connection.execute(
            select(report_versions.c.id, report_versions.c.version)
            .where(report_versions.c.investigation_id == investigation_id)
            .order_by(report_versions.c.version.desc())
            .limit(1)
        )
    ).first()
    identity = NextVersion(
        id=uuid.uuid4(),
        investigation_id=investigation_id,
        version=1 if latest is None else latest.version + 1,
        supersedes=None if latest is None else str(latest.id),
        created_at=datetime.now(UTC),
    )
    report = build(identity).model_copy(update={"fixture": fixture})
    if (report.id, report.investigation_id, report.version, report.supersedes) != (
        str(identity.id),
        identity.investigation_id,
        identity.version,
        identity.supersedes,
    ):
        raise ValueError("the built report does not carry the identity of the next version")
    await connection.execute(
        insert(report_versions).values(
            id=identity.id,
            investigation_id=investigation_id,
            owner_id=owner_id,
            version=identity.version,
            change_summary=report.change_summary,
            fixture=fixture,
            payload=report.model_dump(mode="json"),
            created_at=report.created_at,
        )
    )
    return report


# Internal handler names that are not contract stages, mapped to the contract ``stage`` the
# work starts at: a reanalysis job begins by retrieving evidence for the affected claims, or
# with media validation when it expands to the full video.
REANALYSIS_STAGE = "reanalysis"


def reanalysis_request_of(stage: str, payload: dict[str, Any] | None) -> uuid.UUID | None:
    """The reanalysis request a job works for: the ``reanalysis`` job itself, or the
    retrieval and assessment it started."""
    value = (payload or {}).get(
        "request_id" if stage == REANALYSIS_STAGE else "reanalysis_request_id"
    )
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def contract_stage(row: Row[Any]) -> str:
    stage: str = row.stage
    if stage != REANALYSIS_STAGE:
        return stage
    payload = row.payload or {}
    return "media_validation" if payload.get("reason") == "expansion" else "retrieval"


def report_from_row(payload: dict[str, Any], fixture: bool) -> ReportVersion:
    """A stored version; the ``fixture`` column wins over payloads stored before the field."""
    return ReportVersion.model_validate({**payload, "fixture": fixture})


def summarize_job(row: Row[Any]) -> JobSummary:
    """Client-visible columns of a ``jobs`` row; leases and fencing stay server-side."""
    return JobSummary(
        id=row.id,
        state=row.state,
        stage=contract_stage(row),
        cancel_requested=row.cancel_requested,
        attempts=row.attempts,
        retry_class=row.retry_class,
        available_at=row.available_at,
        updated_at=row.updated_at,
    )


async def latest_reports(
    connection: AsyncConnection, investigation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ReportVersion]:
    """The latest published version of each investigation that has one."""
    if not investigation_ids:
        return {}
    newest = (
        select(
            report_versions.c.investigation_id,
            func.max(report_versions.c.version).label("version"),
        )
        .where(report_versions.c.investigation_id.in_(investigation_ids))
        .group_by(report_versions.c.investigation_id)
        .subquery()
    )
    rows = await connection.execute(
        select(
            report_versions.c.investigation_id,
            report_versions.c.payload,
            report_versions.c.fixture,
        ).join(
            newest,
            (report_versions.c.investigation_id == newest.c.investigation_id)
            & (report_versions.c.version == newest.c.version),
        )
    )
    return {row.investigation_id: report_from_row(row.payload, row.fixture) for row in rows}


def parse_canonical_uuid(value: str) -> uuid.UUID | None:
    """Report, job and investigation ids are canonical lowercase UUID strings; anything else
    names no object."""
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def saved_report(row: Row[Any]) -> SavedReport:
    return SavedReport(
        report_id=str(row.report_id),
        investigation_id=row.investigation_id,
        version=row.version,
        saved_at=row.saved_at,
        report=ReportVersion.model_validate(row.report),
    )


async def _saved_row(
    connection: AsyncConnection, principal: Principal, report_id: uuid.UUID
) -> Row[Any] | None:
    row: Row[Any] | None = (
        await connection.execute(
            select(saved_reports).where(
                saved_reports.c.owner_id == principal.id,
                saved_reports.c.report_id == report_id,
            )
        )
    ).first()
    return row


async def save_owned_report(
    connection: AsyncConnection, principal: Principal, report_id: uuid.UUID
) -> SavedReport:
    """Save one of the caller's report versions; repeating it returns the original save.

    A report the caller already saved is returned even when the version itself belongs to
    another principal, which happens after a second-device link moved the save. Otherwise
    the version must be the caller's: another owner's report is the same 404 as a missing one.
    A caller merged into an account by a concurrent link is refused with 401, so a save
    cannot land under the merged guest after ``transfer_saved_reports`` ran.
    """
    await lock_active_principal(connection, principal)
    existing = await _saved_row(connection, principal, report_id)
    if existing is not None:
        return saved_report(existing)
    version = await load_owned(connection, report_versions, report_id, principal)
    await connection.execute(
        insert(saved_reports)
        .values(
            owner_id=principal.id,
            report_id=version.id,
            investigation_id=version.investigation_id,
            version=version.version,
            report=version.payload,
            saved_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["owner_id", "report_id"])
    )
    stored = (
        await connection.execute(
            select(saved_reports).where(
                saved_reports.c.owner_id == principal.id,
                saved_reports.c.report_id == report_id,
            )
        )
    ).one()
    return saved_report(stored)
