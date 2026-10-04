"""Tests for openapi_check.py (document rules) and compat.py (breaking-change gate).

Run from the repository root with the backend uv project and its ``contracts`` group:

    uv run --project backend --frozen --group contracts pytest packages/contracts/tests -q
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))

import compat  # noqa: E402
import openapi_check  # noqa: E402
import validate  # noqa: E402

OPENAPI = PACKAGE / "openapi.json"
SPECTRAL = PACKAGE / ".spectral.yaml"


def document() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(OPENAPI.read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def write_document(tmp_path: Path, doc: dict[str, Any]) -> Path:
    """Write a modified document next to a copy of schemas/ so relative refs resolve."""
    shutil.copytree(PACKAGE / "schemas", tmp_path / "schemas", dirs_exist_ok=True)
    path = tmp_path / "openapi.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# openapi_check -------------------------------------------------------------


def test_committed_document_passes_every_rule() -> None:
    lines = list(openapi_check.check_all())
    assert lines[0] == "openapi 3.1 document valid"
    assert any(line.startswith("version 0.") for line in lines)
    assert "typed enums" in lines[-1]


def test_cli_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert openapi_check.main() == 0
    assert "OpenAPI document validated" in capsys.readouterr().out


def test_document_version_equals_the_package_version() -> None:
    assert document()["info"]["version"] == validate.check_version()


def test_every_schema_file_is_reachable_from_the_document() -> None:
    referenced = openapi_check.check_file_refs(document())
    published = {f"schemas/{p.name}" for p in (PACKAGE / "schemas").glob("*.schema.json")}
    assert published - referenced == {"schemas/enums.schema.json"}


def test_every_error_response_is_the_shared_shape() -> None:
    doc = document()
    assert openapi_check.check_error_responses(doc) >= 40
    for name, response in doc["components"]["responses"].items():
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Error"
        }, name


def test_invalid_openapi_document_is_rejected(tmp_path: Path) -> None:
    doc = document()
    doc["paths"]["/v1/investigations"]["get"]["responses"]["200"] = "not a response object"
    path = write_document(tmp_path, doc)
    with pytest.raises(openapi_check.Invalid, match="openapi.json"):
        openapi_check.check_spec(doc, path.as_uri())


def test_dangling_schema_file_ref_is_rejected() -> None:
    doc = document()
    doc["components"]["schemas"]["Claim"] = {"$ref": "schemas/missing.schema.json"}
    with pytest.raises(openapi_check.Invalid, match="does not exist"):
        openapi_check.check_file_refs(doc)
    doc["components"]["schemas"]["Claim"] = {"$ref": "../backend/x.json"}
    with pytest.raises(openapi_check.Invalid, match="must point into schemas/"):
        openapi_check.check_file_refs(doc)


def test_unreferenced_schema_file_is_rejected() -> None:
    doc = document()
    del doc["components"]["schemas"]["Claim"]
    with pytest.raises(openapi_check.Invalid, match="does not reference"):
        openapi_check.check_file_refs(doc)


def test_version_mismatch_is_rejected(tmp_path: Path) -> None:
    doc = document()
    version = tmp_path / "VERSION"
    version.write_text("0.9.0-draft\n", encoding="utf-8")
    with pytest.raises(openapi_check.Invalid, match="does not equal VERSION"):
        openapi_check.check_version(doc, version)


def test_error_response_with_another_shape_is_rejected() -> None:
    doc = document()
    response = doc["paths"]["/v1/uploads"]["post"]["responses"]["413"]
    response["content"]["application/json"]["schema"] = {"type": "object"}
    with pytest.raises(openapi_check.Invalid, match="error body must be"):
        openapi_check.check_error_responses(doc)
    doc = document()
    doc["components"]["responses"]["NotFound"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Upload"
    }
    with pytest.raises(openapi_check.Invalid, match="error body must be"):
        openapi_check.check_error_responses(doc)
    doc = document()
    doc["components"]["schemas"]["Error"] = {"type": "object"}
    with pytest.raises(openapi_check.Invalid, match="components.schemas.Error"):
        openapi_check.check_error_responses(doc)


def test_response_without_request_id_header_is_rejected() -> None:
    doc = document()
    del doc["paths"]["/v1/voice/actions"]["post"]["responses"]["200"]["headers"]
    with pytest.raises(openapi_check.Invalid, match="X-Request-Id"):
        openapi_check.check_error_responses(doc)


def test_untyped_enum_is_rejected(tmp_path: Path) -> None:
    doc = document()
    doc["components"]["schemas"]["GuestPrincipalResponse"]["properties"]["kind"] = {
        "enum": ["guest"]
    }
    with pytest.raises(openapi_check.Invalid, match="enum without"):
        openapi_check.check_typed_enums(doc, PACKAGE / "schemas")
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "x.schema.json").write_text(
        json.dumps({"type": "string", "enum": ["a", 1]}), encoding="utf-8"
    )
    with pytest.raises(openapi_check.Invalid, match="nonempty list of strings"):
        openapi_check.check_typed_enums(document(), schema_dir)


def test_spectral_ruleset_mirrors_the_python_rules() -> None:
    text = SPECTRAL.read_text(encoding="utf-8")
    for rule in (
        "ovrly-error-responses-use-error-shape",
        "ovrly-error-component-is-the-shared-shape",
        "ovrly-responses-echo-request-id",
        "ovrly-no-untyped-enums",
    ):
        assert rule in text
    assert "spectral:oas" in text
    assert "#/components/schemas/Error" in text


# compat -------------------------------------------------------------------


def copy_contracts(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.mkdir()
    shutil.copytree(PACKAGE / "schemas", target / "schemas")
    shutil.copy(OPENAPI, target / "openapi.json")
    shutil.copy(PACKAGE / "VERSION", target / "VERSION")
    return target


def narrow_enum(contracts: Path, name: str, value: str) -> None:
    path = contracts / "schemas" / validate.ENUMS_SCHEMA
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["$defs"][name]["enum"].remove(value)
    path.write_text(json.dumps(schema), encoding="utf-8")


def test_identical_contracts_are_not_breaking(tmp_path: Path) -> None:
    base = copy_contracts(tmp_path, "base")
    lines = list(compat.check(base, PACKAGE, None))
    assert "no breaking changes" in lines[-1]


def test_missing_baseline_passes_with_a_note(tmp_path: Path) -> None:
    lines = list(compat.check(tmp_path, PACKAGE, None))
    assert lines == ["no baseline: the comparison base has no openapi.json; nothing to compare"]


def test_removed_enum_value_needs_a_version_bump(tmp_path: Path) -> None:
    base = copy_contracts(tmp_path, "base")
    head = copy_contracts(tmp_path, "head")
    narrow_enum(head, "job_state", "failed")
    with pytest.raises(compat.Breaking, match="without a VERSION bump"):
        list(compat.check(base, head, None))
    (head / "VERSION").write_text("0.2.0-draft\n", encoding="utf-8")
    lines = list(compat.check(base, head, None))
    assert "breaking: enum job_state lost values ['failed']" in lines
    assert lines[-1].startswith("ok VERSION bumped") or "bumped" in lines[-1]


def test_added_enum_value_is_additive(tmp_path: Path) -> None:
    base = copy_contracts(tmp_path, "base")
    head = copy_contracts(tmp_path, "head")
    path = head / "schemas" / validate.ENUMS_SCHEMA
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["$defs"]["stage"]["enum"].append("future_stage")
    path.write_text(json.dumps(schema), encoding="utf-8")
    assert "no breaking changes" in list(compat.check(base, head, None))[-1]


def test_removed_enum_definition_and_schema_file_are_breaking(tmp_path: Path) -> None:
    base = copy_contracts(tmp_path, "base")
    head = copy_contracts(tmp_path, "head")
    path = head / "schemas" / validate.ENUMS_SCHEMA
    schema = json.loads(path.read_text(encoding="utf-8"))
    del schema["$defs"]["chunk_disposition"]
    path.write_text(json.dumps(schema), encoding="utf-8")
    (head / "schemas" / "capture-chunk.schema.json").unlink()
    findings = [*compat.schema_file_findings(base, head), *compat.enum_findings(base, head)]
    assert findings == [
        "schema file capture-chunk.schema.json was removed",
        "enum chunk_disposition was removed",
    ]


def test_oasdiff_output_parsing_keeps_errors_only() -> None:
    output = json.dumps(
        [
            {"id": "x", "text": "warn", "level": 2, "operation": "GET", "path": "/a"},
            {"id": "y", "text": "removed", "level": 3, "operation": "POST", "path": "/b"},
        ]
    )
    assert list(compat.parse_oasdiff(output)) == ["oasdiff y: POST /b: removed"]
    assert list(compat.parse_oasdiff("")) == []
    assert list(compat.parse_oasdiff("  \n")) == []


def test_cli_reports_and_exits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = copy_contracts(tmp_path, "base")
    head = copy_contracts(tmp_path, "head")
    narrow_enum(head, "relation", "mixed")
    assert compat.main(["--base", str(base), "--head", str(head)]) == 1
    captured = capsys.readouterr()
    assert "breaking: enum relation lost values ['mixed']" in captured.out
    assert "without a VERSION bump" in captured.err
    assert compat.main(["--base", str(base), "--head", str(base)]) == 0
