"""Build the result fixtures from the backend Pydantic read models and diff them.

The six scenarios under ``fixtures/results`` are produced by this script from
``backend/services/api/schemas.py`` with the production serialiser
(``model_dump(mode="json")``, the same call the routes use), so the committed JSON is
exactly what the server would emit for those models. Run from the repository root with
the backend uv project so ``services`` and Pydantic are importable:

    uv run --project backend --frozen python packages/contracts/roundtrip.py --check
    uv run --project backend --frozen python packages/contracts/roundtrip.py --update

``--check`` exits nonzero and prints a diff when a committed fixture differs from the
model output (CI mode). ``--update`` rewrites the fixtures; review the diff and run
``validate.py`` afterwards. ``backend/tests/test_contract_roundtrip.py`` runs the check.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parents[1] / "backend"
RESULTS = ROOT / "fixtures" / "results"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.api.schemas import (  # noqa: E402
    Assessment,
    Claim,
    ClaimCorrection,
    Coverage,
    Evidence,
    EvidenceRelation,
    EvidenceSource,
    Interval,
    InvestigationReadModel,
    InvestigationState,
    JobSummary,
    ProcessingStatus,
    ReportVersion,
    RetractionStatus,
    RetrievalRelevance,
    SafeError,
    SourceInspectionLevel,
    SourceType,
    Timebase,
)
from services.jobs.retries import RetryClass  # noqa: E402
from services.jobs.states import JobState  # noqa: E402

# Backend row keys are UUIDs, which cannot spell "synthetic"; the fixtures draw them from
# this reserved prefix (validate.SYNTHETIC_UUID_PREFIX) so they are still obviously fake.
SYNTHETIC_UUID_PREFIX = "00000000-0000-4000-8000-"
T0 = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


def _uuid(number: int) -> uuid.UUID:
    return uuid.UUID(f"{SYNTHETIC_UUID_PREFIX}{number:012d}")


def _time(offset_seconds: int) -> datetime:
    return datetime.fromtimestamp(T0.timestamp() + offset_seconds, tz=UTC)


def _url_source(duration_ms: int | None = None) -> dict[str, Any]:
    """Shape of ``routes.investigations._source`` for a URL row."""
    source: dict[str, Any] = {"kind": "url", "url": "https://video.example/synthetic/clip-0001"}
    if duration_ms is not None:
        source["duration_ms"] = duration_ms
    return source


def _upload_source(number: int, duration_ms: int | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"kind": "upload", "upload_id": str(_uuid(number))}
    if duration_ms is not None:
        source["duration_ms"] = duration_ms
    return source


def _job(
    number: int,
    state: JobState,
    stage: str,
    *,
    attempts: int = 1,
    cancel_requested: bool = False,
    retry_class: RetryClass | None = None,
    offset_seconds: int = 60,
) -> JobSummary:
    return JobSummary(
        id=_uuid(number),
        state=state,
        stage=stage,
        cancel_requested=cancel_requested,
        attempts=attempts,
        retry_class=retry_class,
        available_at=_time(0),
        updated_at=_time(offset_seconds),
    )


def _speech_claim(
    number: int, start_ms: int, end_ms: int, timebase: Timebase, *, corrected: bool = False
) -> Claim:
    original = "a recent survey found that nearly two thirds of city buses are electric now"
    proposition = "Nearly two thirds of the city's buses are electric, per a recent survey."
    correction = None
    if corrected:
        correction = ClaimCorrection(
            attributed_to="user",
            corrected_at=_time(600),
            superseded_proposition="Two thirds of the country's buses are electric.",
        )
    return Claim(
        id=f"clm_synthetic_{number:04d}",
        occurrence_id=f"occ_synthetic_{number:04d}",
        interval=Interval(start_ms=start_ms, end_ms=end_ms, timebase=timebase),
        modality="speech",
        original_text=original,
        proposition=proposition,
        correction=correction,
    )


def _text_claim(number: int, start_ms: int, end_ms: int, timebase: Timebase) -> Claim:
    return Claim(
        id=f"clm_synthetic_{number:04d}",
        occurrence_id=f"occ_synthetic_{number:04d}",
        interval=Interval(start_ms=start_ms, end_ms=end_ms, timebase=timebase),
        modality="text",
        original_text="BUS FLEET: 0 DIESEL BY 2024",
        proposition="The bus fleet had no diesel vehicles by 2024.",
        correction=None,
    )


def _evidence(
    number: int,
    claim_id: str,
    *,
    source_type: SourceType,
    inspection_level: SourceInspectionLevel,
    relevance: RetrievalRelevance,
    retraction_status: RetractionStatus = "none",
    excerpt: str | None,
    published_offset_days: int | None = 30,
) -> Evidence:
    published_at = None if published_offset_days is None else _time(-86400 * published_offset_days)
    return Evidence(
        id=f"evd_synthetic_{number:04d}",
        claim_id=claim_id,
        source=EvidenceSource(
            id=f"src_synthetic_{number:04d}",
            title=f"Synthetic source {number}",
            publisher="Synthetic Publisher",
            url=f"https://sources.example/synthetic/{number:04d}",
            published_at=published_at,
        ),
        source_type=source_type,
        inspection_level=inspection_level,
        retrieval_relevance=relevance,
        retraction_status=retraction_status,
        excerpt=excerpt,
        retrieved_at=_time(120),
    )


def _investigation(
    number: int,
    *,
    state: InvestigationState,
    stage: str,
    processing_status: ProcessingStatus,
    coverage: Coverage,
    version: int,
    error: SafeError | None,
    source: dict[str, Any],
    job: JobSummary | None,
    report: ReportVersion | None,
    updated_offset_seconds: int,
) -> InvestigationReadModel:
    return InvestigationReadModel(
        id=_uuid(number),
        state=state,
        stage=stage,
        coverage=coverage.model_dump(mode="json"),
        version=version,
        error=error,
        source=source,
        created_at=_time(0),
        updated_at=_time(updated_offset_seconds),
        processing_status=processing_status,
        job=job,
        report=report,
    )


# Scenarios ---------------------------------------------------------------


def complete() -> InvestigationReadModel:
    """Shared video fully checked; version 2 after a user correction of one claim."""
    investigation_id = _uuid(1)
    claim_a = _speech_claim(1, 12_400, 18_900, "media", corrected=True)
    claim_b = _text_claim(2, 41_000, 46_000, "media")
    evidence = [
        _evidence(
            1,
            claim_a.id,
            source_type="government",
            inspection_level="full_text",
            relevance="high",
            excerpt="64 percent of the municipal bus fleet was battery-electric at end of 2025.",
        ),
        _evidence(
            2,
            claim_a.id,
            source_type="news",
            inspection_level="full_text",
            relevance="medium",
            excerpt="The city says about two thirds of its buses are now electric.",
        ),
        _evidence(
            3,
            claim_b.id,
            source_type="primary_document",
            inspection_level="full_text",
            relevance="high",
            excerpt="Twelve diesel buses remained in reserve service through 2024.",
        ),
    ]
    assessments = [
        Assessment(
            id="asm_synthetic_0001",
            claim_id=claim_a.id,
            version=2,
            relations=[
                EvidenceRelation(
                    evidence_id=evidence[0].id,
                    relation="support",
                    note="The fleet report gives 64 percent battery-electric at end of 2025.",
                ),
                EvidenceRelation(evidence_id=evidence[1].id, relation="support", note=None),
            ],
            overall="supported",
            provisional=False,
            summary="Two independent sources put the electric share of the fleet near two thirds.",
        ),
        Assessment(
            id="asm_synthetic_0002",
            claim_id=claim_b.id,
            version=2,
            relations=[
                EvidenceRelation(
                    evidence_id=evidence[2].id,
                    relation="qualify",
                    note="Diesel buses stayed in reserve service; the fleet was not diesel-free.",
                )
            ],
            overall="qualified",
            provisional=False,
            summary="The on-screen claim holds for scheduled service only; reserve diesel stayed.",
        ),
    ]
    report = ReportVersion(
        id="rpt_synthetic_0001_v2",
        investigation_id=investigation_id,
        version=2,
        created_at=_time(660),
        provisional=False,
        change_summary=(
            "Correction: the user corrected the normalized meaning of clm_synthetic_0001 from "
            "the country's fleet to the city's fleet; its assessment was rerun. Version 1 is kept."
        ),
        supersedes="rpt_synthetic_0001_v1",
        claims=[claim_a, claim_b],
        evidence=evidence,
        assessments=assessments,
    )
    return _investigation(
        1,
        state="completed",
        stage="publication",
        processing_status="complete",
        coverage=Coverage(status="complete", covered_ms=185_000, total_ms=185_000),
        version=2,
        error=None,
        source=_url_source(185_000),
        job=_job(101, JobState.PUBLISHED, "publication", attempts=1, offset_seconds=660),
        report=report,
        updated_offset_seconds=660,
    )


def partial() -> InvestigationReadModel:
    """Live capture still running: one claim assessed, a second found but not yet assessed."""
    investigation_id = _uuid(2)
    claim_a = _speech_claim(3, 4_200, 9_800, "capture")
    claim_b = _text_claim(4, 31_000, 36_000, "capture")
    evidence = [
        _evidence(
            4,
            claim_a.id,
            source_type="government",
            inspection_level="full_text",
            relevance="high",
            excerpt="41 percent of the municipal bus fleet was battery-electric at end of 2025.",
        )
    ]
    assessments = [
        Assessment(
            id="asm_synthetic_0003",
            claim_id=claim_a.id,
            version=1,
            relations=[
                EvidenceRelation(
                    evidence_id=evidence[0].id,
                    relation="challenge",
                    note="The fleet report gives 41 percent, well short of two thirds.",
                )
            ],
            overall="challenged",
            provisional=True,
            summary="The official fleet report gives a much lower electric share; more may follow.",
        )
    ]
    report = ReportVersion(
        id="rpt_synthetic_0002_v1",
        investigation_id=investigation_id,
        version=1,
        created_at=_time(45),
        provisional=True,
        change_summary="First partial results while the capture continues.",
        supersedes=None,
        claims=[claim_a, claim_b],
        evidence=evidence,
        assessments=assessments,
    )
    return _investigation(
        2,
        state="running",
        stage="retrieval",
        processing_status="partial",
        coverage=Coverage(status="partial", covered_ms=60_000, total_ms=None),
        version=1,
        error=None,
        source=_upload_source(202),
        job=_job(
            102,
            JobState.RUNNING,
            "retrieval",
            attempts=2,
            retry_class=RetryClass.TRANSIENT,
            offset_seconds=50,
        ),
        report=report,
        updated_offset_seconds=50,
    )


def failed() -> InvestigationReadModel:
    """Media validation rejected the input: an error, no job retry and no findings."""
    return _investigation(
        3,
        state="failed",
        stage="media_validation",
        processing_status="failed",
        coverage=Coverage(status="not_started"),
        version=1,
        error=SafeError(code="MEDIA_UNSUPPORTED", message="Processing failed", retryable=False),
        source=_url_source(),
        job=_job(
            103,
            JobState.FAILED,
            "media_validation",
            attempts=1,
            retry_class=RetryClass.NON_RETRIABLE_INPUT,
            offset_seconds=8,
        ),
        report=None,
        updated_offset_seconds=8,
    )


def cancelled() -> InvestigationReadModel:
    """The owner cancelled during transcription before anything was published."""
    return _investigation(
        4,
        state="cancelled",
        stage="asr",
        processing_status="cancelled",
        coverage=Coverage(status="partial", covered_ms=30_000, total_ms=None),
        version=1,
        error=None,
        source=_upload_source(204),
        job=_job(
            104, JobState.CANCELLED, "asr", attempts=1, cancel_requested=True, offset_seconds=31
        ),
        report=None,
        updated_offset_seconds=31,
    )


def insufficient_evidence() -> InvestigationReadModel:
    """Complete, one claim, two weak sources (one retracted): a finding about the evidence."""
    investigation_id = _uuid(5)
    claim = Claim(
        id="clm_synthetic_0005",
        occurrence_id="occ_synthetic_0005",
        interval=Interval(start_ms=2_000, end_ms=7_500, timebase="media"),
        modality="both",
        original_text="this one supplement doubles your reaction speed in a week",
        proposition="Taking this supplement for one week doubles reaction speed.",
        correction=None,
    )
    evidence = [
        _evidence(
            5,
            claim.id,
            source_type="peer_reviewed",
            inspection_level="abstract_only",
            relevance="medium",
            retraction_status="retracted",
            excerpt=None,
            published_offset_days=900,
        ),
        _evidence(
            6,
            claim.id,
            source_type="news",
            inspection_level="metadata_only",
            relevance="low",
            excerpt=None,
            published_offset_days=None,
        ),
    ]
    assessment = Assessment(
        id="asm_synthetic_0005",
        claim_id=claim.id,
        version=1,
        relations=[
            EvidenceRelation(
                evidence_id=evidence[0].id,
                relation="insufficient",
                note="The only study found was retracted; its abstract cannot carry the claim.",
            ),
            EvidenceRelation(
                evidence_id=evidence[1].id,
                relation="insufficient",
                note="Only the headline was inspected and it does not address reaction speed.",
            ),
        ],
        overall="insufficient_evidence",
        provisional=False,
        summary="No sound evidence was found for or against this claim; it remains unverified.",
    )
    report = ReportVersion(
        id="rpt_synthetic_0005_v1",
        investigation_id=investigation_id,
        version=1,
        created_at=_time(240),
        provisional=False,
        change_summary="First published version: all eligible media checked.",
        supersedes=None,
        claims=[claim],
        evidence=evidence,
        assessments=[assessment],
    )
    return _investigation(
        5,
        state="completed",
        stage="publication",
        processing_status="complete",
        coverage=Coverage(status="complete", covered_ms=42_000, total_ms=42_000),
        version=1,
        error=None,
        source=_url_source(42_000),
        job=_job(105, JobState.PUBLISHED, "publication", offset_seconds=240),
        report=report,
        updated_offset_seconds=240,
    )


def no_claims() -> InvestigationReadModel:
    """Complete with no assessable factual claims. Not a statement that the video is true."""
    report = ReportVersion(
        id="rpt_synthetic_0006_v1",
        investigation_id=_uuid(6),
        version=1,
        created_at=_time(150),
        provisional=False,
        change_summary=(
            "No assessable factual claims were found in the checked media. "
            "This is not a statement that the content is accurate."
        ),
        supersedes=None,
        claims=[],
        evidence=[],
        assessments=[],
    )
    return _investigation(
        6,
        state="completed",
        stage="publication",
        processing_status="complete",
        coverage=Coverage(status="complete", covered_ms=58_000, total_ms=58_000),
        version=1,
        error=None,
        source=_url_source(58_000),
        job=_job(106, JobState.PUBLISHED, "publication", offset_seconds=150),
        report=report,
        updated_offset_seconds=150,
    )


SCENARIOS: dict[str, tuple[Any, str, dict[str, Any]]] = {
    "complete": (
        complete,
        "A shared video checked across its full duration, now at report version 2 after the "
        "user corrected one claim's normalized meaning; version 1 is superseded, not edited. "
        "Synthetic fixture for the investigation read model; no real investigation, media, "
        "source or user exists behind these identifiers.",
        {"claim_count": 2, "assessment_count": 2},
    ),
    "partial": (
        partial,
        "A live capture session still running: coverage is partial, the report is provisional, "
        "one claim is assessed and the second is found but not yet assessed. The job is on "
        "its second attempt after a transient provider error. Synthetic fixture; nothing "
        "behind these identifiers exists.",
        {"claim_count": 2, "assessment_count": 1},
    ),
    "failed": (
        failed,
        "Media validation rejected the input (non-retriable), so the investigation failed with "
        "the stored subset of the shared error shape and no report. A failure is never a "
        "finding: there are no claims, evidence or assessments to read. Synthetic fixture.",
        {"claim_count": 0, "assessment_count": 0, "error_code": "MEDIA_UNSUPPORTED"},
    ),
    "cancelled": (
        cancelled,
        "The owner cancelled during transcription before any report version was published: "
        "the job records the request and the effective cancelled state, coverage shows what "
        "had been reached, and there is no report and no error. Synthetic fixture.",
        {"claim_count": 0, "assessment_count": 0},
    ),
    "insufficient-evidence": (
        insufficient_evidence,
        "A complete check whose only claim could not be settled: one retracted study read at "
        "abstract level and one metadata-only news item, both related as insufficient, so the "
        "overall assessment is insufficient_evidence. That describes the evidence, not the "
        "claim's truth, and is distinct from processing failure. Synthetic fixture.",
        {"claim_count": 1, "assessment_count": 1},
    ),
    "no-claims": (
        no_claims,
        "A complete check in which no assessable factual claims were found. The report version "
        "exists with empty claims, evidence and assessments; the change summary says so. This "
        "is not a verdict that the video is true. Synthetic fixture.",
        {"claim_count": 0, "assessment_count": 0},
    ),
}


def build(name: str) -> dict[str, Any]:
    """Return the complete fixture document for one scenario."""
    factory, description, counts = SCENARIOS[name]
    model: InvestigationReadModel = factory()
    payload = model.model_dump(mode="json")
    expect: dict[str, Any] = {
        "payload": "valid",
        "processing_status": payload["processing_status"],
        "state": payload["state"],
        **counts,
    }
    return {
        "synthetic": True,
        "description": description,
        "expect": expect,
        "investigation": payload,
    }


def render(fixture: dict[str, Any]) -> str:
    """Serialise a fixture as the committed file text: two-space indent, ASCII, LF, one newline."""
    return json.dumps(fixture, indent=2, ensure_ascii=True) + "\n"


def drift(fixture_dir: Path | None = None) -> Iterator[str]:
    """Yield a message per scenario whose committed file differs from the model output."""
    directory = fixture_dir or RESULTS
    for name in SCENARIOS:
        path = directory / f"{name}.json"
        expected = render(build(name))
        if not path.exists():
            yield f"{path}: missing; run roundtrip.py --update"
            continue
        actual = path.read_bytes()
        if actual != expected.encode("ascii"):
            diff = difflib.unified_diff(
                actual.decode("utf-8", errors="replace").splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{path.name} (committed)",
                tofile=f"{path.name} (from models)",
            )
            yield f"{path}: differs from the Pydantic models\n" + "".join(diff)


def update(fixture_dir: Path | None = None) -> list[Path]:
    directory = fixture_dir or RESULTS
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in SCENARIOS:
        path = directory / f"{name}.json"
        path.write_bytes(render(build(name)).encode("ascii"))
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail when a fixture drifted")
    mode.add_argument("--update", action="store_true", help="rewrite the fixtures")
    args = parser.parse_args(argv)
    if args.update:
        for path in update():
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
        return 0
    problems = list(drift())
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"{len(SCENARIOS)} result fixtures match the backend models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
