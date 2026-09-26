import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from android_changes import needs_android
from device_evidence import changed_files, main as evidence_main

ROOT = Path(__file__).resolve().parents[2]


class StackedDiffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.hooksPath", str(self.repo / "no-hooks"))
        self.write("README.md")
        self.main = self.commit()
        self.git("checkout", "-qb", "feat/parent")
        self.device = "android/app/src/main/java/app/ovrly/capture/CaptureService.kt"
        self.write(self.device)
        self.parent = self.commit()
        self.git("checkout", "-qb", "feat/child")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo).decode().strip()

    def write(self, name, content="example\n"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def android_required(self, base):
        return needs_android("pull_request", {"pull_request": {"base": {"sha": base}}}, self.repo)[0]

    def test_docs_child_omits_inherited_android_until_retargeted(self):
        self.write("README.md", "child\n")
        self.commit()
        self.assertFalse(self.android_required(self.parent))
        self.assertEqual(["README.md"], changed_files(self.parent, "HEAD", self.repo))
        self.assertTrue(self.android_required(self.main))
        self.assertIn(self.device, changed_files(self.main, "HEAD", self.repo))

    def test_evaluation_child_can_skip_android(self):
        self.write("evaluation/validate.py")
        self.commit()
        self.assertFalse(self.android_required(self.parent))

    def test_child_device_change_requires_both_checks(self):
        self.write(self.device, "child\n")
        self.commit()
        self.assertTrue(self.android_required(self.parent))
        event_path = self.repo / "event.json"
        event_path.write_text(json.dumps({
            "pull_request": {"base": {"sha": self.parent}, "body": ""}
        }), encoding="utf-8")
        with patch.dict(os.environ, {
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_WORKSPACE": str(self.repo),
            "GITHUB_OUTPUT": str(self.repo / "output"),
            "GITHUB_STEP_SUMMARY": str(self.repo / "summary"),
        }), self.assertRaises(SystemExit) as result:
            evidence_main()
        self.assertEqual(1, result.exception.code)


class StackWorkflowTest(unittest.TestCase):
    def test_checks_accept_any_pr_base_and_retargeting(self):
        for name in ("android", "device-evidence", "evaluation"):
            with self.subTest(workflow=name):
                source = (ROOT / ".github/workflows" / f"{name}.yml").read_text()
                section = re.search(r"^  pull_request:\n((?:^    .*\n)+)", source, re.M)
                self.assertIsNotNone(section)
                self.assertNotRegex(section.group(1), r"\b(?:branches|branches-ignore|paths|paths-ignore):")
                for event in ("opened", "synchronize", "reopened", "edited", "ready_for_review"):
                    self.assertIn(event, section.group(1))
                self.assertNotIn("pull_request_target:", source)
                self.assertIn("persist-credentials: false", source)
                if name != "device-evidence":
                    self.assertIn("  push:\n    branches: [main]", source)
                else:
                    self.assertNotIn("  push:", source)


if __name__ == "__main__":
    unittest.main()
