"""Enforce line-coverage floors without rounding or inventing a missing baseline."""

import json
from fractions import Fraction
from pathlib import PurePosixPath

MODULE_FLOORS = {
    "services/worker": 90,
    "services/api/auth": 90,
    "services/contracts": 90,
}


class InvalidCoverage(ValueError):
    pass


def line_counts(summary):
    if not isinstance(summary, dict):
        raise InvalidCoverage("Coverage summary must be an object")
    covered = summary.get("covered_lines")
    statements = summary.get("num_statements")
    if (type(covered) is not int or type(statements) is not int
            or not 0 <= covered <= statements):
        raise InvalidCoverage("Invalid covered/statement counts")
    return covered, statements


def read_report(path, source_root):
    report = json.loads(path.read_text(encoding="utf-8"))
    files = report.get("files")
    if not isinstance(files, dict) or not files:
        raise InvalidCoverage("Coverage report has no source files")
    expected = {p.relative_to(source_root).as_posix()
                for p in (source_root / "services").rglob("*.py")}
    if set(files) != expected:
        raise InvalidCoverage("Coverage file inventory does not match services/*.py")
    covered = statements = 0
    for name, data in files.items():
        path_name = PurePosixPath(name)
        if path_name.is_absolute() or ".." in path_name.parts:
            raise InvalidCoverage("Coverage paths must be source-relative")
        c, s = line_counts(data.get("summary"))
        covered += c
        statements += s
    if statements == 0 or line_counts(report.get("totals")) != (covered, statements):
        raise InvalidCoverage("Coverage totals are empty or inconsistent")
    return report


def percent(covered, statements):
    if not statements:
        raise InvalidCoverage("Coverage denominator is zero")
    return Fraction(100 * covered, statements)


def assess(current, baseline, baseline_sha):
    covered, statements = line_counts(current["totals"])
    current_percent = percent(covered, statements)
    rows = [f"| Overall backend | {covered}/{statements} | {float(current_percent):.2f}% |"]
    failures = []
    for module, floor in MODULE_FLOORS.items():
        summaries = [data["summary"] for name, data in current["files"].items()
                     if name == module + ".py" or name.startswith(module + "/")]
        if not summaries:
            rows.append(f"| `{module}` | Not implemented; not evaluated | {floor}% future floor |")
            continue
        counts = [line_counts(summary) for summary in summaries]
        module_covered = sum(c for c, _ in counts)
        module_statements = sum(s for _, s in counts)
        if module_statements == 0:
            failures.append(f"{module}: no executable lines; cannot establish the floor")
            rows.append(f"| `{module}` | No executable lines | FAIL |")
            continue
        value = percent(module_covered, module_statements)
        passed = value >= floor
        rows.append(
            f"| `{module}` | {module_covered}/{module_statements} "
            f"({float(value):.2f}%) | {'PASS' if passed else 'FAIL'} >= {floor}% |"
        )
        if not passed:
            failures.append(f"{module}: line coverage is below {floor}%")
    if baseline is None:
        comparison = (
            f"Baseline `{baseline_sha}` has no backend project. Regression comparison is "
            "**not available**; this is not a zero-percent or passing regression result. "
            "The first main commit with a backend establishes the baseline."
        )
    else:
        baseline_percent = percent(*line_counts(baseline["totals"]))
        drop = baseline_percent - current_percent
        if drop > 1:
            failures.append("Overall backend line coverage dropped by more than 1 percentage point")
        comparison = (
            f"Measured main `{baseline_sha}`: {float(baseline_percent):.2f}%; "
            f"change: {float(current_percent - baseline_percent):+.2f} percentage points. "
            f"Regression gate: **{'FAIL' if drop > 1 else 'PASS'}** (maximum drop: 1 point). "
            "Decisions use exact line-count fractions, not displayed rounded percentages."
        )
    summary = (
        "### Backend coverage\n\n| Scope | Measured lines | Result |\n|---|---|---|\n"
        + "\n".join(rows) + "\n\n" + comparison
        + "\n\nAndroid unit/instrumented coverage and its capture/share floors are not "
        "measured by this backend job. Future auth/contracts paths must be aligned "
        "with the actual implementations before those portions of #13 can close.\n"
    )
    return summary, failures
