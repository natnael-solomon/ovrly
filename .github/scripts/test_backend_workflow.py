import gzip
import hashlib
import os
import re
import shutil
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
        validate = SOURCE.split("  validate:\n", 1)[1].split("  recovery:\n", 1)[0]
        recovery = SOURCE.split("  recovery:\n", 1)[1].split("  result:\n", 1)[0]
        self.assertIn("timeout-minutes: 40", validate)
        self.assertIn("timeout-minutes: 15", recovery)
        self.assertIn("retention-days: 7", SOURCE)

    def test_main_baseline_cache_is_exact_main_only_and_falls_back(self):
        validate = SOURCE.split("  validate:\n", 1)[1].split("  recovery:\n", 1)[0]
        restore = validate.split("- name: Restore main coverage baseline", 1)[1].split("- name:", 1)[0]
        self.assertIn("actions/cache/restore@", restore)
        self.assertIn("key: backend-coverage-main-${{ steps.baseline.outputs.sha }}", restore)
        self.assertNotIn("restore-keys", validate)
        self.assertIn("--baseline-sha", validate)
        checks = validate.split("- name: Test and compare coverage with main", 1)[1].split("- name:", 1)[0]
        self.assertIn("--cached-baseline reports/main-baseline", checks)
        self.assertIn(
            "SAVE_BASELINE: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}",
            checks)
        save = validate.split("- name: Save main coverage baseline", 1)[1].split("- name:", 1)[0]
        self.assertIn("actions/cache/save@", save)
        self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main' "
                      "&& steps.checks.outcome == 'success'", save)
        self.assertIn("key: backend-coverage-main-${{ github.sha }}", save)
        self.assertEqual(1, SOURCE.count("key: backend-coverage-main-${{ github.sha }}"))

    def test_media_tools_are_pinned_cached_and_bounded(self):
        self.assertNotIn("apt-get", SOURCE)
        for name in ("FFMPEG_GZ_SHA256", "FFPROBE_GZ_SHA256"):
            self.assertRegex(SOURCE, rf"\n  {name}: [0-9a-f]{{64}}\n")
        self.assertRegex(SOURCE, r"\n  MEDIA_TOOLS_RELEASE: b\d+\.\d+(?:\.\d+)?\n")
        for job in ("validate", "recovery"):
            with self.subTest(job=job):
                body = SOURCE.split(f"  {job}:\n", 1)[1].split("\n  result:\n", 1)[0]
                if job == "validate":
                    body = body.split("  recovery:\n", 1)[0]
                restore = body.split("- name: Restore pinned media tools", 1)[1].split("- name:", 1)[0]
                self.assertIn("actions/cache/restore@", restore)
                install = body.split("- name: Install pinned media tools", 1)[1].split("- name:", 1)[0]
                self.assertIn("timeout-minutes: 5", install)
                self.assertIn("bash .github/scripts/media_tools.sh", install)
                self.assertLess(body.index("Install pinned media tools"), body.index("uv run --frozen alembic"))
        saves = [step for step in SOURCE.split("      - name: ") if "actions/cache/save@" in step]
        self.assertEqual(2, len(saves))
        for step in saves:
            self.assertIn("if: github.event_name == 'push' && github.ref == 'refs/heads/main'", step)
        media = [step for step in saves if "media-tools" in step][0]
        restore = SOURCE.split("- name: Restore pinned media tools", 1)[1].split("- name:", 1)[0]
        for field in ("path", "key"):
            self.assertEqual(re.search(rf"{field}: (.+)", restore).group(1),
                             re.search(rf"{field}: (.+)", media).group(1))

    def test_baseline_restore_and_save_use_identical_path_and_key_family(self):
        # actions/cache hashes the path list into the cache version: a differing path
        # never matches even with an identical key.
        validate = SOURCE.split("  validate:\n", 1)[1].split("  recovery:\n", 1)[0]
        restore = validate.split("- name: Restore main coverage baseline", 1)[1].split("- name:", 1)[0]
        save = validate.split("- name: Save main coverage baseline", 1)[1].split("- name:", 1)[0]
        checks = validate.split("- name: Test and compare coverage with main", 1)[1].split("- name:", 1)[0]
        restore_path = re.search(r"^\s+path: (\S+)$", restore, re.M).group(1)
        save_path = re.search(r"^\s+path: (\S+)$", save, re.M).group(1)
        self.assertEqual(restore_path, save_path)
        relative = restore_path.removeprefix("backend/")
        self.assertIn(f"--cached-baseline {relative}", checks)
        self.assertIn(f"--save-baseline {relative}", checks)
        restore_key = re.search(r"key: (\S+)\$\{\{", restore).group(1)
        save_key = re.search(r"key: (\S+)\$\{\{", save).group(1)
        self.assertEqual(restore_key, save_key)

    def test_postgres_is_skipped_for_docs_without_skipping_required_result(self):
        validate = SOURCE.split("  validate:\n", 1)[1].split("  recovery:\n", 1)[0]
        self.assertIn("if: needs.changes.outputs.backend == 'true'", validate)
        self.assertIn("services:\n      postgres:", validate)
        self.assertIn("name: Backend checks\n    if: ${{ always() }}", SOURCE)
        self.assertIn("needs: [changes, validate]", SOURCE)
        self.assertIn("test_backend_*.py", SOURCE)

    def test_recovery_suite_is_a_separate_always_reported_job(self):
        recovery = SOURCE.split("  recovery:\n", 1)[1].split("  result:\n", 1)[0]
        self.assertIn("name: Validate recovery", recovery)
        self.assertIn("if: needs.changes.outputs.backend == 'true'", recovery)
        self.assertIn("services:\n      postgres:", recovery)
        self.assertIn("alembic upgrade head", recovery)
        for path in (
            "tests/test_job_states.py",
            "tests/test_job_retries.py",
            "tests/test_job_queue.py",
            "tests/recovery",
        ):
            self.assertIn(path, recovery)
        result = SOURCE.split("  recovery_result:\n", 1)[1]
        self.assertIn("name: Backend recovery\n    if: ${{ always() }}", result)
        self.assertIn("needs: [changes, recovery]", result)
        self.assertIn("VALIDATION: ${{ needs.recovery.result }}", result)

    def test_final_check_refuses_failed_cancelled_and_missing_jobs(self):
        for marker in ("      - name: Report backend result", "      - name: Report recovery result"):
            with self.subTest(marker=marker):
                self.check_result_script(marker)

    def check_result_script(self, marker):
        script = textwrap.dedent(SOURCE.split(marker, 1)[1]
                                 .split("        run: |\n", 1)[1].split("\n\n", 1)[0])
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


@unittest.skipUnless(os.name == "posix" and shutil.which("prlimit") and shutil.which("sha256sum"),
                     "needs a Linux runner with util-linux and coreutils")
class MediaToolsScriptTest(unittest.TestCase):
    CURL = textwrap.dedent("""\
        #!/usr/bin/env python3
        import os, shutil, sys
        args = sys.argv[1:]
        output = args[args.index("--output") + 1]
        with open(os.environ["CURL_LOG"], "a") as log:
            log.write(args[-1] + "\\n")
        shutil.copyfile(os.path.join(os.environ["FIXTURES"], os.path.basename(args[-1])), output)
        """)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        fixtures, stubs = self.root / "fixtures", self.root / "stubs"
        fixtures.mkdir()
        stubs.mkdir()
        self.sums = {}
        for tool in ("ffmpeg", "ffprobe"):
            binary = f"#!/bin/sh\necho '{tool} version fixture'\n".encode()
            data = gzip.compress(binary, mtime=0)
            (fixtures / f"{tool}-linux-x64.gz").write_bytes(data)
            self.sums[tool] = hashlib.sha256(data).hexdigest()
        curl = stubs / "curl"
        curl.write_text(self.CURL)
        curl.chmod(0o755)
        self.cache = self.root / "cache"
        self.env = {
            **os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "FIXTURES": str(fixtures),
            "CURL_LOG": str(self.root / "curl.log"), "MEDIA_TOOLS_CACHE": str(self.cache),
            "RUNNER_TEMP": str(self.root / "runner"), "GITHUB_PATH": str(self.root / "path"),
            "MEDIA_TOOLS_RELEASE": "b0.0", "FFMPEG_GZ_SHA256": self.sums["ffmpeg"],
            "FFPROBE_GZ_SHA256": self.sums["ffprobe"],
        }

    def run_script(self, **env):
        return subprocess.run(["bash", str(ROOT / ".github/scripts/media_tools.sh")],
                              env={**self.env, **env}, capture_output=True, text=True)

    def downloads(self):
        log = self.root / "curl.log"
        return log.read_text().split() if log.exists() else []

    def test_downloads_verifies_installs_then_reuses_cache(self):
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(self.downloads()))
        self.assertTrue(all("/releases/download/b0.0/" in url for url in self.downloads()))
        bin_dir = (self.root / "path").read_text().strip()
        self.assertEqual(str(self.root / "runner/ovrly-media-tools/bin"), bin_dir)
        self.assertIn("ffprobe version fixture", subprocess.check_output([bin_dir + "/ffprobe"], text=True))
        again = self.run_script()
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual(2, len(self.downloads()))
        self.assertIn("cached archive verified", again.stdout)

    def test_corrupt_cache_is_replaced(self):
        self.cache.mkdir()
        (self.cache / "ffmpeg-linux-x64.gz").write_bytes(b"tampered")
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(self.downloads()))

    def test_checksum_mismatch_fails_without_installing(self):
        result = self.run_script(FFPROBE_GZ_SHA256="0" * 64)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("does not match its pinned SHA-256", result.stdout)
        self.assertFalse((self.cache / "ffprobe-linux-x64.gz").exists())
        self.assertFalse((self.cache / "ffprobe-linux-x64.gz.part").exists())
        self.assertFalse((self.root / "path").exists())


if __name__ == "__main__":
    unittest.main()
