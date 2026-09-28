import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend_checks
from backend_checks import baseline_checkout, baseline_commit, measure


class BaselineSelectionTest(unittest.TestCase):
    def test_stacked_pr_uses_main_not_parent(self):
        with patch("backend_checks.subprocess.check_output", return_value="a" * 40 + "\n") as git:
            result = baseline_commit(Path("/repo"), "pull_request", {
                "pull_request": {"base": {"sha": "b" * 40}}
            })
        self.assertEqual("a" * 40, result)
        self.assertEqual("origin/main^{commit}", git.call_args.args[0][-1])

    def test_main_push_uses_previous_main_not_its_new_head(self):
        with patch("backend_checks.subprocess.check_output", return_value="b" * 40 + "\n") as git:
            baseline_commit(Path("/repo"), "push", {"ref": "refs/heads/main", "before": "b" * 40})
        self.assertEqual("b" * 40 + "^{commit}", git.call_args.args[0][-1])
        self.assertIsNone(baseline_commit(Path("/repo"), "push", {
            "ref": "refs/heads/main", "before": "0" * 40,
        }))

    def test_bad_push_or_unknown_main_never_becomes_missing_baseline(self):
        for event in ({"ref": "refs/heads/feature", "before": "a" * 40},
                      {"ref": "refs/heads/main", "before": "--bad"}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                baseline_commit(Path("/repo"), "push", event)
        with patch("backend_checks.subprocess.check_output",
                   side_effect=subprocess.CalledProcessError(128, ["git"])):
            with self.assertRaises(subprocess.CalledProcessError):
                baseline_commit(Path("/repo"), "pull_request", {})

    def test_owned_worktree_removed_even_on_failure(self):
        with patch("backend_checks.run") as runner:
            with self.assertRaisesRegex(RuntimeError, "fixture"):
                with baseline_checkout(Path("/repo"), "a" * 40):
                    raise RuntimeError("fixture")
        self.assertEqual("add", runner.call_args_list[0].args[0][2])
        self.assertEqual("remove", runner.call_args_list[1].args[0][2])
        self.assertEqual(runner.call_args_list[0].args[0][-2],
                         runner.call_args_list[1].args[0][-1])


class MeasurementTest(unittest.TestCase):
    def test_failing_tests_still_emit_reports_but_do_not_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "reports"

            def command(args, cwd, env):
                if "run" in args:
                    (output / ".coverage.fixture").write_text("fixture")
                    raise subprocess.CalledProcessError(1, args)

            with patch("backend_checks.run", side_effect=command) as runner:
                with self.assertRaises(subprocess.CalledProcessError):
                    measure(Path(temporary), output, ["python"], Path("config"), {})
            commands = [call.args[0] for call in runner.call_args_list]
            self.assertTrue(any("combine" in args for args in commands))
            self.assertTrue(any("xml" in args for args in commands))
            self.assertTrue(any("json" in args for args in commands))

    def test_measure_removes_old_success_reports_before_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            for name in ("coverage.xml", "coverage.json", "junit.xml"):
                (output / name).write_text("old success")
            (output / ".coverage.stale").write_text("old data")
            with patch("backend_checks.run",
                       side_effect=subprocess.CalledProcessError(1, ["coverage"])) as runner:
                with self.assertRaises(subprocess.CalledProcessError):
                    measure(output, output, ["python"], Path("config"), {})
            self.assertEqual(1, runner.call_count)
            for name in ("coverage.xml", "coverage.json", "junit.xml"):
                self.assertFalse((output / name).exists())

    def test_disk_budget_refuses_work_without_deleting_data(self):
        with patch("backend_checks.shutil.disk_usage") as usage:
            usage.return_value.free = 2 * 1024 ** 3 - 1
            with self.assertRaisesRegex(OSError, "2 GiB"):
                backend_checks.require_space(Path("/repo"))

    def test_missing_main_project_is_explicit_not_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "backend").mkdir()
            current = {
                "totals": {"covered_lines": 95, "num_statements": 100},
                "files": {"services/worker/runtime.py": {
                    "summary": {"covered_lines": 95, "num_statements": 100}
                }},
            }
            @contextlib.contextmanager
            def checkout(*args):
                yield root / "old-main"

            with patch.object(backend_checks, "ROOT", root), \
                    patch("backend_checks.baseline_commit", return_value="a" * 40), \
                    patch("backend_checks.baseline_checkout", checkout), \
                    patch("backend_checks.measure", return_value=current), \
                    patch.dict(os.environ, {}, clear=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, backend_checks.main([]))
            comparison = json.loads((root / "backend/reports/comparison.json").read_text())
            self.assertFalse(comparison["baseline_available"])
            self.assertIn("not available", (root / "backend/reports/summary.md").read_text())

    def test_baseline_uses_isolated_environment_and_failures_stay_failures(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "backend").mkdir()
                base = root / "old-main"
                (base / "backend").mkdir(parents=True)
                (base / "backend/pyproject.toml").write_text("[project]\n")
                current = {
                    "totals": {"covered_lines": 95, "num_statements": 100},
                    "files": {"services/worker/runtime.py": {
                        "summary": {"covered_lines": 95, "num_statements": 100}
                    }},
                }
                baseline = subprocess.CalledProcessError(1, ["pytest"]) if failed else current
                with patch.object(backend_checks, "ROOT", root), \
                        patch("backend_checks.baseline_commit", return_value="a" * 40), \
                        patch("backend_checks.baseline_checkout",
                              return_value=contextlib.nullcontext(base)), \
                        patch("backend_checks.importlib.metadata.version",
                              return_value="7.16.1"), \
                        patch("backend_checks.measure",
                              side_effect=[current, baseline]) as measurement, \
                        patch.dict(os.environ, {
                            "VIRTUAL_ENV": "/head/.venv", "PYTHONPATH": "/head",
                            "UV_PROJECT_ENVIRONMENT": "/head/.venv",
                            "OVRLY_TEST_DATABASE_URL": "test-fixture",
                        }, clear=True), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(int(failed), backend_checks.main([]))
                args = measurement.call_args.args
                self.assertEqual(["uv", "run", "--frozen", "--with",
                                  "coverage==7.16.1", "python"], args[2])
                self.assertEqual(root / "backend/pyproject.toml", args[3])
                self.assertEqual({
                    "OVRLY_TEST_DATABASE_URL": "test-fixture", "UV_LINK_MODE": "copy",
                }, args[4])
                comparison_path = root / "backend/reports/comparison.json"
                if failed:
                    self.assertFalse(comparison_path.exists())
                    self.assertIn("No passing baseline was assumed", errors.getvalue())
                else:
                    self.assertTrue(json.loads(comparison_path.read_text())["baseline_available"])


if __name__ == "__main__":
    unittest.main()
