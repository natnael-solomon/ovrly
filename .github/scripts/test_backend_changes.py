import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend_changes import main, needs_backend


class BackendChangesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.hooksPath", str(self.repo / "no-hooks"))
        self.write("README.md")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

    def write(self, name, content="example\n"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def required(self, base=None, event_name="pull_request"):
        sha = self.base if base is None else base
        event = {"before": sha, "pull_request": {"base": {"sha": sha}}}
        return needs_backend(event_name, event, self.repo)[0]

    def test_docs_only_push_and_pr_skip(self):
        self.write("docs/new.md")
        self.write("backend/README.md")
        self.commit()
        self.assertFalse(self.required())
        self.assertFalse(self.required(event_name="push"))

    def test_all_nondocumentation_including_evaluation_requires_backend(self):
        for name in ["backend/services/app.py", "backend/uv.lock", "android/app.kt",
                     "evaluation/validate.py", ".github/workflows/backend.yml",
                     ".github/scripts/ci_changes.py", ".gitignore", "unknown"]:
            with self.subTest(name=name):
                self.write(name)
                self.commit()
                self.assertTrue(self.required())
                self.base = self.git("rev-parse", "HEAD")

    def test_empty_initial_and_manual_run_checks(self):
        self.assertTrue(self.required())
        self.assertTrue(self.required(base="0" * 40, event_name="push"))
        self.assertTrue(needs_backend("workflow_dispatch", {}, self.repo)[0])

    def test_unknown_history_warns_and_checks(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(self.required(base="f" * 40))
        self.assertIn("::warning::", output.getvalue())

    def test_invalid_payload_fails(self):
        with self.assertRaises(KeyError):
            needs_backend("pull_request", {}, self.repo)
        with self.assertRaises(ValueError):
            self.required(base="--output=README.md")

    def test_rename_and_delete_preserve_source_detection(self):
        self.write("backend/server.py")
        parent = self.commit()
        (self.repo / "docs").mkdir()
        self.git("mv", "backend/server.py", "docs/moved.md")
        self.commit()
        self.assertTrue(self.required(parent))

    def test_large_multicommit_diff_is_not_truncated(self):
        self.write("backend/server.py")
        self.commit()
        for index in range(350):
            self.write(f"docs/{index}.md")
        self.commit()
        self.assertTrue(self.required())

    def test_stacked_base_and_retarget(self):
        self.write("backend/server.py")
        parent = self.commit()
        self.write("README.md", "child\n")
        self.commit()
        self.assertFalse(self.required(parent))
        self.assertTrue(self.required(self.base))

    def test_cli_reports_docs_skip(self):
        self.write("README.md", "docs\n")
        self.commit()
        payload = self.repo / "event.json"
        payload.write_text(json.dumps({"pull_request": {"base": {"sha": self.base}}}))
        with patch.dict(os.environ, {
            "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": str(payload),
            "GITHUB_WORKSPACE": str(self.repo), "GITHUB_OUTPUT": str(self.repo / "output"),
            "GITHUB_STEP_SUMMARY": str(self.repo / "summary"),
        }), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual("backend=false\n", (self.repo / "output").read_text())
        self.assertIn("backend work skipped", (self.repo / "summary").read_text())


if __name__ == "__main__":
    unittest.main()
