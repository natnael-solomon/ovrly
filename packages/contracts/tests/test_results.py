"""Contract tests for the investigation read model, its enums and the result fixtures.

Run from the repository root with the backend uv project:

    uv run --project backend --frozen pytest packages/contracts/tests -q

The backend package is importable here, so the enum lists in enums.schema.json are
checked against their sources in backend/services.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE.parents[1] / "backend"))

from services.api import schemas as backend  # noqa: E402
from services.jobs.retries import RetryClass  # noqa: E402
from services.jobs.states import JobState  # noqa: E402

import validate  # noqa: E402

RESULTS = PACKAGE / "fixtures" / "results"
INTAKE = PACKAGE / "fixtures" / "intake"
INVESTIGATION = validate.INVESTIGATION_SCHEMA


@pytest.fixture(scope="module")
def validator() -> validate.Validator:
    return validate.Validator()


def result(name: str) -> dict[str, Any]:
    fixture: dict[str, Any] = validate.read_json(RESULTS / f"{name}.json")
    return fixture


def payload(name: str) -> dict[str, Any]:
    data: dict[str, Any] = copy.deepcopy(result(name)["investigation"])
    return data


def intake(name: str) -> dict[str, Any]:
    fixture: dict[str, Any] = validate.read_json(INTAKE / f"{name}.json")
    return fixture


def enums() -> dict[str, list[str]]:
    schema = validate.read_json(PACKAGE / "schemas" / validate.ENUMS_SCHEMA)
    return {name: list(definition["enum"]) for name, definition in schema["$defs"].items()}


def set_path(node: Any, path: tuple[Any, ...], value: Any) -> None:
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def write(tmp_path: Path, fixture: dict[str, Any]) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    return path


# Whole-package checks --------------------------------------------------


def test_the_six_result_fixtures_exist_and_validate(validator: validate.Validator) -> None:
    names = {path.stem for path in validate.result_fixture_paths()}
    assert names == set(validate.RESULT_FIXTURES)
    for path in validate.result_fixture_paths():
        validate.check_result_fixture(validator, path)


def test_check_all_reports_results_and_intake_fixtures() -> None:
    lines = list(validate.check_all())
    assert sum(line.startswith("fixture fixtures/results/") for line in lines) == 6
    assert sum(line.startswith("fixture fixtures/intake/") for line in lines) == len(
        validate.intake_fixture_paths()
    )


def test_result_fixtures_are_ascii_lf_and_end_with_one_newline() -> None:
    for path in [*validate.result_fixture_paths(), *validate.intake_fixture_paths()]:
        raw = path.read_bytes()
        raw.decode("ascii")
        assert b"\r" not in raw, path.name
        assert raw.endswith(b"}\n") and not raw.endswith(b"\n\n"), path.name


def test_fixture_scenarios_say_what_they_mean() -> None:
    assert result("no-claims")["investigation"]["report"]["claims"] == []
    assert "not a verdict" in result("no-claims")["description"]
    assert "not a statement" in result("no-claims")["investigation"]["report"]["change_summary"]
    failed = result("failed")["investigation"]
    assert failed["report"] is None and failed["error"]["code"] == "MEDIA_UNSUPPORTED"
    assert result("failed")["expect"]["error_code"] == "MEDIA_UNSUPPORTED"
    insufficient = result("insufficient-evidence")["investigation"]["report"]["assessments"][0]
    assert insufficient["overall"] == "insufficient_evidence"
    assert {r["relation"] for r in insufficient["relations"]} == {"insufficient"}
    complete = result("complete")["investigation"]["report"]
    assert complete["version"] == 2 and complete["supersedes"] is not None
    assert complete["claims"][0]["correction"]["attributed_to"] == "user"
    partial = result("partial")["investigation"]
    assert partial["report"]["provisional"] is True
    assert partial["report"]["claims"][0]["interval"]["timebase"] == "capture"
    cancelled = result("cancelled")["investigation"]
    assert cancelled["job"]["cancel_requested"] is True and cancelled["report"] is None


# Enums against their backend sources -----------------------------------


def media_coverage() -> dict[str, Any]:
    return {
        "has_audio": False,
        "has_video": True,
        "speech_status": "unavailable",
        "text_status": "pending",
        "speech_unavailable_reason": "no_audio_track",
    }


def test_prepared_media_coverage_roundtrips_without_becoming_analysis(
    validator: validate.Validator,
) -> None:
    body = payload("cancelled")
    body.update(state="queued", stage="media_validation", processing_status="waiting", job=None)
    body["coverage"] = {"status": "not_started", "total_ms": 1000, "media": media_coverage()}
    validator.validate(body, INVESTIGATION)
    assert (
        backend.Coverage.model_validate(body["coverage"]).model_dump(exclude_unset=True)
        == body["coverage"]
    )


def test_job_state_and_retry_class_enums_come_from_the_job_engine() -> None:
    assert enums()["job_state"] == [state.value for state in JobState]
    assert enums()["retry_class"] == [retry_class.value for retry_class in RetryClass]


@pytest.mark.parametrize(
    ("name", "alias"),
    [
        ("investigation_state", backend.InvestigationState),
        ("upload_state", backend.UploadState),
        ("stage", backend.Stage),
        ("processing_status", backend.ProcessingStatus),
        ("coverage_status", backend.CoverageStatus),
        ("media_speech_status", backend.MediaSpeechStatus),
        ("media_text_status", backend.MediaTextStatus),
        ("speech_unavailable_reason", backend.SpeechUnavailableReason),
        ("speech_status", backend.SpeechStatus),
        ("analysis_status", backend.AnalysisStatus),
        ("speech_reason", backend.SpeechReason),
        ("asr_provider", backend.ASRProvider),
        ("timebase", backend.Timebase),
        ("modality", backend.Modality),
        ("relation", backend.Relation),
        ("overall_assessment", backend.OverallAssessment),
        ("source_inspection_level", backend.SourceInspectionLevel),
        ("source_type", backend.SourceType),
        ("retrieval_relevance", backend.RetrievalRelevance),
        ("retraction_status", backend.RetractionStatus),
        ("correction_attribution", backend.CorrectionAttribution),
    ],
)
def test_schema_enums_match_the_pydantic_literals(name: str, alias: Any) -> None:
    assert enums()[name] == list(get_args(alias))


def test_processing_status_and_relation_vocabularies_are_disjoint() -> None:
    vocab = enums()
    findings = set(vocab["relation"]) | set(vocab["overall_assessment"])
    assert not findings & set(vocab["processing_status"])
    assert not findings & set(vocab["job_state"])
    assert not findings & set(vocab["investigation_state"])
    assert "failed" not in findings and "error" not in findings


def test_stage_enum_contains_the_only_stage_the_backend_uses_today() -> None:
    from services.pipeline.intake import COVERAGE_PLACEHOLDER, INITIAL_STATE, INTAKE_STAGE

    assert INTAKE_STAGE in enums()["stage"]
    assert INITIAL_STATE in enums()["investigation_state"]
    assert COVERAGE_PLACEHOLDER["status"] in enums()["coverage_status"]
    assert validate.STATUS_STATES["waiting"] == {INITIAL_STATE}


def test_client_job_state_mapping_matches_the_status_table() -> None:
    """routes/investigations.py derives the stored state from the intake job."""
    from services.api.routes.investigations import _JOB_STATE_TO_INVESTIGATION

    for job_state, state in _JOB_STATE_TO_INVESTIGATION.items():
        assert job_state in enums()["job_state"]
        assert state in enums()["investigation_state"]
        statuses = [s for s, states in validate.STATUS_STATES.items() if state in states]
        assert any(job_state in validate.STATUS_JOB_STATES[s] for s in statuses), job_state


# Failures are never findings --------------------------------------------


def test_processing_status_cannot_carry_a_relation(validator: validate.Validator) -> None:
    for value in ("support", "challenge", "qualify", "mixed", "insufficient", "supported"):
        body = payload("complete")
        body["processing_status"] = value
        with pytest.raises(validate.Invalid, match="not one of"):
            validator.validate(body, INVESTIGATION)


def test_relation_cannot_carry_a_processing_status(validator: validate.Validator) -> None:
    for value in ("failed", "cancelled", "waiting", "partial"):
        body = payload("complete")
        body["report"]["assessments"][0]["relations"][0]["relation"] = value
        with pytest.raises(validate.Invalid, match="not one of"):
            validator.validate(body, INVESTIGATION)
        body = payload("complete")
        body["report"]["assessments"][0]["overall"] = value
        with pytest.raises(validate.Invalid, match="not one of"):
            validator.validate(body, INVESTIGATION)


def test_failed_investigation_cannot_carry_a_report(validator: validate.Validator) -> None:
    body = payload("failed")
    body["report"] = payload("complete")["report"]
    body["report"]["investigation_id"] = body["id"]
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(body, INVESTIGATION)


def test_failed_fixture_with_assessments_fails_the_harness(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("failed")
    fixture["investigation"]["report"] = payload("insufficient-evidence")["report"]
    fixture["investigation"]["report"]["investigation_id"] = fixture["investigation"]["id"]
    fixture["expect"]["claim_count"] = 1
    fixture["expect"]["assessment_count"] = 1
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_failed_investigation_requires_an_error(validator: validate.Validator) -> None:
    body = payload("failed")
    body["error"] = None
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(body, INVESTIGATION)


def test_complete_investigation_cannot_carry_an_error(validator: validate.Validator) -> None:
    body = payload("complete")
    body["error"] = {"code": "INTERNAL_ERROR", "message": "Processing failed", "retryable": False}
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(body, INVESTIGATION)


def test_stored_error_is_the_shared_shape_subset(validator: validate.Validator) -> None:
    body = payload("failed")
    body["error"]["request_id"] = "req_synthetic_0001"
    with pytest.raises(validate.Invalid, match="unknown fields: \\['request_id'\\]"):
        validator.validate(body, INVESTIGATION)
    body = payload("failed")
    body["error"]["code"] = "lowercase"
    with pytest.raises(validate.Invalid, match="pattern mismatch"):
        validator.validate(body, INVESTIGATION)
    body = payload("failed")
    body["error"]["retryable"] = "false"
    with pytest.raises(validate.Invalid, match="expected boolean"):
        validator.validate(body, INVESTIGATION)


def test_no_claims_report_cannot_carry_assessments(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("no-claims")
    fixture["investigation"]["report"]["assessments"] = payload("complete")["report"]["assessments"]
    fixture["expect"]["assessment_count"] = 2
    with pytest.raises(validate.Invalid, match="unknown claim"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


# The UNKNOWN fallback on read models -------------------------------------

READ_ENUM_PATHS: dict[str, tuple[str, tuple[Any, ...]]] = {
    "analysis_status": ("complete", ("analysis", "status")),
    "speech_status": ("complete", ("speech", "status")),
    "speech_reason": ("complete", ("speech", "reason")),
    "asr_provider": ("complete", ("speech", "provider")),
    "investigation_state": ("complete", ("state",)),
    "stage": ("complete", ("stage",)),
    "processing_status": ("complete", ("processing_status",)),
    "coverage_status": ("complete", ("coverage", "status")),
    "media_speech_status": ("complete", ("coverage", "media", "speech_status")),
    "media_text_status": ("complete", ("coverage", "media", "text_status")),
    "speech_unavailable_reason": ("complete", ("coverage", "media", "speech_unavailable_reason")),
    "source_kind": ("complete", ("source", "kind")),
    "job_state": ("complete", ("job", "state")),
    "retry_class": ("partial", ("job", "retry_class")),
    "timebase": ("complete", ("report", "claims", 0, "interval", "timebase")),
    "modality": ("complete", ("report", "claims", 0, "modality")),
    "correction_attribution": ("complete", ("report", "claims", 0, "correction", "attributed_to")),
    "source_type": ("complete", ("report", "evidence", 0, "source_type")),
    "source_inspection_level": ("complete", ("report", "evidence", 0, "inspection_level")),
    "retrieval_relevance": ("complete", ("report", "evidence", 0, "retrieval_relevance")),
    "retraction_status": ("complete", ("report", "evidence", 0, "retraction_status")),
    "relation": ("complete", ("report", "assessments", 0, "relations", 0, "relation")),
    "overall_assessment": ("complete", ("report", "assessments", 0, "overall")),
}
INTAKE_ENUM_PATHS: dict[str, tuple[str, str, tuple[Any, ...]]] = {
    "upload_state": ("upload-complete", "upload-complete-response.schema.json", ("state",)),
    "capture_session_state": ("capture-session-open", "capture-session.schema.json", ("state",)),
    "chunk_disposition": ("capture-chunk-duplicate", "capture-chunk.schema.json", ("disposition",)),
}


def test_every_enum_has_an_unknown_tolerant_read_path() -> None:
    """Each $def in enums.schema.json is covered by one of the UNKNOWN tests below."""
    assert set(READ_ENUM_PATHS) | set(INTAKE_ENUM_PATHS) == set(enums())


@pytest.mark.parametrize("name", sorted(READ_ENUM_PATHS))
def test_unknown_enum_values_are_tolerated_on_the_investigation_read_model(
    validator: validate.Validator, name: str
) -> None:
    """The #62 pattern: strict validation rejects a future value, relaxed validation proves
    the payload is still structurally readable so the parser maps it to UNKNOWN."""
    fixture, path = READ_ENUM_PATHS[name]
    body = payload(fixture)
    if path[:2] == ("coverage", "media"):
        body["coverage"]["media"] = media_coverage()
    if path[0] == "speech":
        body["speech"] = backend.SpeechResult(status="unavailable", reason="disabled").model_dump()
    if path[0] == "analysis":
        body["analysis"] = {
            "status": "pending",
            "text_deadline": None,
            "text_expired": False,
            "analyzed_modalities": [],
            "pending_modalities": ["text"],
            "unavailable_modalities": [],
            "gaps": [],
            "text": None,
            "captions": [],
        }
    set_path(body, path, "__future_value__")
    with pytest.raises(validate.Invalid, match="not one of"):
        validator.validate(body, INVESTIGATION)
    validator.validate(body, INVESTIGATION, relax_enums=True)


@pytest.mark.parametrize("name", sorted(INTAKE_ENUM_PATHS))
def test_unknown_enum_values_are_tolerated_on_upload_and_capture_reads(
    validator: validate.Validator, name: str
) -> None:
    fixture, schema, path = INTAKE_ENUM_PATHS[name]
    body = copy.deepcopy(intake(fixture)["response"])
    set_path(body, path, "__future_value__")
    with pytest.raises(validate.Invalid, match="not one of"):
        validator.validate(body, schema)
    validator.validate(body, schema, relax_enums=True)


def test_unknown_enum_is_never_a_finding_or_a_success_shape(
    validator: validate.Validator,
) -> None:
    """With enums relaxed the structural branches still tell failure and findings apart."""
    body = payload("failed")
    body["processing_status"] = "__future_value__"
    validator.validate(body, INVESTIGATION, relax_enums=True)
    assert body["report"] is None and body["error"] is not None
    body["report"] = payload("complete")["report"]
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(body, INVESTIGATION, relax_enums=True)


def test_relaxed_mode_still_rejects_structural_damage(validator: validate.Validator) -> None:
    body = payload("complete")
    del body["report"]["claims"][0]["interval"]
    with pytest.raises(validate.Invalid, match="missing required fields"):
        validator.validate(body, INVESTIGATION, relax_enums=True)
    body = payload("complete")
    body["report"]["assessments"][0]["provisional"] = "no"
    with pytest.raises(validate.Invalid, match="expected boolean"):
        validator.validate(body, INVESTIGATION, relax_enums=True)


def test_unknown_enum_fixtures_are_allowed_for_read_models_only(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("complete")
    fixture["investigation"]["processing_status"] = "__future_value__"
    fixture["expect"]["payload"] = "unknown-enum"
    fixture["expect"]["processing_status"] = "__future_value__"
    validate.check_result_fixture(validator, write(tmp_path, fixture))
    request = intake("investigation-create-url")["request"]
    with pytest.raises(validate.Invalid, match="read models and responses only"):
        validate._check_expectation(
            validator, request, "investigation-create-request.schema.json", "unknown-enum", "x"
        )


def test_request_enums_are_validated_strictly(validator: validate.Validator) -> None:
    request = copy.deepcopy(intake("investigation-create-url")["request"])
    request["source"]["kind"] = "__future_value__"
    with pytest.raises(validate.Invalid):
        validator.validate(request, "investigation-create-request.schema.json")
    chunk = copy.deepcopy(intake("capture-chunk-duplicate")["request"])
    chunk["interval"]["timebase"] = "media"
    with pytest.raises(validate.Invalid, match="expected 'capture'"):
        validator.validate(chunk, "capture-chunk-request.schema.json")


# Fixture harness negatives ----------------------------------------------


def test_state_must_fit_processing_status(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = result("complete")
    fixture["investigation"]["state"] = "running"
    fixture["expect"]["state"] = "running"
    with pytest.raises(validate.Invalid, match="state 'running' does not fit status 'complete'"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))
    fixture = result("complete")
    fixture["investigation"]["state"] = "failed"
    fixture["expect"]["state"] = "failed"
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))
    fixture = result("partial")
    fixture["investigation"]["job"]["state"] = "failed"
    with pytest.raises(validate.Invalid, match="job state 'failed' does not fit"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_counts_must_match_the_report(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = result("complete")
    fixture["expect"]["claim_count"] = 3
    with pytest.raises(validate.Invalid, match="claim_count differs"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_claim_intervals_must_be_ordered(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = result("complete")
    claim = fixture["investigation"]["report"]["claims"][0]
    claim["interval"]["end_ms"] = claim["interval"]["start_ms"]
    with pytest.raises(validate.Invalid, match="0 <= start_ms < end_ms"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_references_must_resolve_inside_the_report(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("complete")
    fixture["investigation"]["report"]["assessments"][0]["relations"][0]["evidence_id"] = (
        "evd_synthetic_9999"
    )
    with pytest.raises(validate.Invalid, match="unknown evidence"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))
    fixture = result("complete")
    fixture["investigation"]["report"]["evidence"][0]["claim_id"] = "clm_synthetic_9999"
    with pytest.raises(validate.Invalid, match="unknown claim"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_assessment_without_relations_is_insufficient_evidence(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("complete")
    fixture["investigation"]["report"]["assessments"][0]["relations"] = []
    with pytest.raises(validate.Invalid, match="insufficient_evidence"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_complete_reports_assess_every_claim_and_are_final(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = result("complete")
    fixture["investigation"]["report"]["assessments"].pop()
    fixture["expect"]["assessment_count"] = 1
    with pytest.raises(validate.Invalid, match="assess every claim"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))
    fixture = result("complete")
    fixture["investigation"]["report"]["provisional"] = True
    with pytest.raises(validate.Invalid, match="not provisional"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_identifiers_must_be_synthetic(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = result("complete")
    fixture["investigation"]["id"] = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    fixture["investigation"]["report"]["investigation_id"] = fixture["investigation"]["id"]
    with pytest.raises(validate.Invalid, match="synthetic UUID prefix"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))
    fixture = result("complete")
    fixture["investigation"]["report"]["claims"][0]["id"] = "clm_0001"
    fixture["investigation"]["report"]["evidence"][0]["claim_id"] = "clm_0001"
    fixture["investigation"]["report"]["evidence"][1]["claim_id"] = "clm_0001"
    fixture["investigation"]["report"]["assessments"][0]["claim_id"] = "clm_0001"
    with pytest.raises(validate.Invalid, match="is not synthetic"):
        validate.check_result_fixture(validator, write(tmp_path, fixture))


def test_intake_fixture_schemas_and_expectations_must_agree(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = intake("upload-declare")
    del fixture["schemas"]["response"]
    with pytest.raises(validate.Invalid, match="same payloads"):
        validate.check_intake_fixture(validator, write(tmp_path, fixture))
    fixture = intake("upload-declare")
    fixture["schemas"]["response"] = "missing.schema.json"
    with pytest.raises(validate.Invalid, match="unknown schema"):
        validate.check_intake_fixture(validator, write(tmp_path, fixture))


# Schema subset: arrays ---------------------------------------------------


def test_items_keyword_validates_every_element(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"type": "array", "items": {"type": "integer"}}), encoding="utf-8"
    )
    validator = validate.Validator(schema_dir)
    validator.validate([], "x.schema.json")
    validator.validate([1, 2], "x.schema.json")
    with pytest.raises(validate.Invalid, match=r"\$\[1\]: expected integer"):
        validator.validate([1, "2"], "x.schema.json")
    with pytest.raises(validate.Invalid, match="expected array"):
        validator.validate({"0": 1}, "x.schema.json")


def test_validator_fails_closed_on_other_array_keywords(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    for keyword in ("prefixItems", "uniqueItems", "contains"):
        (schema_dir / "x.schema.json").write_text(
            json.dumps({"type": "array", keyword: 1}), encoding="utf-8"
        )
        with pytest.raises(validate.Invalid, match="unsupported keywords"):
            validate.Validator(schema_dir)


def test_array_bounds_apply_without_an_items_schema(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"type": "array", "minItems": 1, "maxItems": 2}), encoding="utf-8"
    )
    validator = validate.Validator(schema_dir)
    validator.validate([None], "x.schema.json")
    validator.validate([1, "two"], "x.schema.json")
    for value in ([], [1, 2, 3]):
        with pytest.raises(validate.Invalid, match="array too"):
            validator.validate(value, "x.schema.json")


@pytest.mark.parametrize("bound", [-1, True, 1.5, "2"])
def test_array_bound_schema_requires_nonnegative_integer(tmp_path: Path, bound: object) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    for keyword in ("minItems", "maxItems"):
        (schema_dir / "x.schema.json").write_text(
            json.dumps({"type": "array", keyword: bound}), encoding="utf-8"
        )
        with pytest.raises(validate.Invalid, match="nonnegative integer"):
            validate.Validator(schema_dir)


def test_items_schema_is_self_checked(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"type": "array", "items": {"type": "object", "patternProperties": {}}}),
        encoding="utf-8",
    )
    with pytest.raises(validate.Invalid, match=r"items: unsupported keywords"):
        validate.Validator(schema_dir)


# Scalar types -------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["2026-10-04T12:00:00", "2026-10-04 12:00:00Z", "2026-10-04T12:00Z", "today", ""],
)
def test_timestamps_need_an_offset(validator: validate.Validator, value: str) -> None:
    body = payload("complete")
    body["created_at"] = value
    with pytest.raises(validate.Invalid):
        validator.validate(body, INVESTIGATION)


def test_timestamps_accept_pydantic_and_offset_forms(validator: validate.Validator) -> None:
    for value in ("2026-10-04T12:00:00Z", "2026-10-04T12:00:00.123456+03:00"):
        body = payload("complete")
        body["created_at"] = value
        validator.validate(body, INVESTIGATION)


@pytest.mark.parametrize(
    "value", ["00000000-0000-4000-8000-00000000000", "not-a-uuid", "SYNTHETIC-0001", 1]
)
def test_backend_row_ids_are_uuids(validator: validate.Validator, value: Any) -> None:
    body = payload("complete")
    body["id"] = value
    body["report"]["investigation_id"] = value
    with pytest.raises(validate.Invalid):
        validator.validate(body, INVESTIGATION)


# The request schemas mirror the Pydantic request models -----------------


def test_investigation_create_requests_mirror_the_pydantic_union() -> None:
    for name in ("investigation-create-url", "investigation-create-upload"):
        request = intake(name)["request"]
        model = backend.InvestigationCreateRequest.model_validate(request)
        dumped = model.model_dump(mode="json")
        assert dumped["source"]["kind"] == request["source"]["kind"]
    with pytest.raises(ValidationError):
        backend.InvestigationCreateRequest.model_validate(
            intake("investigation-create-mixed-source")["request"]
        )
    with pytest.raises(ValidationError):
        backend.InvestigationCreateRequest.model_validate(
            {"source": {"kind": "url", "url": "https://x.example/a", "user_id": "u"}}
        )


def test_upload_declare_request_mirrors_the_pydantic_model() -> None:
    request = intake("upload-declare")["request"]
    assert backend.UploadCreateRequest.model_validate(request).model_dump() == request
    with pytest.raises(ValidationError):
        backend.UploadCreateRequest.model_validate({**request, "owner_id": "x"})


def test_intake_read_payloads_are_what_the_pydantic_models_serialise() -> None:
    for name in ("upload-declare", "upload-complete"):
        response = intake(name)["response"]
        assert backend.UploadResponse.model_validate(response).model_dump(mode="json") == response
    for name in ("investigation-create-url", "investigation-create-upload"):
        response = intake(name)["response"]
        model = backend.InvestigationReadModel.model_validate(response)
        assert model.model_dump(mode="json") == response
        parent = backend.InvestigationResponse.model_validate(response)
        assert parent.model_dump(mode="json") == {
            key: value
            for key, value in response.items()
            if key not in {"processing_status", "job", "report"}
        }


def test_result_payloads_load_back_into_the_read_model() -> None:
    for name in validate.RESULT_FIXTURES:
        body = payload(name)
        assert backend.InvestigationReadModel.model_validate(body).model_dump(mode="json") == body
