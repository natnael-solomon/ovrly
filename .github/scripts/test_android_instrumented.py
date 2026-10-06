import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import android_instrumented as runner
from android_instrumented import test_cases as read_cases
from android_instrumented import (
    CLASS_ARGUMENT, FAILED, PASSED, SKIPPED, classify, evaluate, plan_retry, summary,
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

    def main(self, *runs):
        summary_path = Path(self.temp.name) / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = runner.main(["--label", "API 29"], runner=self.gradle(*runs))
        return code, output.getvalue(), summary_path.read_text(encoding="utf-8")

    def test_flake_is_retried_once_with_only_failed_tests_and_keeps_both_attempts(self):
        code, output, text = self.main((1, {"a.A#x": FAILED, "a.A#y": PASSED}),
                                       (0, {"a.A#x": PASSED}))
        self.assertEqual(0, code)
        self.assertEqual(2, len(self.commands))
        self.assertEqual(CLASS_ARGUMENT + "a.A#x", self.commands[1][-1])
        self.assertNotIn(CLASS_ARGUMENT, " ".join(self.commands[0]))
        self.assertIn("::warning::Flaky instrumented test on API 29: a.A#x", output)
        self.assertIn("Flaky", text)
        for attempt in (1, 2):
            self.assertTrue((self.attempts / f"attempt-{attempt}/coverage/device/coverage.ec")
                            .is_file())
        result = json.loads((self.attempts / "result.json").read_text())
        self.assertEqual(["a.A#x"], result["flaky"])
        self.assertTrue(result["passed"])

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
