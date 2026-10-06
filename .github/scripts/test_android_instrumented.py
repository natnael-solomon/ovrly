import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import android_instrumented as runner
from android_instrumented import test_cases as read_cases
from android_instrumented import (
    CLASS_ARGUMENT, FAILED, PASSED, SKIPPED, classify, evaluate, expected_tests, plan_retry,
    summary,
)


def junit(cases):
    rows = []
    for name, outcome in cases.items():
        cls, method = name.split("#")
        inner = {FAILED: "<failure>boom</failure>", SKIPPED: "<skipped/>"}.get(outcome, "")
        rows.append(f'<testcase classname="{cls}" name="{method}">{inner}</testcase>')
    return f'<testsuite name="device">{"".join(rows)}</testsuite>'


class ResultParsingTest(unittest.TestCase):
    def test_reads_every_xml_and_failed_wins_over_a_duplicate_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "debug").mkdir()
            (root / "debug" / "TEST-a.xml").write_text(junit({"a.A#x": PASSED, "a.A#y": FAILED}))
            (root / "debug" / "TEST-b.xml").write_text(junit({"a.A#x": FAILED, "b.B#z": SKIPPED}))
            (root / "debug" / "TEST-c.xml").write_text(junit({"a.A#x": PASSED}))
            self.assertEqual({"a.A#x": FAILED, "a.A#y": FAILED, "b.B#z": SKIPPED},
                             read_cases(root))

    def test_error_elements_count_as_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "TEST.xml"
            path.write_text('<testsuite><testcase classname="a.A" name="x">'
                            '<error>crash</error></testcase></testsuite>')
            self.assertEqual({"a.A#x": FAILED}, read_cases(temporary))


class InventoryTest(unittest.TestCase):
    def test_parses_kotlin_and_java_tests_with_extra_annotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a").mkdir()
            (root / "a" / "OneTest.kt").write_text(
                "package app.a\n\n@RunWith(AndroidJUnit4::class)\nclass OneTest {\n"
                "    @Test fun plain() {}\n\n    @LargeTest\n    @Test fun large() {}\n"
                "    @Test\n    @SdkSuppress(minSdkVersion = 30)\n    fun wrapped() {}\n"
                "    private fun helper() {}\n}\n\nenum class Mode { ON }\n",
                encoding="utf-8")
            (root / "a" / "TwoTest.java").write_text(
                "package app.a;\n\npublic final class TwoTest {\n"
                "    @Test\n    public void javaTest() {}\n}\n", encoding="utf-8")
            (root / "a" / "Helper.kt").write_text("package app.a\n\nobject Helper\n",
                                                  encoding="utf-8")
            self.assertEqual({"app.a.OneTest#plain", "app.a.OneTest#large",
                              "app.a.OneTest#wrapped", "app.a.TwoTest#javaTest"},
                             expected_tests(root))

    def test_no_tests_is_an_error(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            expected_tests(temporary)

    def test_repository_inventory_covers_the_suites(self):
        tests = expected_tests()
        for name in ("app.ovrly.capture.CaptureServiceTest#threeMinuteLimitStopsAndReleases",
                     "app.ovrly.share.ShareInputReaderTest#ungrantedSourceIsPrivate",
                     "app.ovrly.overlay.OverlayServiceTest#showAndHideLeaveNoWindow",
                     "app.ovrly.ui.ShareIntakeSheetTest#malformedShareOffersNoFileAndCloses"):
            self.assertIn(name, tests)

    def test_evaluate_rejects_missing_and_undeclared_tests(self):
        expected = {"a.A#x", "a.A#y"}
        passed, _, failed, reason = evaluate(1, {"a.A#x": FAILED}, 0, {"a.A#x": PASSED},
                                             expected)
        self.assertFalse(passed)
        self.assertEqual(["(not run) a.A#y"], failed)
        self.assertIn("Not retried", reason)
        self.assertTrue(evaluate(0, {"a.A#x": PASSED, "a.A#y": PASSED}, expected=expected)[0])


class RetryPolicyTest(unittest.TestCase):
    def test_success_is_not_retried(self):
        self.assertEqual([], plan_retry(0, {"a.A#x": PASSED})[0])

    def test_only_failed_tests_are_retried(self):
        retry, _ = plan_retry(1, {"a.A#x": PASSED, "b.B#y": FAILED, "a.A#z": FAILED})
        self.assertEqual(["a.A#z", "b.B#y"], retry)

    def test_gradle_failure_without_failing_test_is_not_retried_and_fails(self):
        retry, reason = plan_retry(1, {"a.A#x": PASSED})
        self.assertEqual([], retry)
        self.assertIn("not retried", reason)
        self.assertFalse(evaluate(1, {"a.A#x": PASSED})[0])

    def test_flake_passes_and_is_reported(self):
        first = {"a.A#x": FAILED, "a.A#y": PASSED}
        passed, flaky, failed, _ = evaluate(1, first, 0, {"a.A#x": PASSED})
        self.assertTrue(passed)
        self.assertEqual(["a.A#x"], flaky)
        self.assertEqual([], failed)

    def test_failing_twice_fails(self):
        first = {"a.A#x": FAILED, "b.B#y": FAILED}
        passed, flaky, failed, _ = evaluate(1, first, 1, {"a.A#x": PASSED, "b.B#y": FAILED})
        self.assertFalse(passed)
        self.assertEqual(["a.A#x"], flaky)
        self.assertEqual(["b.B#y"], failed)

    def test_retry_that_did_not_run_the_test_fails(self):
        self.assertEqual(([], ["a.A#x"]), classify({"a.A#x": FAILED}, {}))
        self.assertFalse(evaluate(1, {"a.A#x": FAILED}, 1, {})[0])

    def test_retry_gradle_failure_without_failing_test_fails(self):
        passed, _, failed, _ = evaluate(1, {"a.A#x": FAILED}, 1, {"a.A#x": PASSED})
        self.assertFalse(passed)
        self.assertTrue(failed)

    def test_no_results_never_pass(self):
        self.assertFalse(evaluate(0, {})[0])

    def test_summary_names_flakes_failures_and_policy(self):
        text = summary("API 34", {"a.A#x": FAILED, "a.A#y": PASSED}, {"a.A#x": PASSED},
                       ["a.A#x"], [], "retrying")
        self.assertIn("### Android instrumented tests (API 34)", text)
        self.assertIn("1 passed, 1 failed, 0 skipped", text)
        self.assertIn("- `a.A#x`", text)
        self.assertIn("quarantined with `@Ignore`", text)
        self.assertIn("Never re-run", text)


class RunnerTest(unittest.TestCase):
    """main() with a fake Gradle that writes JUnit XML where AGP would."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        build = Path(self.temp.name) / "build"
        self.outputs = {
            "results": build / "outputs/androidTest-results/connected",
            "coverage": build / "outputs/code_coverage/debugAndroidTest/connected",
            "report": build / "reports/androidTests/connected",
        }
        self.attempts = build / "instrumented"
        for name, value in (("OUTPUTS", self.outputs), ("ATTEMPTS", self.attempts),
                            ("ANDROID", Path(self.temp.name))):
            patcher = patch.object(runner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.commands = []

    def gradle(self, *runs):
        queue = list(runs)

        def run(command, cwd, check):
            self.commands.append(command)
            code, cases = queue.pop(0)
            results = self.outputs["results"] / "debug"
            results.mkdir(parents=True)
            (results / "TEST-device.xml").write_text(junit(cases))
            coverage = self.outputs["coverage"] / "device"
            coverage.mkdir(parents=True)
            (coverage / "coverage.ec").write_bytes(b"ec")
            return SimpleNamespace(returncode=code)
        return run

    def declare(self, names):
        """Write Kotlin sources declaring names (`pkg.Class#method`), as androidTest would."""
        sources = Path(self.temp.name) / "src"
        shutil.rmtree(sources, ignore_errors=True)
        by_class = {}
        for name in names:
            cls, method = name.split("#")
            by_class.setdefault(cls, []).append(method)
        for cls, methods in by_class.items():
            package, _, simple = cls.rpartition(".")
            body = "".join(f"    @Test fun {method}() {{}}\n" for method in methods)
            path = sources / package.replace(".", "/") / f"{simple}.kt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"package {package}\n\nclass {simple} {{\n{body}}}\n",
                            encoding="utf-8")
        return sources

    def main(self, *runs, declared=None):
        summary_path = Path(self.temp.name) / "summary.md"
        sources = self.declare(declared if declared is not None else runs[0][1])
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main(["--label", "API 29"], runner=self.gradle(*runs),
                               sources=sources)
        return code, output.getvalue(), summary_path.read_text(encoding="utf-8")

    def test_crash_that_drops_later_tests_fails_even_if_the_retry_passes(self):
        # A crash records the running test as failed and drops the rest of the suite.
        code, output, text = self.main(
            (1, {"a.A#x": PASSED, "a.A#y": FAILED}),
            (0, {"a.A#y": PASSED}),
            declared=["a.A#x", "a.A#y", "a.A#z", "b.B#w"],
        )
        self.assertEqual(1, code)
        self.assertEqual(1, len(self.commands), "an incomplete first attempt is not retried")
        self.assertIn("::error::Instrumented test failed on API 29: (not run) a.A#z", output)
        self.assertIn("(not run) b.B#w", output)
        self.assertIn("did not report every declared test", text)
        result = json.loads((self.attempts / "result.json").read_text())
        self.assertFalse(result["passed"])
        self.assertEqual([], result["flaky"])

    def test_incomplete_run_with_zero_exit_fails(self):
        code, output, _ = self.main((0, {"a.A#x": PASSED}), declared=["a.A#x", "a.A#y"])
        self.assertEqual(1, code)
        self.assertIn("(not run) a.A#y", output)

    def test_result_for_an_undeclared_test_fails(self):
        code, output, _ = self.main((0, {"a.A#x": PASSED, "c.C#v": PASSED}),
                                    declared=["a.A#x"])
        self.assertEqual(1, code)
        self.assertIn("(not declared in the sources) c.C#v", output)

    def test_flake_is_retried_once_with_only_failed_tests_and_keeps_both_attempts(self):
        code, output, text = self.main((1, {"a.A#x": FAILED, "a.A#y": PASSED}),
                                       (0, {"a.A#x": PASSED}))
        self.assertEqual(0, code)
        self.assertEqual(2, len(self.commands))
        self.assertEqual(CLASS_ARGUMENT + "a.A#x", self.commands[1][-1])
        self.assertNotIn(CLASS_ARGUMENT, " ".join(self.commands[0]))
        self.assertIn("::warning::Flaky instrumented test on API 29: a.A#x", output)
        self.assertIn("Flaky", text)
        for run in ("attempt-1", "attempt-2/run-1"):
            self.assertTrue((self.attempts / run / "coverage/device/coverage.ec").is_file())
        result = json.loads((self.attempts / "result.json").read_text())
        self.assertEqual(["a.A#x"], result["flaky"])
        self.assertTrue(result["passed"])

    def test_each_failed_test_is_retried_in_its_own_run(self):
        code, output, _ = self.main(
            (1, {"a.A#x": FAILED, "b.B#y": FAILED, "a.A#z": PASSED}),
            (0, {"a.A#x": PASSED}),
            (1, {"b.B#y": FAILED}),
        )
        self.assertEqual(1, code)
        self.assertEqual([CLASS_ARGUMENT + "a.A#x", CLASS_ARGUMENT + "b.B#y"],
                         [command[-1] for command in self.commands[1:]])
        self.assertIn("::warning::Flaky instrumented test on API 29: a.A#x", output)
        self.assertIn("::error::Instrumented test failed on API 29: b.B#y", output)
        for run in ("run-1", "run-2"):
            self.assertTrue((self.attempts / "attempt-2" / run / "results").is_dir())

    def test_real_failure_fails_after_one_retry(self):
        code, output, _ = self.main((1, {"a.A#x": FAILED}), (1, {"a.A#x": FAILED}))
        self.assertEqual(1, code)
        self.assertEqual(2, len(self.commands))
        self.assertIn("::error::Instrumented test failed on API 29: a.A#x", output)

    def test_pass_runs_once(self):
        code, _, _ = self.main((0, {"a.A#x": PASSED}))
        self.assertEqual(0, code)
        self.assertEqual(1, len(self.commands))


if __name__ == "__main__":
    unittest.main()
