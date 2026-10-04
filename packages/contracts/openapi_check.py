"""Validate openapi.json and apply the two contract lint rules.

Run from the repository root with the backend uv project and its ``contracts`` group:

    uv run --project backend --frozen --group contracts python packages/contracts/openapi_check.py

Checks, in order:

1. ``openapi.json`` is a valid OpenAPI 3.1 document (``openapi-spec-validator``), with the
   relative ``schemas/*.schema.json`` references resolved from disk.
2. Every ``$ref`` to a file resolves to an existing schema in ``schemas/`` and every
   schema file is referenced from the document (nothing is published that the
   document does not know about).
3. ``info.version`` equals ``VERSION``.
4. Every 4xx and 5xx response carries ``application/json`` content whose schema is
   ``#/components/schemas/Error``, which in turn is a reference to
   ``schemas/error.schema.json`` (the "all errors use the error shape" rule), and every
   response declares the ``X-Request-Id`` header.
5. Every ``enum`` in the document and in every schema file sits next to ``"type": "string"``
   (the "no untyped enums" rule), so a client can give each one a typed UNKNOWN fallback.

``.spectral.yaml`` expresses rules 4 and 5 for spectral as well; this module is the
dependency-light version that Backend CI and the Contract checks job both run.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from openapi_spec_validator import validate as validate_openapi
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError

ROOT = Path(__file__).resolve().parent
OPENAPI = ROOT / "openapi.json"
SCHEMAS = ROOT / "schemas"
VERSION_FILE = ROOT / "VERSION"
ERROR_REF = "schemas/error.schema.json"
ERROR_COMPONENT = "#/components/schemas/Error"
REQUEST_ID_HEADER = "X-Request-Id"


class Invalid(ValueError):
    """The OpenAPI document violates the contract rules."""


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk(node: Any, location: str) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        yield location, node
        for key, value in node.items():
            yield from _walk(value, f"{location}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{location}[{index}]")


def check_spec(document: dict[str, Any], base_uri: str) -> None:
    try:
        validate_openapi(document, base_uri=base_uri)
    except OpenAPIValidationError as error:
        raise Invalid(f"openapi.json: {error.message}") from error


def check_file_refs(document: dict[str, Any], schema_dir: Path = SCHEMAS) -> set[str]:
    referenced: set[str] = set()
    for location, node in _walk(document, "#"):
        ref = node.get("$ref")
        if not isinstance(ref, str) or ref.startswith("#"):
            continue
        file_name, _, _ = ref.partition("#")
        if not file_name.startswith("schemas/"):
            raise Invalid(f"{location}: $ref {ref!r} must point into schemas/")
        if not (ROOT / file_name).is_file():
            raise Invalid(f"{location}: $ref {ref!r} does not exist")
        referenced.add(file_name)
    published = {f"schemas/{path.name}" for path in schema_dir.glob("*.schema.json")}
    unreferenced = sorted(published - referenced)
    # common.schema.json and enums.schema.json are $defs-only and reached through other files.
    direct = sorted(name for name in unreferenced if name not in _defs_only(schema_dir))
    if direct:
        raise Invalid(f"openapi.json does not reference {direct}")
    return referenced


def _defs_only(schema_dir: Path) -> set[str]:
    names: set[str] = set()
    for path in schema_dir.glob("*.schema.json"):
        schema = _load(path)
        if "type" not in schema and "$ref" not in schema and "$defs" in schema:
            names.add(f"schemas/{path.name}")
    return names


def check_version(document: dict[str, Any], version_file: Path = VERSION_FILE) -> str:
    version = version_file.read_text(encoding="utf-8").strip()
    declared = document.get("info", {}).get("version")
    if declared != version:
        raise Invalid(f"info.version {declared!r} does not equal VERSION {version!r}")
    return version


def check_error_responses(document: dict[str, Any]) -> int:
    """Rule: every 4xx/5xx body is the shared error shape; every response has X-Request-Id."""
    checked = 0
    components = document.get("components", {}).get("responses", {})
    error_alias = document.get("components", {}).get("schemas", {}).get("Error")
    if error_alias != {"$ref": ERROR_REF}:
        raise Invalid(f"components.schemas.Error must be {{'$ref': {ERROR_REF!r}}}")
    for path, item in document.get("paths", {}).items():
        for method, operation in item.items():
            if method == "parameters":
                continue
            for status, response in operation.get("responses", {}).items():
                where = f"{method.upper()} {path} {status}"
                if "$ref" in response:
                    name = response["$ref"].rsplit("/", 1)[-1]
                    if name not in components:
                        raise Invalid(f"{where}: unknown response component {name!r}")
                    response = components[name]
                    where = f"{where} (#/components/responses/{name})"
                headers = response.get("headers", {})
                if REQUEST_ID_HEADER not in headers:
                    raise Invalid(f"{where}: missing the {REQUEST_ID_HEADER} header")
                if not status.startswith(("4", "5")):
                    continue
                schema = response.get("content", {}).get("application/json", {}).get("schema")
                if schema != {"$ref": ERROR_COMPONENT}:
                    raise Invalid(f"{where}: error body must be {{'$ref': {ERROR_COMPONENT!r}}}")
                checked += 1
    return checked


def check_typed_enums(document: dict[str, Any], schema_dir: Path = SCHEMAS) -> int:
    """Rule: an enum is always a string enum with an explicit type."""
    checked = 0
    sources = [("openapi.json", document)]
    sources += [(path.name, _load(path)) for path in sorted(schema_dir.glob("*.schema.json"))]
    for name, source in sources:
        for location, node in _walk(source, "#"):
            if "enum" not in node:
                continue
            if node.get("type") != "string":
                raise Invalid(f'{name}{location}: enum without "type": "string"')
            values = node["enum"]
            if not values or not all(isinstance(v, str) for v in values):
                raise Invalid(f"{name}{location}: enum must be a nonempty list of strings")
            checked += 1
    return checked


def check_all(openapi: Path = OPENAPI) -> Iterator[str]:
    document = _load(openapi)
    check_spec(document, openapi.as_uri())
    yield "openapi 3.1 document valid"
    yield f"version {check_version(document)}"
    referenced = check_file_refs(document)
    yield f"schema files referenced: {len(referenced)}"
    yield f"error responses using the shared shape: {check_error_responses(document)}"
    yield f"typed enums: {check_typed_enums(document)}"


def main() -> int:
    try:
        for line in check_all():
            print(f"ok {line}")
    except Invalid as error:
        print(f"error {error}", file=sys.stderr)
        return 1
    print("OpenAPI document validated against the contract rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
