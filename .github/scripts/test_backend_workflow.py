import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / ".github/workflows/backend.yml").read_text(encoding="utf-8")


class BackendWorkflowTest(unittest.TestCase):
    def test_stack_triggers_and_main_only_push(self):
        pr = re.search(r"^  pull_request:\n((?:^    .*\n)+)", SOURCE, re.M).group(1)
        self.assertNotRegex(pr, r"\b(?:branches|branches-ignore|paths|paths-ignore):")
        for event in ("opened", "synchronize", "reopened", "edited", "ready_for_review"):
            self.assertIn(event, pr)
        self.assertIn("  push:\n    branches: [main]", SOURCE)
        self.assertNotIn("pull_request_target", SOURCE)

    def test_read_only_pinned_tools_and_main_only_cache_writes(self):
        self.assertIn("permissions:\n  contents: read", SOURCE)
        self.assertNotIn("secrets.", SOURCE)
        for uses in re.findall(r"uses: (\S+)", SOURCE):
            self.assertRegex(uses, r"^[\w/-]+@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", SOURCE)
        self.assertIn("save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}", SOURCE)
        self.assertIn("timeout-minutes: 15", SOURCE)
        self.assertIn("retention-days: 7", SOURCE)

    def test_postgres_is_skipped_for_docs_without_skipping_required_result(self):
        validate = SOURCE.split("  validate:\n", 1)[1].split("  result:\n", 1)[0]
        self.assertIn("if: needs.changes.outputs.backend == 'true'", validate)
        self.assertIn("services:\n      postgres:", validate)
        self.assertIn("name: Backend checks\n    if: ${{ always() }}", SOURCE)
        self.assertIn("needs: [changes, validate]", SOURCE)
        self.assertIn("test_backend_*.py", SOURCE)

    def test_final_check_refuses_failed_cancelled_and_missing_jobs(self):
        script = textwrap.dedent(SOURCE.split("      - name: Report backend result", 1)[1]
                                 .split("        run: |\n", 1)[1])
        cases = [
            ("success", "false", "skipped", 0),
            ("success", "true", "success", 0),
            ("failure", "false", "skipped", 1),
            ("cancelled", "", "skipped", 1),
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

    def test_backend_dependabot_uses_native_uv(self):
        source = (ROOT / ".github/dependabot.yml").read_text()
        self.assertIn("package-ecosystem: uv\n    directory: /backend", source)


if __name__ == "__main__":
    unittest.main()
