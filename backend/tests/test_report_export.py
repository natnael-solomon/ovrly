"""Export payload builder (BE-10, #33): the field allowlist and limitations, without a database."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_intake_api import ContractInvalid
from test_reports_api import assert_component

from services.api.schemas import ReportVersion
from services.report_export import FIXTURE_LIMITATION, VERDICT_LIMITATION, build_export

RESULTS = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures" / "results"
RETRIEVED = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
# Fields of the read model that an export must never carry.
FORBIDDEN_KEYS = {"original_text", "excerpt", "note", "occurrence_id", "owner_id", "media"}


def fixture_report(name: str) -> ReportVersion:
    document = json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))
    return ReportVersion.model_validate(document["investigation"]["report"])


def keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys(item)}
    return set()


@pytest.mark.parametrize("name", ["complete", "partial", "insufficient-evidence", "no-claims"])
def test_export_is_an_allowlist_of_the_report(name):
    report = fixture_report(name)
    exported = build_export(report, fixture=False, retrieved_at=RETRIEVED).model_dump(mode="json")
    assert_component(exported, "ReportExport")
    if exported["claims"]:
        leaked = {**exported, "claims": [{**exported["claims"][0], "original_text": "x"}]}
        with pytest.raises(ContractInvalid):
            assert_component(leaked, "ReportExport")
    assert not keys(exported) & FORBIDDEN_KEYS
    text = json.dumps(exported)
    for claim in report.claims:
        assert claim.original_text not in text
    for item in report.evidence:
        assert item.excerpt is None or item.excerpt not in text
    assert exported["format"] == "ovrly.report-export" and exported["format_version"] == 1
    assert exported["retrieved_at"] == "2026-10-05T18:00:00Z"
    assert exported["version"] == report.version and exported["report_id"] == report.id
    assert exported["provisional"] is report.provisional and exported["fixture"] is False
    assert [claim["id"] for claim in exported["claims"]] == [claim.id for claim in report.claims]
    assert [source["evidence_id"] for source in exported["sources"]] == [
        item.id for item in report.evidence
    ]
    assert exported["limitations"][-1] == VERDICT_LIMITATION
    assert FIXTURE_LIMITATION not in exported["limitations"]


def test_export_carries_assessments_corrections_and_relations():
    report = fixture_report("complete")
    exported = build_export(report, fixture=True, retrieved_at=RETRIEVED)
    assert exported.limitations[0] == FIXTURE_LIMITATION
    by_claim = {claim.id: claim for claim in exported.claims}
    for assessment in report.assessments:
        exported_assessment = by_claim[assessment.claim_id].assessment
        assert exported_assessment is not None
        assert exported_assessment.overall == assessment.overall
        for relation in assessment.relations:
            source = next(s for s in exported.sources if s.evidence_id == relation.evidence_id)
            assert source.relation == relation.relation
    corrected = [claim.id for claim in report.claims if claim.correction is not None]
    assert corrected and all(by_claim[claim_id].corrected for claim_id in corrected)


def test_limitations_describe_partial_and_empty_reports():
    partial = build_export(fixture_report("partial"), fixture=False, retrieved_at=RETRIEVED)
    assert any("provisional" in note for note in partial.limitations)
    assert any("have not been assessed yet" in note for note in partial.limitations)
    assert any(claim.assessment is None for claim in partial.claims)

    empty = build_export(fixture_report("no-claims"), fixture=False, retrieved_at=RETRIEVED)
    assert empty.claims == [] and empty.sources == []
    assert any("does not mean the video is accurate" in note for note in empty.limitations)

    weak = build_export(
        fixture_report("insufficient-evidence"), fixture=False, retrieved_at=RETRIEVED
    )
    assert any("retracted or withdrawn" in note for note in weak.limitations)
    assert any("read only in part" in note for note in weak.limitations)
