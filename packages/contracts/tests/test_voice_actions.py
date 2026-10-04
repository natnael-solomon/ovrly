"""Contract tests for the voice-actions schemas and fixtures.

Run from the repository root with the backend uv project:

    uv run --project backend --frozen pytest packages/contracts/tests -q
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

import validate  # noqa: E402

FIXTURES = PACKAGE / "fixtures" / "voice-actions"
ANDROID_CONTRACT = (
    PACKAGE.parents[1] / "android/app/src/main/java/app/ovrly/voice/VoiceCommandContract.kt"
)


@pytest.fixture(scope="module")
def validator() -> validate.Validator:
    return validate.Validator()


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = validate.read_json(FIXTURES / f"{name}.json")
    return data


def accepted_request(action: str) -> dict[str, Any]:
    request: dict[str, Any] = copy.deepcopy(load(f"{action.replace('_', '-')}-accepted")["request"])
    return request


def accepted_response(action: str) -> dict[str, Any]:
    response: dict[str, Any] = copy.deepcopy(
        load(f"{action.replace('_', '-')}-accepted")["response"]
    )
    return response


def error(code: str = "VOICE_TARGET_NOT_FOUND", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code,
        "message": "m",
        "retryable": False,
        "action": "none",
        "request_id": "req_synthetic_test",
    }
    body.update(overrides)
    return body


# Whole-package checks --------------------------------------------------


def test_every_schema_and_fixture_passes_the_validator() -> None:
    lines = list(validate.check_all())
    assert any(line.startswith("version 0.") for line in lines)
    assert sum(line.startswith("fixture ") for line in lines) == len(validate.fixture_paths())


def test_cli_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert validate.main() == 0
    assert "Synthetic fixtures validated" in capsys.readouterr().out


def test_each_allowed_action_has_an_accepted_pair(validator: validate.Validator) -> None:
    for action in validate.ALLOWED_ACTIONS:
        fixture = load(f"{action.replace('_', '-')}-accepted")
        assert fixture["expect"] == {"request": "valid", "response": "valid"}
        assert fixture["request"]["action"] == action
        assert fixture["response"]["result"] == "accepted"
        validator.validate(fixture["request"], validate.REQUEST_SCHEMA)
        validator.validate(fixture["response"], validate.RESPONSE_SCHEMA)


def test_every_error_code_has_a_denied_fixture() -> None:
    codes = {
        fixture["expect"]["error_code"]
        for path in validate.fixture_paths()
        for fixture in [validate.read_json(path)]
        if "error_code" in fixture["expect"]
    }
    assert codes == set(validate.ERROR_CODES)


def test_fixtures_are_labelled_synthetic_and_use_synthetic_ids() -> None:
    for path in validate.fixture_paths():
        text = path.read_text(encoding="utf-8")
        fixture = json.loads(text)
        assert fixture["synthetic"] is True
        for part in ("request", "response"):
            payload = fixture.get(part, {})
            for value in (payload.get("request_id"), payload.get("target", {}).get("id")):
                if value is not None:
                    assert "synthetic" in value, (
                        f"{path.name}: {value!r} is not obviously synthetic"
                    )


def test_schema_allowlist_matches_the_android_client() -> None:
    schema = validate.read_json(PACKAGE / "schemas" / validate.REQUEST_SCHEMA)
    assert schema["properties"]["action"]["enum"] == list(validate.ALLOWED_ACTIONS)
    assert "switch_tab" not in schema["properties"]["action"]["enum"]
    assert "open_tab" not in schema["properties"]["action"]["enum"]
    if ANDROID_CONTRACT.exists():
        kotlin = ANDROID_CONTRACT.read_text(encoding="utf-8")
        for action in validate.ALLOWED_ACTIONS:
            assert f'("{action}")' in kotlin


def test_response_error_codes_are_exactly_the_agreed_four() -> None:
    schema = validate.read_json(PACKAGE / "schemas" / validate.RESPONSE_SCHEMA)
    enum = schema["properties"]["error"]["allOf"][1]["properties"]["code"]["enum"]
    assert enum == list(validate.ERROR_CODES)


# Request negatives -----------------------------------------------------


@pytest.mark.parametrize(
    "action", ["delete_report", "publish_report", "open_settings", "switch_tab", "open_tab"]
)
def test_action_outside_allowlist_fails(validator: validate.Validator, action: str) -> None:
    request = accepted_request("save_report")
    request["action"] = action
    with pytest.raises(validate.Invalid, match="not one of"):
        validator.validate(request, validate.REQUEST_SCHEMA)


def test_request_without_target_fails(validator: validate.Validator) -> None:
    request = accepted_request("open_check")
    del request["target"]
    with pytest.raises(validate.Invalid, match="missing required fields: \\['target'\\]"):
        validator.validate(request, validate.REQUEST_SCHEMA)


def test_request_without_request_id_fails(validator: validate.Validator) -> None:
    request = accepted_request("open_check")
    del request["request_id"]
    with pytest.raises(validate.Invalid, match="request_id"):
        validator.validate(request, validate.REQUEST_SCHEMA)


@pytest.mark.parametrize(
    ("action", "wrong_kind"),
    [("open_check", "job"), ("save_report", "investigation"), ("queue_retry", "report")],
)
def test_target_kind_must_match_action(
    validator: validate.Validator, action: str, wrong_kind: str
) -> None:
    request = accepted_request(action)
    request["target"]["kind"] = wrong_kind
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(request, validate.REQUEST_SCHEMA)


@pytest.mark.parametrize("extra", [{"confirmed": True}, {"owner": "someone"}, {"args": {}}])
def test_free_form_arguments_are_rejected(
    validator: validate.Validator, extra: dict[str, Any]
) -> None:
    request = accepted_request("queue_cancel")
    request.update(extra)
    with pytest.raises(validate.Invalid, match="unknown fields"):
        validator.validate(request, validate.REQUEST_SCHEMA)
    request = accepted_request("queue_cancel")
    request["target"].update(extra)
    with pytest.raises(validate.Invalid, match="unknown fields"):
        validator.validate(request, validate.REQUEST_SCHEMA)


@pytest.mark.parametrize(
    "bad_id", ["", " job_1", "job_1\n", "-leading-hyphen", "job/1", "a" * 129, 42, None]
)
def test_target_id_syntax_is_strict(validator: validate.Validator, bad_id: Any) -> None:
    request = accepted_request("queue_retry")
    request["target"]["id"] = bad_id
    with pytest.raises(validate.Invalid):
        validator.validate(request, validate.REQUEST_SCHEMA)


def test_request_enums_are_never_relaxed(validator: validate.Validator) -> None:
    request = accepted_request("open_check")
    request["action"] = "__future_value__"
    with pytest.raises(validate.Invalid):
        validator.validate(request, validate.REQUEST_SCHEMA, relax_enums=True)


# Response negatives and the UNKNOWN fallback ----------------------------


def test_accepted_response_cannot_carry_an_error(validator: validate.Validator) -> None:
    response = accepted_response("open_check")
    response["error"] = error(request_id=response["request_id"])
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(response, validate.RESPONSE_SCHEMA)


def test_denied_response_requires_an_error(validator: validate.Validator) -> None:
    response = accepted_response("open_check")
    response["result"] = "denied"
    with pytest.raises(validate.Invalid, match="no oneOf branch matched"):
        validator.validate(response, validate.RESPONSE_SCHEMA)


def test_denied_response_rejects_codes_outside_the_voice_set(
    validator: validate.Validator,
) -> None:
    response = load("cross-owner-denied")["response"]
    response = copy.deepcopy(response)
    response["error"]["code"] = "VOICE_ACTION_INVALID_ARGUMENTS"
    with pytest.raises(validate.Invalid, match="not one of"):
        validator.validate(response, validate.RESPONSE_SCHEMA)


def test_error_code_syntax_is_enforced_by_the_shared_shape(
    validator: validate.Validator,
) -> None:
    for code in ("__FUTURE_VALUE__", "lowercase", "AB", "HAS SPACE", "X" * 65):
        with pytest.raises(validate.Invalid):
            validator.validate(error(code), validate.ERROR_SCHEMA)
    validator.validate(error("ANY_FUTURE_CODE"), validate.ERROR_SCHEMA)


@pytest.mark.parametrize("field", ["code", "message", "retryable", "action", "request_id"])
def test_every_error_field_is_required(validator: validate.Validator, field: str) -> None:
    body = error()
    del body[field]
    with pytest.raises(validate.Invalid, match=f"missing required fields: \\['{field}'\\]"):
        validator.validate(body, validate.ERROR_SCHEMA)


def test_error_shape_has_no_details_or_extra_fields(validator: validate.Validator) -> None:
    with pytest.raises(validate.Invalid, match="unknown fields: \\['details'\\]"):
        validator.validate(error(details={}), validate.ERROR_SCHEMA)


def test_error_action_enum_and_retryable_type(validator: validate.Validator) -> None:
    for action in ("none", "retry", "authenticate", "fix_request", "upload_again"):
        validator.validate(error(action=action), validate.ERROR_SCHEMA)
    with pytest.raises(validate.Invalid, match="not one of"):
        validator.validate(error(action="reboot"), validate.ERROR_SCHEMA)
    with pytest.raises(validate.Invalid, match="expected boolean"):
        validator.validate(error(retryable="false"), validate.ERROR_SCHEMA)


def test_error_request_id_syntax_matches_opaque_id(validator: validate.Validator) -> None:
    request_schema = validate.read_json(PACKAGE / "schemas" / validate.REQUEST_SCHEMA)
    error_schema = validate.read_json(PACKAGE / "schemas" / validate.ERROR_SCHEMA)
    opaque = request_schema["$defs"]["opaque_id"]
    field = error_schema["properties"]["request_id"]
    for key in ("type", "minLength", "maxLength", "pattern"):
        assert field[key] == opaque[key], key
    for bad in ("", " req", "req\n", "-req", "req/1", "a" * 129):
        with pytest.raises(validate.Invalid):
            validator.validate(error(request_id=bad), validate.ERROR_SCHEMA)


def test_denied_fixtures_echo_request_id_inside_the_error() -> None:
    for path in validate.fixture_paths():
        fixture = validate.read_json(path)
        err = fixture.get("response", {}).get("error")
        if err is not None:
            assert err["request_id"] == fixture["response"]["request_id"], path.name
            assert err["retryable"] is False, path.name


def test_message_is_required_and_bounded(validator: validate.Validator) -> None:
    response = accepted_response("save_report")
    response["message"] = ""
    with pytest.raises(validate.Invalid, match="string too short"):
        validator.validate(response, validate.RESPONSE_SCHEMA)
    response["message"] = "m" * 201
    with pytest.raises(validate.Invalid, match="string too long"):
        validator.validate(response, validate.RESPONSE_SCHEMA)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("result",), "__future_value__"),
        (("target", "kind"), "__future_value__"),
        (("error", "code"), "FUTURE_VALUE"),
        (("error", "action"), "__future_value__"),
    ],
)
def test_unknown_enum_values_are_structurally_representable(
    validator: validate.Validator, path: tuple[str, ...], value: str
) -> None:
    """The #62 Android pattern: unknown enum strings parse to UNKNOWN, never to success.

    Strict validation must reject the value (the server never emits it in this version),
    while the same payload with enum checks relaxed is structurally readable: every other
    field keeps its type and the accepted/denied shape is still unambiguous.
    """
    base = "cross-owner-denied" if path[0] == "error" else "open-check-accepted"
    response = copy.deepcopy(load(base)["response"])
    node: Any = response
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(validate.Invalid):
        validator.validate(response, validate.RESPONSE_SCHEMA)
    validator.validate(response, validate.RESPONSE_SCHEMA, relax_enums=True)


def test_relaxed_mode_still_rejects_structural_damage(validator: validate.Validator) -> None:
    response = copy.deepcopy(load("unknown-enum-response")["response"])
    del response["message"]
    with pytest.raises(validate.Invalid, match="missing required fields"):
        validator.validate(response, validate.RESPONSE_SCHEMA, relax_enums=True)


# Fixture harness negatives ----------------------------------------------


def test_negative_fixture_expectations_are_really_enforced(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = load("missing-target")
    fixture["expect"]["request"] = "valid"
    path = tmp_path / "missing-target.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(validate.Invalid, match="expected valid but"):
        validate.check_fixture(validator, path)


def test_positive_fixture_cannot_be_marked_invalid(
    validator: validate.Validator, tmp_path: Path
) -> None:
    fixture = load("open-check-accepted")
    fixture["expect"]["response"] = "invalid"
    path = tmp_path / "open-check-accepted.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(validate.Invalid, match="expected the payload to fail"):
        validate.check_fixture(validator, path)


def test_fixture_must_be_labelled_synthetic(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = load("open-check-accepted")
    fixture["synthetic"] = False
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(validate.Invalid, match="synthetic"):
        validate.check_fixture(validator, path)


def test_response_must_echo_request(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = load("open-check-accepted")
    fixture["response"]["request_id"] = "req_synthetic_other"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(validate.Invalid, match="request_id must echo"):
        validate.check_fixture(validator, path)


def test_error_request_id_must_echo_response(validator: validate.Validator, tmp_path: Path) -> None:
    fixture = load("cross-owner-denied")
    fixture["response"]["error"]["request_id"] = "req_synthetic_other"
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(validate.Invalid, match="error.request_id must echo"):
        validate.check_fixture(validator, path)


def test_validator_fails_closed_on_unsupported_schema_keywords(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"type": "object", "patternProperties": {}}), encoding="utf-8"
    )
    with pytest.raises(validate.Invalid, match="unsupported keywords"):
        validate.Validator(schema_dir)


def test_validator_rejects_dangling_references(tmp_path: Path) -> None:
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"$ref": "#/$defs/missing", "$defs": {}}), encoding="utf-8"
    )
    with pytest.raises(validate.Invalid, match="unresolvable reference"):
        validate.Validator(schema_dir)


def test_version_must_stay_a_pre_release_draft(tmp_path: Path) -> None:
    version = tmp_path / "VERSION"
    version.write_text("1.0.0\n", encoding="utf-8")
    with pytest.raises(validate.Invalid, match="pre-1.0"):
        validate.check_version(version)
    version.write_text("0.2.0-draft\n", encoding="utf-8")
    assert validate.check_version(version) == "0.2.0-draft"
