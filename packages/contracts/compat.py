"""Fail a breaking contract change that does not bump VERSION.

Run by the Contract checks workflow (and by hand) with a copy of ``packages/contracts`` from
the comparison base, usually ``origin/main``:

    git archive origin/main packages/contracts | tar -x -C /tmp/base
    uv run --project backend --frozen python packages/contracts/compat.py \
        --base /tmp/base/packages/contracts --oasdiff /path/to/oasdiff

Two detectors run and their findings are combined:

* ``oasdiff breaking`` (pinned binary) on ``openapi.json``: removed paths or operations,
  request enum values removed, response properties removed or made optional, type changes.
* A stdlib diff of ``schemas/enums.schema.json`` and the schema file list: a removed enum
  ``$def``, a removed enum value (response-side narrowing, which oasdiff does not count)
  or a removed schema file is breaking by the README rule.

If anything is breaking, ``VERSION`` must differ from the base; otherwise exit 1 with the
findings. A base without ``openapi.json`` (the contract landing for the first time) reports
"no baseline" and passes. Additive changes never need a bump.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENUMS = "enums.schema.json"


class Breaking(ValueError):
    """A breaking change was found without a version bump."""


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def version_of(contracts: Path) -> str:
    return (contracts / "VERSION").read_text(encoding="utf-8").strip()


def enum_findings(base: Path, head: Path) -> Iterator[str]:
    """Removed enum definitions or values between two contracts directories."""
    base_file = base / "schemas" / ENUMS
    if not base_file.is_file():
        return
    base_defs = _read(base_file).get("$defs", {})
    head_defs = _read(head / "schemas" / ENUMS).get("$defs", {})
    for name, definition in sorted(base_defs.items()):
        if name not in head_defs:
            yield f"enum {name} was removed"
            continue
        removed = [
            v for v in definition.get("enum", []) if v not in head_defs[name].get("enum", [])
        ]
        if removed:
            yield f"enum {name} lost values {removed}"


def schema_file_findings(base: Path, head: Path) -> Iterator[str]:
    base_files = {p.name for p in (base / "schemas").glob("*.schema.json")}
    head_files = {p.name for p in (head / "schemas").glob("*.schema.json")}
    for name in sorted(base_files - head_files):
        yield f"schema file {name} was removed"


def oasdiff_findings(binary: Path, base_spec: Path, head_spec: Path) -> Iterator[str]:
    """Lines of ``oasdiff breaking`` output that are errors, or nothing."""
    result = subprocess.run(  # noqa: S603 - fixed binary and two repository paths
        [str(binary), "breaking", str(base_spec), str(head_spec), "--format", "json"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"oasdiff failed ({result.returncode}): {result.stderr.strip()}")
    yield from parse_oasdiff(result.stdout)


def parse_oasdiff(output: str) -> Iterator[str]:
    """Turn ``oasdiff breaking --format json`` output into finding lines (errors only)."""
    output = output.strip()
    if not output:
        return
    changes = json.loads(output)
    for change in changes:
        if change.get("level") == 3:  # oasdiff: 3 = error, 2 = warning, 1 = info
            operation = change.get("operation", "")
            path = change.get("path", "")
            yield f"oasdiff {change.get('id')}: {operation} {path}: {change.get('text')}"


def check(base: Path, head: Path, oasdiff: Path | None) -> Iterator[str]:
    """Yield report lines; raise Breaking when a bump is required and missing."""
    base_spec = base / "openapi.json"
    head_spec = head / "openapi.json"
    if not base_spec.is_file():
        yield "no baseline: the comparison base has no openapi.json; nothing to compare"
        return
    findings = [*schema_file_findings(base, head), *enum_findings(base, head)]
    if oasdiff is not None:
        findings.extend(oasdiff_findings(oasdiff, base_spec, head_spec))
    else:
        yield "oasdiff binary not given; only the enum and schema-file rules ran"
    base_version = version_of(base)
    head_version = version_of(head)
    yield f"base version {base_version}, head version {head_version}"
    if not findings:
        yield "no breaking changes"
        return
    for finding in findings:
        yield f"breaking: {finding}"
    if head_version == base_version:
        raise Breaking(
            f"{len(findings)} breaking change(s) without a VERSION bump "
            f"(still {head_version}); bump packages/contracts/VERSION in this PR"
        )
    yield f"VERSION bumped from {base_version} to {head_version}: breaking changes allowed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", required=True, type=Path, help="base packages/contracts dir")
    parser.add_argument("--head", type=Path, default=ROOT, help="head packages/contracts dir")
    parser.add_argument("--oasdiff", type=Path, default=None, help="path to the oasdiff binary")
    args = parser.parse_args(argv)
    try:
        for line in check(args.base, args.head, args.oasdiff):
            print(f"ok {line}" if not line.startswith("breaking:") else line)
    except Breaking as error:
        print(f"error {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
