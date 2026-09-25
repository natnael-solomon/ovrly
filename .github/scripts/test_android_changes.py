import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from android_changes import is_documentation, is_evaluation, main, needs_android


class DocumentationPathsTest(unittest.TestCase):
    def test_evaluation_boundary(self):
        for name in ["evaluation/validate.py", "evaluation/examples/clips.jsonl",
                     ".github/workflows/evaluation.yml"]:
            with self.subTest(name=name):
                self.assertTrue(is_evaluation(name))
        for name in ["evaluation-other/tool.py", "backend/evaluation/tool.py",
                     ".github/workflows/android.yml", ".github/scripts/android_changes.py"]:
            with self.subTest(name=name):
                self.assertFalse(is_evaluation(name))

    def test_known_documentation(self):
        for name in ["README.md", "WORKFLOW.md", "LICENSE.md", "docs/architecture.md",
                     "docs/nested/a guide.md", "android/README.md", "backend/README.md"]:
            with self.subTest(name=name):
                self.assertTrue(is_documentation(name))

    def test_everything_else_requires_android(self):
        for name in ["android/app/src/main/res/raw/help.md", "android/gradlew",
                     "android/gradle/wrapper/gradle-wrapper.jar", "android/build.gradle.kts",
                     ".github/workflows/android.yml", ".github/scripts/android_changes.py",
                     ".github/dependabot.yml", "scripts/android.ps1", ".gitattributes",
                     ".gitignore", "docs/example.kt", "backend/server.py", "new-file"]:
            with self.subTest(name=name):
                self.assertFalse(is_documentation(name))


class ChangeDetectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.hooksPath", str(self.repo / ".git" / "no-hooks"))
        self.write("README.md")
        self.write("android/app/source.kt")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo).decode().strip()

    def write(self, name, content="example\n"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "test")
        return self.git("rev-parse", "HEAD")

    def required(self, event_name="push", base=None):
        base = base or self.base
        event = {"before": base, "pull_request": {"base": {"sha": base}}}
        return needs_android(event_name, event, self.repo)[0]

    def test_documentation_only_push_and_fork_pr(self):
        self.write("README.md", "changed\n")
        self.commit()
        self.assertFalse(self.required())
        self.assertFalse(self.required("pull_request"))

    def test_evaluation_only_can_skip_android(self):
        self.write("evaluation/validate.py")
        self.write(".github/workflows/evaluation.yml")
        self.commit()
        self.assertFalse(self.required())
        self.assertFalse(self.required("pull_request"))

    def test_mixed_evaluation_and_android_still_requires_checks(self):
        self.write("evaluation/validate.py")
        self.write("android/app/source.kt", "changed\n")
        self.commit()
        self.assertTrue(self.required())

    def test_source_renamed_into_evaluation_requires_checks(self):
        (self.repo / "evaluation").mkdir()
        self.git("mv", "android/app/source.kt", "evaluation/source.kt")
        self.commit()
        self.assertTrue(self.required())

    def test_initial_push_and_manual_dispatch(self):
        self.assertTrue(self.required(base="0" * 40))
        self.assertTrue(needs_android("workflow_dispatch", {}, self.repo)[0])

    def test_empty_diff_does_not_silently_skip(self):
        self.assertTrue(self.required())

    def test_missing_history_runs_checks_with_warning(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(self.required(base="f" * 40))
        self.assertIn("::warning::", output.getvalue())

    def test_missing_or_invalid_payload_fails(self):
        with self.assertRaises(KeyError):
            needs_android("pull_request", {}, self.repo)
        with self.assertRaises(ValueError):
            self.required(base="--output=README.md")

    def test_deleted_source_requires_checks(self):
        (self.repo / "android/app/source.kt").unlink()
        self.commit()
        self.assertTrue(self.required())

    def test_source_renamed_to_documentation_requires_checks(self):
        self.git("mv", "android/app/source.kt", "source.md")
        self.commit()
        self.assertTrue(self.required())

    def test_deleted_documentation_can_skip(self):
        (self.repo / "README.md").unlink()
        self.commit()
        self.assertFalse(self.required())

    def test_entire_multi_commit_diff_is_checked(self):
        self.write("android/app/source.kt", "changed\n")
        self.commit()
        self.write("README.md", "changed\n")
        self.commit()
        self.assertTrue(self.required())

    def test_large_diff_is_not_truncated(self):
        for index in range(350):
            self.write(f"docs/page-{index}.md")
        self.write("scripts/tool.py")
        self.commit()
        self.assertTrue(self.required())

    def test_cli_writes_step_output_and_summary(self):
        self.write("README.md", "changed\n")
        self.commit()
        event_path = self.repo / "event.json"
        event_path.write_text(json.dumps({"before": self.base}), encoding="utf-8")
        output_path = self.repo / "output"
        summary_path = self.repo / "summary"
        with patch("android_changes.os.environ", {
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_WORKSPACE": str(self.repo),
            "GITHUB_OUTPUT": str(output_path),
            "GITHUB_STEP_SUMMARY": str(summary_path),
        }), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual("android=false\n", output_path.read_text(encoding="utf-8"))
        self.assertIn("Android work skipped", summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
