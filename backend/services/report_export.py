"""The export payload of one report version (BE-10, #33; decision 0003 export contents).

The fields are an explicit allowlist: each claim's normalized meaning, interval and
assessment, the source links with their inspection level and retraction status, the
provisional and fixture flags, the report version and the retrieval date, plus
plain-language limitations. Transcript wording, evidence excerpts, assessment notes, media
and any identity are never copied. On-device export (AN-11, #39) covers the submission; this
endpoint gives clients a server-built equivalent.
"""

from datetime import datetime

from services.api.schemas import (
    ExportAssessment,
    ExportClaim,
    ExportSource,
    Relation,
    ReportExport,
    ReportVersion,
)

FIXTURE_LIMITATION = "Development fixture: this report was not produced by checking this media."
VERDICT_LIMITATION = (
    "Assessments describe the evidence that was found for each claim; they are not a verdict "
    "on the whole video."
)


def limitations(report: ReportVersion, fixture: bool) -> list[str]:
    notes = []
    if fixture:
        notes.append(FIXTURE_LIMITATION)
    if report.provisional:
        notes.append("This report is provisional: checking was not complete when it was made.")
    assessed = {item.claim_id for item in report.assessments}
    unassessed = sum(1 for claim in report.claims if claim.id not in assessed)
    if unassessed:
        notes.append(f"{unassessed} of {len(report.claims)} claims have not been assessed yet.")
    if not report.claims:
        notes.append(
            "No assessable factual claim was found. This does not mean the video is accurate."
        )
    if any(item.inspection_level != "full_text" for item in report.evidence):
        notes.append("Some sources were read only in part (abstract or metadata).")
    if any(item.retraction_status in {"retracted", "withdrawn"} for item in report.evidence):
        notes.append("At least one source has been retracted or withdrawn.")
    notes.append(VERDICT_LIMITATION)
    return notes


def build_export(report: ReportVersion, *, fixture: bool, retrieved_at: datetime) -> ReportExport:
    assessments = {item.claim_id: item for item in report.assessments}
    relations: dict[str, Relation] = {
        relation.evidence_id: relation.relation
        for item in report.assessments
        for relation in item.relations
    }
    claims = []
    for claim in report.claims:
        assessment = assessments.get(claim.id)
        claims.append(
            ExportClaim(
                id=claim.id,
                proposition=claim.proposition,
                interval=claim.interval,
                corrected=claim.correction is not None,
                assessment=None
                if assessment is None
                else ExportAssessment(
                    overall=assessment.overall,
                    summary=assessment.summary,
                    provisional=assessment.provisional,
                ),
            )
        )
    sources = [
        ExportSource(
            evidence_id=item.id,
            claim_id=item.claim_id,
            title=item.source.title,
            publisher=item.source.publisher,
            url=item.source.url,
            published_at=item.source.published_at,
            retrieved_at=item.retrieved_at,
            source_type=item.source_type,
            inspection_level=item.inspection_level,
            retraction_status=item.retraction_status,
            relation=relations.get(item.id),
        )
        for item in report.evidence
    ]
    return ReportExport(
        investigation_id=report.investigation_id,
        report_id=report.id,
        version=report.version,
        supersedes=report.supersedes,
        provisional=report.provisional,
        fixture=fixture,
        report_created_at=report.created_at,
        retrieved_at=retrieved_at,
        change_summary=report.change_summary,
        claims=claims,
        sources=sources,
        limitations=limitations(report, fixture),
    )
