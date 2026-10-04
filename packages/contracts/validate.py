"""Validate the voice-actions contract schemas and fixtures without third-party packages.

Supports only the JSON Schema 2020-12 subset the contracts use and fails closed on
anything else. Run from any directory:

    python packages/contracts/validate.py

Exit status is nonzero when a schema uses an unsupported feature, a fixture is
malformed, a positive fixture fails validation or a negative fixture passes.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "fixtures"
VERSION_FILE = ROOT / "VERSION"

REQUEST_SCHEMA = "voice-action-request.schema.json"
RESPONSE_SCHEMA = "voice-action-response.schema.json"
ERROR_SCHEMA = "error.schema.json"

ALLOWED_ACTIONS = ("open_check", "save_report", "queue_cancel", "queue_retry", "queue_continue")
ERROR_CODES = (
    "VOICE_ACTION_UNSUPPORTED",
    "VOICE_TARGET_NOT_FOUND",
    "VOICE_TARGET_NOT_OWNED",
    "VOICE_ACTION_INVALID_STATE",
)

KEYWORDS = {
    "$schema",
    "$comment",
    "$defs",
    "$ref",
    "title",
    "description",
    "type",
    "const",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "oneOf",
    "allOf",
    "not",
    "minLength",
    "maxLength",
    "pattern",
}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
EXPECTATIONS = {"valid", "invalid", "unknown-enum"}
FIXTURE_KEYS = {"synthetic", "description", "expect", "request", "response"}


class Invalid(ValueError):
    """A schema, payload or fixture violates the contract."""


def require(condition: bool, location: str, message: str) -> None:
    if not condition:
        raise Invalid(f"{location}: {message}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, key, "duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise Invalid(f"non-finite JSON number: {value}")


def parse(text: str, location: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as error:
        raise Invalid(f"{location}: {error}") from error


def read_json(path: Path) -> Any:
    return parse(path.read_text(encoding="utf-8"), str(path))


def json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return bool(left == right)


class Validator:
    """Loads the schema files once and validates payloads against them."""

    def __init__(self, schema_dir: Path = SCHEMAS) -> None:
        self.schema_dir = schema_dir
        self.schemas: dict[str, dict[str, Any]] = {}
        for path in sorted(schema_dir.glob("*.schema.json")):
            schema = read_json(path)
            self.schemas[path.name] = schema
        require(bool(self.schemas), str(schema_dir), "no *.schema.json files found")
        for name, schema in self.schemas.items():
            self.check_schema(schema, name, name)

    # Schema self-check -------------------------------------------------

    def check_schema(self, schema: Any, file: str, location: str) -> None:
        """Fail closed on schema features this validator does not implement."""
        require(isinstance(schema, dict), location, "expected a schema object")
        unknown = sorted(schema.keys() - KEYWORDS)
        require(not unknown, location, f"unsupported keywords: {unknown}")
        if "$schema" in schema:
            require(
                schema["$schema"] == "https://json-schema.org/draft/2020-12/schema",
                location,
                "schemas must declare draft 2020-12",
            )
        if "type" in schema:
            require(
                isinstance(schema["type"], str) and schema["type"] in TYPES,
                location,
                "type must be a single supported type name",
            )
        for key in ("minLength", "maxLength"):
            if key in schema:
                require(
                    type(schema[key]) is int and schema[key] >= 0,
                    location,
                    f"{key} must be a nonnegative integer",
                )
        if "additionalProperties" in schema:
            require(
                type(schema["additionalProperties"]) is bool,
                location,
                "additionalProperties must be boolean",
            )
        if "enum" in schema:
            require(
                isinstance(schema["enum"], list)
                and bool(schema["enum"])
                and all(isinstance(v, str) for v in schema["enum"]),
                location,
                "enum must be a nonempty array of strings",
            )
        if "required" in schema:
            require(
                isinstance(schema["required"], list)
                and all(isinstance(k, str) for k in schema["required"]),
                location,
                "required must be an array of property names",
            )
        if "pattern" in schema:
            require(isinstance(schema["pattern"], str), location, "pattern must be a string")
            try:
                re.compile(schema["pattern"])
            except re.error as error:
                raise Invalid(f"{location}: invalid pattern: {error}") from error
        if "$ref" in schema:
            self.resolve(schema["$ref"], file, location)
        properties = schema.get("properties", {})
        require(isinstance(properties, dict), location, "properties must be an object")
        for name, child in properties.items():
            self.check_schema(child, file, f"{location}.properties.{name}")
        defs = schema.get("$defs", {})
        require(isinstance(defs, dict), location, "$defs must be an object")
        for name, child in defs.items():
            self.check_schema(child, file, f"{location}.$defs.{name}")
        for key in ("oneOf", "allOf"):
            if key in schema:
                require(
                    isinstance(schema[key], list) and bool(schema[key]),
                    location,
                    f"{key} must be a nonempty array",
                )
                for index, child in enumerate(schema[key]):
                    self.check_schema(child, file, f"{location}.{key}[{index}]")
        if "not" in schema:
            self.check_schema(schema["not"], file, f"{location}.not")

    def resolve(self, ref: str, file: str, location: str) -> tuple[dict[str, Any], str]:
        """Resolve `#/$defs/x`, `other.schema.json` or `other.schema.json#/$defs/x`."""
        require(isinstance(ref, str), location, "$ref must be a string")
        target_file, _, fragment = ref.partition("#")
        target_file = target_file or file
        require(target_file in self.schemas, location, f"unknown schema reference {ref!r}")
        node: Any = self.schemas[target_file]
        if fragment:
            require(fragment.startswith("/"), location, f"unsupported fragment in {ref!r}")
            for part in fragment[1:].split("/"):
                require(
                    isinstance(node, dict) and part in node,
                    location,
                    f"unresolvable reference {ref!r}",
                )
                node = node[part]
        require(isinstance(node, dict), location, f"reference {ref!r} is not a schema")
        return node, target_file

    # Payload validation ------------------------------------------------

    def validate(self, value: Any, schema_file: str, *, relax_enums: bool = False) -> None:
        """Raise Invalid when `value` does not satisfy the named schema.

        `relax_enums` ignores `enum` and `const` so a client can check whether a payload
        with values from a newer contract version is still structurally readable. It is
        meant for responses; requests are always validated strictly by the server.
        """
        require(schema_file in self.schemas, schema_file, "unknown schema")
        self._validate(value, self.schemas[schema_file], schema_file, "$", relax_enums)

    def _validate(
        self, value: Any, schema: dict[str, Any], file: str, location: str, relax: bool
    ) -> None:
        if "$ref" in schema:
            target, target_file = self.resolve(schema["$ref"], file, location)
            self._validate(value, target, target_file, location, relax)
        if "type" in schema:
            require(
                self._matches_type(value, schema["type"]), location, f"expected {schema['type']}"
            )
        if not relax:
            if "const" in schema:
                require(
                    json_equal(value, schema["const"]), location, f"expected {schema['const']!r}"
                )
            if "enum" in schema:
                require(
                    any(json_equal(value, v) for v in schema["enum"]),
                    location,
                    f"{value!r} is not one of {schema['enum']}",
                )
        if isinstance(value, dict):
            missing = sorted(set(schema.get("required", [])) - value.keys())
            require(not missing, location, f"missing required fields: {missing}")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extra = sorted(value.keys() - properties.keys())
                require(not extra, location, f"unknown fields: {extra}")
            for name, item in value.items():
                if name in properties:
                    self._validate(item, properties[name], file, f"{location}.{name}", relax)
        if isinstance(value, str):
            require(len(value) >= schema.get("minLength", 0), location, "string too short")
            require(len(value) <= schema.get("maxLength", len(value)), location, "string too long")
            if "pattern" in schema:
                require(
                    re.search(schema["pattern"], value) is not None, location, "pattern mismatch"
                )
        for index, child in enumerate(schema.get("allOf", [])):
            self._validate(value, child, file, f"{location}<allOf[{index}]>", relax)
        if "oneOf" in schema:
            self._one_of(value, schema["oneOf"], file, location, relax)
        if "not" in schema:
            try:
                self._validate(value, schema["not"], file, location, relax)
            except Invalid:
                return
            raise Invalid(f"{location}: matched a forbidden shape")

    def _one_of(
        self, value: Any, branches: list[dict[str, Any]], file: str, location: str, relax: bool
    ) -> None:
        matched = 0
        reasons: list[str] = []
        for index, branch in enumerate(branches):
            try:
                self._validate(value, branch, file, f"{location}<oneOf[{index}]>", relax)
            except Invalid as error:
                reasons.append(str(error))
            else:
                matched += 1
        require(matched != 0, location, "no oneOf branch matched: " + "; ".join(reasons))
        require(matched == 1, location, f"{matched} oneOf branches matched; exactly one is allowed")

    @staticmethod
    def _matches_type(value: Any, type_name: str) -> bool:
        if type_name == "object":
            return isinstance(value, dict)
        if type_name == "array":
            return isinstance(value, list)
        if type_name == "string":
            return isinstance(value, str)
        if type_name == "boolean":
            return type(value) is bool
        if type_name == "null":
            return value is None
        if type_name == "integer":
            return type(value) is int
        return type(value) in (int, float)


# Fixtures --------------------------------------------------------------


def fixture_paths(fixture_dir: Path = FIXTURES / "voice-actions") -> list[Path]:
    return sorted(fixture_dir.glob("*.json"))


def check_fixture(validator: Validator, path: Path) -> None:
    """Validate one fixture file, including its own expectations."""
    location = str(path)
    fixture = read_json(path)
    require(isinstance(fixture, dict), location, "fixture must be an object")
    unknown = sorted(fixture.keys() - FIXTURE_KEYS)
    require(not unknown, location, f"unknown fixture keys: {unknown}")
    require(fixture.get("synthetic") is True, location, "fixtures must declare synthetic: true")
    description = fixture.get("description")
    require(
        isinstance(description, str) and "synthetic" in description.lower(),
        location,
        "description must be a string that says the data is synthetic",
    )
    expect = fixture.get("expect")
    require(isinstance(expect, dict), location, "expect must be an object")
    allowed_expect = {"request", "response", "error_code"}
    require(
        not (expect.keys() - allowed_expect), location, f"expect keys must be in {allowed_expect}"
    )
    for part, schema in (("request", REQUEST_SCHEMA), ("response", RESPONSE_SCHEMA)):
        present = part in fixture
        expected = expect.get(part)
        require(
            present == (expected is not None),
            location,
            f"{part} payload and expect.{part} must be present together",
        )
        if not present:
            continue
        require(expected in EXPECTATIONS, location, f"expect.{part} must be one of {EXPECTATIONS}")
        _check_expectation(validator, fixture[part], schema, expected, f"{location} {part}")
    require("request" in fixture, location, "every fixture carries a request")
    _check_consistency(fixture, expect, location)


def _check_expectation(
    validator: Validator, payload: Any, schema: str, expected: str, location: str
) -> None:
    strict_error: str | None = None
    try:
        validator.validate(payload, schema)
    except Invalid as error:
        strict_error = str(error)
    if expected == "valid":
        require(strict_error is None, location, f"expected valid but: {strict_error}")
    elif expected == "invalid":
        require(strict_error is not None, location, "expected the payload to fail validation")
    else:
        require(schema == RESPONSE_SCHEMA, location, "unknown-enum applies to responses only")
        require(strict_error is not None, location, "unknown-enum payload passed strict validation")
        validator.validate(payload, schema, relax_enums=True)


def _check_consistency(fixture: dict[str, Any], expect: dict[str, Any], location: str) -> None:
    request = fixture["request"]
    response = fixture.get("response")
    error_code = expect.get("error_code")
    if response is None:
        require(error_code is None, location, "expect.error_code needs a response")
        return
    require(isinstance(response, dict), location, "response must be an object")
    require(
        response.get("request_id") == request.get("request_id"),
        location,
        "response.request_id must echo the request",
    )
    require(response.get("action") == request.get("action"), location, "response.action must echo")
    if "target" in request and "target" in response:
        require(json_equal(response["target"], request["target"]), location, "target echo differs")
    error = response.get("error")
    if isinstance(error, dict) and "request_id" in error:
        require(
            error["request_id"] == response.get("request_id"),
            location,
            "error.request_id must echo the response request_id",
        )
    if error_code is not None:
        require(
            isinstance(error, dict) and error.get("code") == error_code,
            location,
            f"expected error code {error_code}",
        )
        require(error_code in ERROR_CODES, location, f"unknown expected error code {error_code}")
    elif expect.get("response") == "valid":
        require(error is None, location, "denied responses must declare expect.error_code")


def check_version(path: Path = VERSION_FILE) -> str:
    version = path.read_text(encoding="utf-8").strip()
    require(
        re.fullmatch(r"0\.\d+\.\d+-draft", version) is not None,
        str(path),
        "the contract is pre-1.0 and must read like 0.1.0-draft until the allowlist is final",
    )
    return version


def check_all() -> Iterator[str]:
    """Yield one line per checked artifact; raise Invalid on the first problem."""
    yield f"version {check_version()}"
    validator = Validator()
    for name in sorted(validator.schemas):
        yield f"schema {name}"
    paths = fixture_paths()
    require(bool(paths), str(FIXTURES), "no fixtures found")
    for path in paths:
        check_fixture(validator, path)
        yield f"fixture {path.relative_to(ROOT).as_posix()}"
    accepted = {f"{action.replace('_', '-')}-accepted.json" for action in ALLOWED_ACTIONS} - {
        p.name for p in paths
    }
    require(not accepted, str(FIXTURES), f"missing accepted fixtures: {sorted(accepted)}")


def main() -> int:
    try:
        for line in check_all():
            print(f"ok {line}")
    except Invalid as error:
        print(f"error {error}", file=sys.stderr)
        return 1
    print("Synthetic fixtures validated. This proves the contract shape, not an implementation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
