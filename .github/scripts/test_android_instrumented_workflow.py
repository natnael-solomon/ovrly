import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / ".github/workflows/android-instrumented.yml").read_text(encoding="utf-8")
MAIN_ONLY = "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def job(name, following):
    return SOURCE.split(f"  {name}:\n", 1)[1].split(f"  {following}:\n", 1)[0]


class InstrumentedWorkflowTest(unittest.TestCase):
    def test_stack_triggers_without_path_filters(self):
        pr = re.search(r"^  pull_request:\n((?:^    .*\n)+)", SOURCE, re.M).group(1)
        self.assertNotRegex(pr, r"\b(?:branches|branches-ignore|paths|paths-ignore):")
        for event in ("opened", "synchronize", "reopened", "edited", "ready_for_review"):
            self.assertIn(event, pr)
        self.assertIn("  push:\n    branches: [main]", SOURCE)
        self.assertNotIn("pull_request_target", SOURCE)
        self.assertNotRegex(SOURCE, r"^\s+paths(?:-ignore)?:")

    def test_read_only_pinned_actions_without_secrets(self):
        self.assertIn("permissions:\n  contents: read", SOURCE)
        self.assertNotIn("secrets.", SOURCE)
        self.assertIn("persist-credentials: false", SOURCE)
        uses = re.findall(r"uses: (\S+)", SOURCE)
        self.assertIn("reactivecircus/android-emulator-runner", " ".join(uses))
        for action in uses:
            self.assertRegex(action, r"^[\w/-]+@[0-9a-f]{40}$")

    def test_api_29_and_34_matrix_gates_coverage_on_34(self):
        matrix = job("instrumented", "reports")
        self.assertIn("fail-fast: false", matrix)
        self.assertIn("- api-level: 29\n            coverage-gate: false", matrix)
        self.assertIn("- api-level: 34\n            coverage-gate: true", matrix)
        self.assertIn("if: needs.changes.outputs.android == 'true'", matrix)

    def test_only_main_writes_caches(self):
        self.assertIn("cache-read-only: ${{ github.event_name == 'pull_request' || "
                      "github.ref != 'refs/heads/main' }}", SOURCE)
        saves = [step for step in SOURCE.split("      - name: ")
                 if "actions/cache/save@" in step]
        self.assertEqual(2, len(saves))
        for step in saves:
            self.assertIn(MAIN_ONLY, step.split("\n", 2)[1])
        self.assertNotRegex(SOURCE, r"uses: actions/cache@")
        snapshot = SOURCE.split("- name: Create AVD snapshot (main only)", 1)[1].split("\n", 2)[1]
        self.assertIn(MAIN_ONLY, snapshot)

    def test_runs_the_retry_runner_and_coverage_gate(self):
        self.assertIn("python3 ../.github/scripts/android_instrumented.py", SOURCE)
        self.assertIn(":app:jacocoDebugReport", SOURCE)
        self.assertIn("android_coverage.py", SOURCE)
        self.assertIn("-Povrly.coverage=true", SOURCE)
        self.assertIn("--dependency-verification=strict", SOURCE)
        self.assertIn("test_android_*.py", SOURCE)

    def test_reports_kept_seven_days_as_one_artifact(self):
        self.assertIn("name: android-instrumented-reports-api${{ matrix.api-level }}", SOURCE)
        merge = job("reports", "result")
        self.assertIn("uses: actions/upload-artifact/merge@", merge)
        self.assertIn("name: android-instrumented-reports\n", merge)
        self.assertEqual(2, SOURCE.count("retention-days: 7"))

    def test_api_29_cold_boots_and_emulator_steps_are_bounded(self):
        matrix = job("instrumented", "reports")
        indent = "\n" + " " * 12
        self.assertIn(f"- api-level: 29{indent}coverage-gate: false{indent}snapshot: false"
                      f"{indent}emulator-options: -no-snapshot ", matrix)
        self.assertIn(f"- api-level: 34{indent}coverage-gate: true{indent}snapshot: true"
                      f"{indent}emulator-options: -no-snapshot-save ", matrix)
        for step in ("Restore AVD snapshot", "Create AVD snapshot (main only)",
                     "Save AVD snapshot (main only)"):
            header = SOURCE.split(f"- name: {step}\n", 1)[1].split("uses:", 1)[0]
            self.assertIn("matrix.snapshot", header, step)
        tests = SOURCE.split("- name: Run instrumented tests", 1)[1].split("- name: ", 1)[0]
        self.assertIn("timeout-minutes: 25", tests)
        self.assertIn("emulator-boot-timeout: 300", tests)
        self.assertIn("emulator-options: ${{ matrix.emulator-options }}", tests)
        create = SOURCE.split("- name: Create AVD snapshot (main only)", 1)[1]
        create = create.split("- name: ", 1)[0]
        self.assertIn("timeout-minutes: 15", create)
        self.assertIn("android_emulator.py snapshot", create)
        self.assertIn("-no-snapshot-save", create)

    def test_coverage_is_measured_only_after_a_complete_test_run(self):
        coverage = SOURCE.split("- name: Coverage report, floors and comparison with main", 1)[1]
        self.assertIn("if: ${{ !cancelled() && steps.tests.outcome == 'success' }}",
                      coverage.split("run: |", 1)[0])
        self.assertIn("--gate", coverage)

    def test_final_check_refuses_failed_cancelled_and_missing_jobs(self):
        self.assertIn("name: Android instrumented checks\n    if: ${{ always() }}", SOURCE)
        self.assertIn("needs: [changes, instrumented]", SOURCE.split("  result:\n", 1)[1])
        script = textwrap.dedent(SOURCE.split("      - name: Report instrumented result", 1)[1]
                                 .split("        run: |\n", 1)[1])
        cases = [
            ("success", "false", "skipped", 0),
            ("success", "true", "success", 0),
            ("failure", "false", "skipped", 1),
            ("success", "true", "failure", 1),
            ("success", "true", "cancelled", 1),
            ("success", "true", "skipped", 1),
            ("success", "", "skipped", 1),
            ("success", "false", "success", 1),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for detection, required, validation, expected in cases:
                with self.subTest(detection=detection, required=required, validation=validation):
                    result = subprocess.run(["bash", "-c", script], env={
                        **os.environ, "DETECTION": detection, "REQUIRED": required,
                        "VALIDATION": validation, "GITHUB_STEP_SUMMARY": temporary + "/summary",
                    }, capture_output=True, text=True)
                    self.assertEqual(expected, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
