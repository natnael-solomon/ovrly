"""Summarize Android JaCoCo line coverage, enforce floors and compare with main.

Floors (#13): at least 90% of lines in `capture/`, `share/` and contract parsing
(`contract/`), and overall line coverage may fall by at most one percentage point against
the last measurement of main. Decisions use exact line counts, never rounded percentages.
A missing baseline is reported as unavailable, never as a pass or as zero.
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

PACKAGE_FLOORS = {
    "app/ovrly/capture": 90,
    "app/ovrly/share": 90,
    "app/ovrly/contract": 90,
}
MAX_DROP = 1


class InvalidCoverage(ValueError):
    pass


def line_counter(element):
    """(covered, total) lines from the direct LINE counter of a JaCoCo element."""
    for counter in element.findall("counter"):
        if counter.get("type") == "LINE":
            try:
                missed, covered = int(counter.get("missed")), int(counter.get("covered"))
            except (TypeError, ValueError) as error:
                raise InvalidCoverage("Malformed LINE counter") from error
            if missed < 0 or covered < 0:
                raise InvalidCoverage("Negative LINE counter")
            return covered, covered + missed
    return 0, 0


def read_report(path):
    """Return {'total': (covered, lines), 'packages': {name: (covered, lines)}}."""
    if not Path(path).is_file():
        raise InvalidCoverage(f"JaCoCo report {path} is missing")
    root = ET.parse(path).getroot()
    if root.tag != "report":
        raise InvalidCoverage("Not a JaCoCo XML report")
    packages = {package.get("name"): line_counter(package) for package in root.iter("package")}
    total = line_counter(root)
    if total[1] == 0:
        raise InvalidCoverage("The report has no executable lines")
    if (sum(c for c, _ in packages.values()), sum(t for _, t in packages.values())) != total:
        raise InvalidCoverage("Package counters do not add up to the report total")
    return {"total": total, "packages": packages}


def scope(report, prefix):
    covered = lines = 0
    for name, (c, t) in report["packages"].items():
        if name == prefix or name.startswith(prefix + "/"):
            covered += c
            lines += t
    return covered, lines


def percent(covered, lines):
    return Fraction(100 * covered, lines)


def read_baseline(path):
    if path is None or not Path(path).is_file():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    covered, lines = data.get("covered"), data.get("lines")
    if type(covered) is not int or type(lines) is not int or not 0 <= covered <= lines or not lines:
        raise InvalidCoverage("Baseline has invalid line counts")
    if not isinstance(data.get("sha"), str):
        raise InvalidCoverage("Baseline does not name the main commit it measured")
    return data


def assess(report, baseline, label, gate=True):
    """Return (markdown summary, failures)."""
    covered, lines = report["total"]
    current = percent(covered, lines)
    rows = [f"| Overall | {covered}/{lines} | {float(current):.2f}% |"]
    failures = []
    for prefix, floor in PACKAGE_FLOORS.items():
        c, t = scope(report, prefix)
        if t == 0:
            failures.append(f"{prefix}: no executable lines measured")
            rows.append(f"| `{prefix}` | none | FAIL: no lines measured |")
            continue
        value = percent(c, t)
        passed = value >= floor
        rows.append(
            f"| `{prefix}` | {c}/{t} ({float(value):.2f}%) | "
            f"{'PASS' if passed else 'FAIL'} >= {floor}% |"
        )
        if not passed:
            failures.append(f"{prefix}: line coverage is below {floor}%")
    if baseline is None:
        comparison = (
            "No measurement of main is available yet, so the regression comparison is "
            "**not available**; this is not a passing or zero-percent result. The first "
            "main push with this job records the baseline."
        )
    else:
        before = percent(baseline["covered"], baseline["lines"])
        drop = before - current
        if drop > MAX_DROP:
            failures.append("Overall line coverage dropped by more than 1 percentage point")
        comparison = (
            f"Main `{baseline['sha']}`: {float(before):.2f}%; change: "
            f"{float(current - before):+.2f} percentage points. Regression gate: "
            f"**{'FAIL' if drop > MAX_DROP else 'PASS'}** (maximum drop: 1 point)."
        )
    if not gate:
        comparison = "Reported only; floors and the main comparison gate the API 34 job."
        failures = []
    text = (
        f"### Android coverage ({label})\n\nUnit and instrumented tests, JaCoCo line "
        "coverage. Exclusions: generated code (R, BuildConfig, Room `_Impl`, serializers, "
        "Compose singletons), Compose preview files and gallery fixtures; see "
        "`android/app/build.gradle.kts`.\n\n| Scope | Lines | Result |\n|---|---|---|\n"
        + "\n".join(rows) + "\n\n" + comparison + "\n"
    )
    return text, failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--sha", required=True, help="Commit measured by this run")
    parser.add_argument("--gate", action="store_true", help="Enforce floors and comparison")
    args = parser.parse_args(argv)
    try:
        report = read_report(args.report)
        baseline = read_baseline(args.baseline) if args.gate else None
    except (InvalidCoverage, ET.ParseError, json.JSONDecodeError) as error:
        print(f"::error::Android coverage could not be assessed: {error}")
        return 1
    text, failures = assess(report, baseline, args.label, args.gate)
    args.output.mkdir(parents=True, exist_ok=True)
    covered, lines = report["total"]
    (args.output / "coverage.json").write_text(json.dumps(
        {"sha": args.sha, "covered": covered, "lines": lines}, indent=2) + "\n",
        encoding="utf-8")
    (args.output / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(text)
    for failure in failures:
        print(f"::error::{failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
