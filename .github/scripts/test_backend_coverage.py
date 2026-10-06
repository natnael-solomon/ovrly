import json
import tempfile
import unittest
from pathlib import Path

from backend_coverage import InvalidCoverage, assess, line_counts, percent, read_report


def report(covered, total, name="services/worker/runtime.py"):
    summary = {"covered_lines": covered, "num_statements": total}
    return {"totals": summary, "files": {name: {"summary": summary}}}


class CoveragePolicyTest(unittest.TestCase):
    def test_exact_floor_boundary_and_missing_modules_are_explicit(self):
        summary, failures = assess(report(90, 100), None, "a" * 40)
        self.assertEqual([], failures)
        self.assertIn("Not implemented; not evaluated", summary)
        self.assertIn(
            "| `services/jobs` | Not implemented; not evaluated | 90% future floor |", summary,
        )
        self.assertIn("**not available**", summary)
        self.assertTrue(assess(report(89, 100), None, "a" * 40)[1])

    def test_one_point_drop_allowed_more_than_one_rejected(self):
        self.assertFalse(assess(report(94, 100), report(95, 100), "a" * 40)[1])
        self.assertTrue(assess(report(93999, 100000), report(95, 100), "a" * 40)[1])
        self.assertFalse(assess(report(96, 100), report(95, 100), "a" * 40)[1])

    def test_display_rounding_cannot_hide_a_floor_failure(self):
        self.assertTrue(assess(report(89999, 100000), None, "a" * 40)[1])

    def test_jobs_exact_floor_and_display_rounding(self):
        for covered, total, passed in [(90, 100, True), (89, 100, False),
                                       (89999, 100000, False)]:
            with self.subTest(covered=covered, total=total):
                summary, failures = assess(
                    report(covered, total, "services/jobs/queue.py"), None, "a" * 40,
                )
                expected = [] if passed else ["services/jobs: line coverage is below 90%"]
                self.assertEqual(expected, failures)
                self.assertIn(
                    f"| `services/jobs` | {covered}/{total} "
                    f"({100 * covered / total:.2f}%) | "
                    f"{'PASS' if passed else 'FAIL'} >= 90% |", summary,
                )

    def test_jobs_floor_uses_line_weighted_counts_across_nested_files(self):
        for covered, total, nested_covered, nested_total, passed in [
            (900, 900, 0, 100, True), (9, 10, 80, 90, False),
        ]:
            with self.subTest(covered=covered):
                combined_covered = covered + nested_covered
                combined_total = total + nested_total
                data = report(combined_covered, combined_total, "services/jobs/queue.py")
                data["files"] = {
                    **report(0, 0, "services/jobs/__init__.py")["files"],
                    **report(covered, total, "services/jobs/queue.py")["files"],
                    **report(nested_covered, nested_total,
                             "services/jobs/internal/state.py")["files"],
                }
                summary, failures = assess(data, None, "a" * 40)
                self.assertEqual([] if passed else [
                    "services/jobs: line coverage is below 90%",
                ], failures)
                self.assertIn(
                    f"| `services/jobs` | {combined_covered}/{combined_total}", summary,
                )

    def test_jobs_floor_cannot_be_hidden_by_healthy_worker_or_sibling_prefix(self):
        for other in ("services/worker/runtime.py", "services/jobs_extra.py",
                      "services/jobs_extra/queue.py"):
            with self.subTest(other=other):
                data = report(989, 1000, other)
                data["files"] = {
                    **report(900, 900, other)["files"],
                    **report(89, 100, "services/jobs/queue.py")["files"],
                }
                summary, failures = assess(data, data, "a" * 40)
                self.assertEqual(["services/jobs: line coverage is below 90%"], failures)
                self.assertIn("Regression gate: **PASS**", summary)

    def test_passing_jobs_floor_does_not_bypass_overall_regression_gate(self):
        for covered, passed in [(94000, True), (93999, False)]:
            with self.subTest(covered=covered):
                summary, failures = assess(
                    report(covered, 100000, "services/jobs/queue.py"),
                    report(95, 100, "services/jobs/queue.py"), "a" * 40,
                )
                self.assertIn("PASS >= 90%", summary)
                self.assertEqual([] if passed else [
                    "Overall backend line coverage dropped by more than 1 percentage point",
                ], failures)

    def test_auth_module_file_and_nested_contracts_are_gated(self):
        for path in ["services/api/auth.py", "services/api/auth/check.py",
                     "services/contracts/parser.py"]:
            with self.subTest(path=path):
                self.assertTrue(assess(report(89, 100, path), None, "a" * 40)[1])

    def test_present_empty_module_is_not_treated_as_implemented(self):
        for module in ("services/worker", "services/jobs"):
            with self.subTest(module=module):
                data = report(100, 100, "services/api/main.py")
                data["files"][module + "/__init__.py"] = {
                    "summary": {"covered_lines": 0, "num_statements": 0}
                }
                self.assertEqual([
                    f"{module}: no executable lines; cannot establish the floor",
                ], assess(data, None, "a" * 40)[1])

    def test_malformed_counts_and_zero_denominator_fail(self):
        for covered, total in [(True, 10), (1, 0), (-1, 3), (2, "3"), (1.5, 3)]:
            with self.subTest(covered=covered, total=total), self.assertRaises(InvalidCoverage):
                line_counts({"covered_lines": covered, "num_statements": total})
        with self.assertRaises(InvalidCoverage):
            percent(0, 0)

    def test_source_inventory_and_totals_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "services/worker").mkdir(parents=True)
            (root / "services/worker/runtime.py").write_text("pass\n")
            path = root / "coverage.json"
            path.write_text(json.dumps(report(90, 100)))
            self.assertEqual(report(90, 100), read_report(path, root))
            (root / "services/new.py").write_text("pass\n")
            with self.assertRaisesRegex(InvalidCoverage, "inventory"):
                read_report(path, root)
            (root / "services/new.py").unlink()
            data = report(90, 100)
            data["totals"] = {"covered_lines": 91, "num_statements": 100}
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(InvalidCoverage, "inconsistent"):
                read_report(path, root)


if __name__ == "__main__":
    unittest.main()
