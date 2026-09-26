"""Synthetic-only fixtures: no live credentials, provider calls or history rewrites."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from secret_scan import ROOT, ScanError, private_paths, scan, scanner, verified_archive

REQUIRED = __name__ == "__main__" or os.environ.get("OVRLY_REQUIRE_QUALITY_TOOLS") == "1"


class SecretPolicyTest(unittest.TestCase):
    def test_defaults_remain_enabled_without_broad_allowlists(self):
        config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
        self.assertEqual({"useDefault": True}, config["extend"])
        self.assertNotIn("allowlists", config)
        self.assertNotIn("allowlist", config)

    def test_pin_manifest_covers_supported_targets_with_sha256(self):
        manifest = json.loads((ROOT / ".github/gitleaks.json").read_text())
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual({"linux_x64", "linux_arm64", "darwin_x64", "darwin_arm64",
                          "windows_x64", "windows_arm64"}, set(manifest["sha256"]))
        for digest in manifest["sha256"].values():
            self.assertRegex(digest, r"^[a-f0-9]{64}$")

    def test_checksum_mismatch_fails_before_execution(self):
        data = b"synthetic archive"
        self.assertEqual(data, verified_archive(data, hashlib.sha256(data).hexdigest()))
        with self.assertRaisesRegex(ScanError, "checksum mismatch"):
            verified_archive(data, "0" * 64)

    def test_unsupported_platform_never_downloads(self):
        with patch("secret_scan.platform.system", return_value="Unsupported"), \
                patch("secret_scan.urllib.request.urlopen") as download:
            with self.assertRaisesRegex(ScanError, "Unsupported"):
                with scanner():
                    self.fail("Unsupported platform yielded a binary")
        download.assert_not_called()

    def test_shallow_history_never_becomes_a_clean_result(self):
        with patch("secret_scan.git", return_value=b"true\n"):
            with self.assertRaisesRegex(ScanError, "full history"):
                private_paths(ROOT)

    def test_ci_fetches_full_history_and_runs_both_hooks(self):
        workflow = (ROOT / ".github/workflows/quality.yml").read_text()
        hooks = (ROOT / ".pre-commit-config.yaml").read_text()
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("pre-commit run --all-files", workflow)
        self.assertIn("python .github/scripts/secret_scan.py", hooks)
        self.assertIn("python .github/scripts/test_secret_scan.py", hooks)


class SecretFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not REQUIRED:
            raise unittest.SkipTest("Native secret fixtures run in the dedicated Quality checks job")
        cls.binary = cls.enterClassContext(scanner())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ovrly-secret-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.hooksPath", str(self.repo / "no-hooks"))
        self.write("safe.txt", "safe fixture\n")
        self.commit()
        # Deliberately nonfunctional, generated only inside a disposable repo.
        self.fake = "ghp_" + hashlib.sha256(b"ovrly-synthetic-token").hexdigest()[:36]

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def write(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_scan(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = scan(self.repo, self.binary)
        self.assertNotIn(self.fake, output.getvalue())
        return result, output.getvalue()

    def test_clean_repo_and_ignored_local_properties_are_allowed(self):
        self.write(".gitignore", "voxide.local.properties\n")
        self.commit()
        self.write("android/voxide.local.properties", "local fixture\n")
        self.assertEqual(0, self.run_scan()[0])

    def test_staged_secret_cannot_be_hidden_by_safe_working_copy(self):
        self.write("staged.txt", self.fake + "\n")
        self.git("add", "staged.txt")
        self.write("staged.txt", "safe working copy\n")
        result, output = self.run_scan()
        self.assertEqual(1, result)
        self.assertIn("index:", output)

    def test_tracked_working_secret_is_detected(self):
        self.write("safe.txt", self.fake + "\n")
        result, output = self.run_scan()
        self.assertEqual(1, result)
        self.assertIn("tracked working changes:", output)

    def test_deleted_historical_secret_and_ignore_bypasses_still_fail(self):
        self.write("historical.txt", self.fake + " # gitleaks:allow\n")
        sha = self.commit()
        self.git("rm", "-q", "historical.txt")
        self.commit()
        with patch.dict(os.environ, {"GITLEAKS_CONFIG": "/nonexistent/override"}):
            result, output = self.run_scan()
        self.assertEqual(1, result)
        self.assertIn("history:", output)
        self.write(".gitleaksignore", f"{sha}:historical.txt:github-pat:1\n")
        result, output = self.run_scan()
        self.assertEqual(1, result)
        self.assertIn(".gitleaksignore is not permitted", output)

    def test_redaction_applies_to_native_reports_as_well_as_console(self):
        self.write("staged.txt", self.fake + "\n")
        self.git("add", "staged.txt")
        actual_run = subprocess.run
        reports = []

        def checked_run(args, **kwargs):
            result = actual_run(args, **kwargs)
            if "--report-path" in args:
                raw = Path(args[args.index("--report-path") + 1]).read_text()
                self.assertNotIn(self.fake, raw + repr(result.stdout) + repr(result.stderr))
                reports.extend(json.loads(raw))
            return result

        with patch("secret_scan.subprocess.run", side_effect=checked_run):
            self.assertEqual(1, self.run_scan()[0])
        self.assertTrue(reports)

    def test_private_file_is_rejected_even_if_empty_or_deleted_later(self):
        self.write("android/voxide.local.properties", "")
        self.git("add", "android/voxide.local.properties")
        self.assertEqual(["android/voxide.local.properties"], private_paths(self.repo))
        self.commit()
        self.git("rm", "-q", "android/voxide.local.properties")
        self.commit()
        self.assertEqual(["android/voxide.local.properties"], private_paths(self.repo))
        self.assertEqual(1, self.run_scan()[0])

    def test_invalid_scanner_config_fails_closed(self):
        config = self.repo / "bad.toml"
        config.write_text("not = [valid")
        with self.assertRaisesRegex(ScanError, "produced no report"):
            scan(self.repo, self.binary, config)

    def test_merge_resolution_secret_is_included_in_history(self):
        base = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "other")
        self.write("other.txt", "other\n")
        self.commit()
        self.git("checkout", "-q", base)
        self.write("main.txt", "main\n")
        self.commit()
        self.git("merge", "--no-ff", "--no-commit", "other")
        self.write("merged.txt", self.fake + "\n")
        self.commit()
        self.git("rm", "-q", "merged.txt")
        self.commit()
        self.assertEqual(1, self.run_scan()[0])

    def test_unmerged_branch_is_included_in_history(self):
        base = self.git("branch", "--show-current")
        self.git("checkout", "-qb", "other")
        self.write("other.txt", self.fake + "\n")
        self.commit()
        self.git("checkout", "-q", base)
        self.assertEqual(1, self.run_scan()[0])


if __name__ == "__main__":
    unittest.main()
