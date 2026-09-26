"""Exercise policy and real negative fixtures without providers, GitHub tokens or a database."""

import importlib.metadata
import os
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = __name__ == "__main__" or os.environ.get("OVRLY_REQUIRE_QUALITY_TOOLS") == "1"


def load_yaml(path):
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


class QualityConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if yaml is None:
            if REQUIRED:
                raise RuntimeError("Quality checks require the locked quality dependency group")
            raise unittest.SkipTest("PyYAML is available in the quality dependency group")

    def test_missing_tools_cannot_skip_required_enforcement(self):
        with patch(__name__ + ".REQUIRED", True), patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "cannot skip"):
                QualityToolTest.setUpClass()

    def test_backend_rules_do_not_blanket_disable_security(self):
        config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
        lint = config["tool"]["ruff"]["lint"]
        self.assertTrue({"S", "PGH003"}.issubset(lint["select"]))
        self.assertNotIn("ignore", lint)
        self.assertEqual({"tests/**/*.py": ["S101"]}, lint["per-file-ignores"])
        self.assertIn("ignore-without-code", config["tool"]["mypy"]["enable_error_code"])

    def test_hooks_are_local_locked_and_run_even_on_config_only_changes(self):
        config = load_yaml(ROOT / ".pre-commit-config.yaml")
        self.assertEqual(["local"], [repo["repo"] for repo in config["repos"]])
        hooks = config["repos"][0]["hooks"]
        self.assertEqual({"backend-ruff", "backend-format", "backend-types", "actionlint", "zizmor",
                          "quality-policy"},
                         {hook["id"] for hook in hooks})
        for hook in hooks:
            with self.subTest(hook=hook["id"]):
                self.assertEqual("system", hook["language"])
                self.assertEqual("false", hook["pass_filenames"])
                self.assertEqual("true", hook["always_run"])
                args = shlex.split(hook["entry"])
                self.assertEqual(["uv", "run"], args[:2])
                self.assertIn("--frozen", args)
        entries = {hook["id"]: shlex.split(hook["entry"]) for hook in hooks}
        self.assertTrue({"--offline", "--strict-collection", "--persona", "regular",
                         "workflows", "actions"}.issubset(entries["zizmor"]))
        self.assertNotIn("--exit-zero", entries["backend-ruff"])
        self.assertNotIn("--fix", entries["backend-ruff"])

    def test_ci_uses_same_hooks_on_all_pr_targets_without_secrets(self):
        workflow = load_yaml(ROOT / ".github/workflows/quality.yml")
        self.assertEqual({"pull_request", "push", "workflow_dispatch"}, set(workflow["on"]))
        self.assertEqual({"types"}, set(workflow["on"]["pull_request"]))
        self.assertIn("edited", workflow["on"]["pull_request"]["types"])
        self.assertEqual(["main"], workflow["on"]["push"]["branches"])
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        job = workflow["jobs"]["quality"]
        self.assertEqual("Quality checks", job["name"])
        self.assertEqual("1", job["env"]["OVRLY_REQUIRE_QUALITY_TOOLS"])
        commands = [step.get("run", "") for step in job["steps"]]
        self.assertIn("uv run --project backend --frozen --group quality pre-commit run --all-files",
                      commands)
        for step in job["steps"]:
            self.assertNotIn("continue-on-error", step)
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[\da-f]{40}$")
        source = (ROOT / ".github/workflows/quality.yml").read_text()
        self.assertNotIn("secrets.", source)
        self.assertIn("save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}",
                      source)

    def test_queue_exception_cannot_hide_other_concurrency_shapes(self):
        config = load_yaml(ROOT / ".github/actionlint.yaml")
        self.assertEqual([".github/workflows/telegram-apk.yml"], list(config["paths"]))
        ignores = config["paths"][".github/workflows/telegram-apk.yml"]["ignore"]
        self.assertEqual(
            ['^unexpected key "queue" for "concurrency" section\\. expected one of '
             '"cancel-in-progress", "group"$'], ignores,
        )
        workflow = load_yaml(ROOT / ".github/workflows/telegram-apk.yml")
        self.assertNotIn("queue", workflow.get("concurrency", {}))
        self.assertEqual({"group": "ovrly-production-publish",
                          "cancel-in-progress": "false", "queue": "max"},
                         workflow["jobs"]["publish"]["concurrency"])
        for name, job in workflow["jobs"].items():
            if name != "publish":
                self.assertNotIn("queue", job.get("concurrency", {}))

    def test_notifier_exception_keeps_workflow_revision_and_no_run_artifacts(self):
        workflow = load_yaml(ROOT / ".github/workflows/telegram-notify.yml")
        self.assertEqual({"workflows": ["Android CI"], "types": ["completed"]},
                         workflow["on"]["workflow_run"])
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        steps = workflow["jobs"]["notify"]["steps"]
        self.assertEqual(2, len(steps))
        self.assertTrue(steps[0]["uses"].startswith("actions/checkout@"))
        self.assertEqual("${{ github.workflow_sha }}", steps[0]["with"]["ref"])
        self.assertEqual("false", steps[0]["with"]["persist-credentials"])
        self.assertEqual("python3 .github/scripts/telegram_notify.py", steps[1]["run"])


class QualityToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = {name: shutil.which(name) for name in ("ruff", "mypy", "actionlint", "zizmor")}
        if not all(cls.tools.values()):
            if REQUIRED:
                raise RuntimeError("Install the locked quality tools; enforcement tests cannot skip")
            raise unittest.SkipTest("Real linter fixtures run in the dedicated Quality checks job")

    def run_tool(self, tool, *args, source=None):
        return subprocess.run([self.tools[tool], *args], cwd=ROOT, input=source,
                              text=True, capture_output=True, timeout=60)

    def test_actionlint_version_matches_locked_distribution(self):
        version = importlib.metadata.version("actionlint-py")
        result = self.run_tool("actionlint", "-version")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(version.rsplit(".", 1)[0], result.stdout.splitlines()[0])

    def test_ruff_security_and_ignore_codes_fail_but_pytest_assertions_pass(self):
        cases = [
            ("backend/services/fixture.py", 'eval("1 + 1")\n', "S307"),
            ("backend/services/fixture.py", "assert True\n", "S101"),
            ("backend/services/fixture.py", "x = 1  # type: ignore\n", "PGH003"),
            ("backend/tests/fixture.py", 'eval("1 + 1")\n', "S307"),
            ("backend/tests/fixture.py", "assert True\n", None),
            ("backend/services/fixture.py", "x = 1  # type: ignore[assignment]\n", None),
        ]
        for name, source, expected in cases:
            with self.subTest(name=name, source=source):
                result = self.run_tool("ruff", "check", "--stdin-filename", name, "-", source=source)
                self.assertEqual(1 if expected else 0, result.returncode, result.stdout + result.stderr)
                if expected:
                    self.assertIn(expected, result.stdout)

    def test_mypy_rejects_blanket_ignore_and_accepts_specific_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.py"
            for suffix, expected in (("", 1), ("[assignment]", 0)):
                path.write_text(f"x: int = 'text'  # type: ignore{suffix}\n")
                result = self.run_tool("mypy", "--config-file", "backend/pyproject.toml",
                                       "--no-incremental", str(path))
                self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
                if expected:
                    self.assertIn("ignore-without-code", result.stdout)

    def test_actionlint_rejects_invalid_expressions_and_unknown_keys(self):
        for bad in ("${{ nonexistent.value }}", "hello"):
            source = f"""name: fixture
on: push
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - run: echo "{bad}"
"""
            result = self.run_tool("actionlint", "-shellcheck=", "-pyflakes=", "-", source=source)
            self.assertEqual(1 if "nonexistent" in bad else 0, result.returncode,
                             result.stdout + result.stderr)
        result = self.run_tool("actionlint", "-shellcheck=", "-pyflakes=", "-",
                               source=source.replace("runs-on:", "unknown-key:"))
        self.assertNotEqual(0, result.returncode)

    def test_zizmor_rejects_unpinned_actions_and_template_injection(self):
        safe = """name: fixture
on: pull_request
permissions:
  contents: read
jobs:
  check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          persist-credentials: false
      - run: echo safe
"""
        for source, finding in (
            (safe, None),
            (safe.replace("@3d3c42e5aac5ba805825da76410c181273ba90b1", "@main"), "unpinned-uses"),
            (safe.replace("echo safe", 'echo "${{ github.event.pull_request.title }}"'),
             "template-injection"),
        ):
            result = self.run_tool("zizmor", "--offline", "--strict-collection", "--no-progress",
                                   "--format", "plain", "-", source=source)
            if finding:
                self.assertNotEqual(0, result.returncode)
                self.assertIn(finding, result.stdout)
            else:
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_zizmor_parse_errors_fail_instead_of_disappearing(self):
        result = self.run_tool("zizmor", "--offline", "--strict-collection", "--no-progress", "-",
                               source="on: [\n")
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
