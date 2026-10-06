import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from android_coverage import InvalidCoverage, assess, main, read_baseline, read_report

HEADER = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">')


def line(covered, missed):
    return f'<counter type="LINE" missed="{missed}" covered="{covered}"/>'


def report(packages):
    body = "".join(
        f'<package name="{name}"><class name="{name}/X"/>'
        f'<counter type="INSTRUCTION" missed="1" covered="1"/>{line(c, t - c)}</package>'
        for name, (c, t) in packages.items()
    )
    covered = sum(c for c, _ in packages.values())
    total = sum(t for _, t in packages.values())
    return f'{HEADER}<report name="app">{body}{line(covered, total - covered)}</report>'


PASSING = {
    "app/ovrly/capture": (95, 100),
    "app/ovrly/share": (90, 100),
    "app/ovrly/contract": (180, 200),
    "app/ovrly/ui": (10, 100),
}


class CoverageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, packages, name="report.xml"):
        path = self.root / name
        path.write_text(report(packages), encoding="utf-8")
        return path

    def test_floors_pass_at_exactly_ninety_percent(self):
        text, failures = assess(read_report(self.write(PASSING)), None, "API 34")
        self.assertEqual([], failures)
        self.assertIn("| `app/ovrly/share` | 90/100 (90.00%) | PASS >= 90% |", text)
        self.assertIn("**not available**", text)

    def test_floor_below_ninety_fails_without_rounding(self):
        packages = {**PASSING, "app/ovrly/share": (899, 1000)}
        _, failures = assess(read_report(self.write(packages)), None, "API 34")
        self.assertEqual(["app/ovrly/share: line coverage is below 90%"], failures)

    def test_subpackages_count_towards_their_floor(self):
        packages = {**PASSING, "app/ovrly/contract": (90, 100),
                    "app/ovrly/contract/wire": (0, 100), "app/ovrly/contractor": (0, 100)}
        _, failures = assess(read_report(self.write(packages)), None, "API 34")
        self.assertEqual(["app/ovrly/contract: line coverage is below 90%"], failures)

    def test_missing_floor_package_fails(self):
        packages = {k: v for k, v in PASSING.items() if k != "app/ovrly/capture"}
        _, failures = assess(read_report(self.write(packages)), None, "API 34")
        self.assertEqual(["app/ovrly/capture: no executable lines measured"], failures)

    def test_overall_drop_over_one_point_fails(self):
        current = read_report(self.write(PASSING))  # 375/500 = 75%
        _, ok = assess(current, {"sha": "a" * 40, "covered": 760, "lines": 1000}, "API 34")
        self.assertEqual([], ok)
        text, failures = assess(current, {"sha": "b" * 40, "covered": 7601, "lines": 10000},
                                "API 34")
        self.assertIn("dropped by more than 1 percentage point", failures[0])
        self.assertIn("**FAIL**", text)

    def test_ungated_report_never_fails(self):
        packages = {**PASSING, "app/ovrly/share": (0, 100)}
        text, failures = assess(read_report(self.write(packages)), None, "API 29", gate=False)
        self.assertEqual([], failures)
        self.assertIn("Reported only", text)

    def test_inconsistent_or_empty_reports_are_rejected(self):
        bad = self.root / "bad.xml"
        bad.write_text(f'{HEADER}<report name="app"><package name="p">{line(1, 1)}</package>'
                       f'{line(5, 5)}</report>', encoding="utf-8")
        with self.assertRaises(InvalidCoverage):
            read_report(bad)
        with self.assertRaises(InvalidCoverage):
            read_report(self.root / "missing.xml")
        with self.assertRaises(InvalidCoverage):
            read_report(self.write({"app/ovrly/share": (0, 0)}, "empty.xml"))

    def test_invalid_baseline_is_rejected_and_missing_is_none(self):
        self.assertIsNone(read_baseline(self.root / "none.json"))
        path = self.root / "baseline.json"
        for data in ({"sha": "a", "covered": 5, "lines": 4}, {"covered": 1, "lines": 2},
                     {"sha": "a", "covered": True, "lines": 2}):
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(data=data), self.assertRaises(InvalidCoverage):
                read_baseline(path)

    def test_cli_writes_measurement_summary_and_exit_code(self):
        output = self.root / "out"
        summary = self.root / "summary.md"
        failing = self.write({**PASSING, "app/ovrly/capture": (1, 100)}, "failing.xml")
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(0, main(["--report", str(self.write(PASSING)), "--output",
                                      str(output), "--label", "API 34", "--sha", "c" * 40,
                                      "--gate"]))
            self.assertEqual(1, main(["--report", str(failing), "--output", str(output),
                                      "--label", "API 34", "--sha", "c" * 40, "--gate"]))
            self.assertEqual(1, main(["--report", str(self.root / "missing.xml"), "--output",
                                      str(output), "--label", "API 34", "--sha", "c" * 40]))
        self.assertEqual({"sha": "c" * 40, "covered": 281, "lines": 500},
                         json.loads((output / "coverage.json").read_text()))
        self.assertIn("### Android coverage (API 34)", summary.read_text(encoding="utf-8"))
        self.assertIn("::error::app/ovrly/capture", printed.getvalue())


if __name__ == "__main__":
    unittest.main()
