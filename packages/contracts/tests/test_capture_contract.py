"""Capture progress compatibility and machine-readable duration limits."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate as validate  # noqa: E402


@pytest.mark.parametrize("value", [1000, 10000, 30000])
def test_create_bounds_accept_endpoints(value: int) -> None:
    validate.Validator().validate(
        {"chunk_duration_ms": value}, "capture-create-request.schema.json"
    )


@pytest.mark.parametrize("value", [999, 30001, True, "10000", 1000.5])
def test_create_bounds_reject_invalid_values(value: object) -> None:
    with pytest.raises(validate.Invalid):
        validate.Validator().validate(
            {"chunk_duration_ms": value}, "capture-create-request.schema.json"
        )


@pytest.mark.parametrize("value", [None, 0, 180000])
def test_close_bounds_accept_nullable_endpoints(value: int | None) -> None:
    validate.Validator().validate(
        {"continue_research": True, "duration_ms": value}, "capture-close-request.schema.json"
    )


@pytest.mark.parametrize("value", [-1, 180001, True, "0", 0.5])
def test_close_bounds_reject_invalid_values(value: object) -> None:
    with pytest.raises(validate.Invalid):
        validate.Validator().validate(
            {"continue_research": True, "duration_ms": value},
            "capture-close-request.schema.json",
        )


def test_default_is_annotation_not_injected_value() -> None:
    validator = validate.Validator()
    schema = validator.schemas["capture-create-request.schema.json"]
    assert schema["properties"]["chunk_duration_ms"]["default"] == 10000
    payload: dict[str, object] = {}
    validator.validate(payload, "capture-create-request.schema.json")
    assert payload == {}


@pytest.mark.parametrize("status", ["not_started", "partial", "complete", "future_status"])
def test_extraction_progress_uses_shared_enum(status: str) -> None:
    validator = validate.Validator()
    fixture = validate.read_json(validate.FIXTURES / "intake" / "capture-status-waiting.json")[
        "response"
    ]
    fixture["claim_extraction_status"] = status
    if status == "future_status":
        with pytest.raises(validate.Invalid):
            validator.validate(fixture, "capture-status.schema.json")
        validator.validate(fixture, "capture-status.schema.json", relax_enums=True)
    else:
        validator.validate(fixture, "capture-status.schema.json")


@pytest.mark.parametrize("keyword", ["minimum", "maximum"])
@pytest.mark.parametrize("value", [True, None, "1", [], {}])
def test_numeric_keywords_reject_malformed_schema(
    tmp_path: Path, keyword: str, value: object
) -> None:
    (tmp_path / "numeric.schema.json").write_text(json.dumps({keyword: value}))
    with pytest.raises(validate.Invalid, match="must be a finite number"):
        validate.Validator(tmp_path)


def test_numeric_bounds_apply_without_type_and_to_numbers_only(tmp_path: Path) -> None:
    name = "numeric.schema.json"
    (tmp_path / name).write_text(json.dumps({"minimum": 0.5, "maximum": 1.5}))
    validator = validate.Validator(tmp_path)
    for value in [0.5, 1, 1.5, None, True, "unaffected"]:
        validator.validate(value, name)
    for value in [0.49, 1.51]:
        with pytest.raises(validate.Invalid):
            validator.validate(value, name)
