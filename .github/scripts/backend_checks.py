"""Run the same PostgreSQL/coverage checks locally and in GitHub Actions."""

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from backend_coverage import InvalidCoverage, assess, read_report

ROOT = Path(__file__).resolve().parents[2]


def require_space(path):
    if shutil.disk_usage(path).free < 2 * 1024 ** 3:
        raise OSError("Backend checks require at least 2 GiB free; no data was deleted")


def run(command, cwd, env=None):
    return subprocess.run(command, cwd=cwd, env=env, check=True)


def baseline_commit(repository, event_name, event):
    # A main push compares with the previous main commit, never itself. PRs use
    # origin/main even when the PR's target is another feature branch.
    if event_name == "push":
        if event.get("ref") != "refs/heads/main":
            raise ValueError("Backend push baseline requires refs/heads/main")
        before = event["before"]
        if before == "0" * 40:
            return None
        if not isinstance(before, str) or not re.fullmatch(r"[0-9a-f]{40}", before):
            raise ValueError("Expected a full previous-main SHA")
        ref = before
    else:
        ref = "origin/main"
    return subprocess.check_output(
        ["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=repository, text=True
    ).strip()


@contextmanager
def baseline_checkout(repository, sha):
    if sha is None:
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="ovrly-baseline-") as temporary:
        checkout = Path(temporary) / "source"
        run(["git", "worktree", "add", "--quiet", "--detach", str(checkout), sha], repository)
        try:
            yield checkout
        finally:
            # Only this freshly created disposable worktree contains installed
            # dependencies/reports; no user worktree is removed.
            run(["git", "worktree", "remove", "--force", str(checkout)], repository)


def measure(backend, output, python, config, env):
    output.mkdir(parents=True, exist_ok=True)
    for name in ("coverage.xml", "coverage.json", "junit.xml"):
        (output / name).unlink(missing_ok=True)
    measured_env = {
        **env,
        "COVERAGE_FILE": str(output / ".coverage"),
        "COVERAGE_RCFILE": str(config),
    }
    tests_started = False
    try:
        run([*python, "-m", "coverage", "erase"], backend, measured_env)
        tests_started = True
        run([*python, "-m", "coverage", "run", "-m", "pytest", "-q",
             f"--junitxml={output / 'junit.xml'}"], backend, measured_env)
    finally:
        # Preserve reports from failing tests too. Failures are never converted
        # into a successful baseline or a successful check.
        if tests_started and list(output.glob(".coverage*")):
            run([*python, "-m", "coverage", "combine"], backend, measured_env)
            run([*python, "-m", "coverage", "xml", "-o", str(output / "coverage.xml")],
                backend, measured_env)
            run([*python, "-m", "coverage", "json", "-o", str(output / "coverage.json")],
                backend, measured_env)
    return read_report(output / "coverage.json", backend)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    backend = ROOT / "backend"
    reports = backend / "reports"
    reports.mkdir(exist_ok=True)
    for name in ("summary.md", "comparison.json"):
        (reports / name).unlink(missing_ok=True)
    for name in ("coverage.xml", "coverage.json", "junit.xml"):
        (reports / "main" / name).unlink(missing_ok=True)
    try:
        require_space(ROOT)
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        event = json.loads(Path(event_path).read_text()) if event_path else {}
        sha = baseline_commit(ROOT, os.environ.get("GITHUB_EVENT_NAME", "local"), event)
        config = backend / "pyproject.toml"
        current = measure(backend, reports, [sys.executable], config, os.environ)
        baseline = None
        with baseline_checkout(ROOT, sha) as checkout:
            if checkout is not None:
                base_backend = checkout / "backend"
                if (base_backend / "pyproject.toml").exists():
                    require_space(base_backend)
                    # Use base dependencies, code and tests, with this run's
                    # exact coverage version and measurement configuration.
                    coverage_version = importlib.metadata.version("coverage")
                    python = ["uv", "run", "--frozen", "--with", f"coverage=={coverage_version}",
                              "python"]
                    base_env = {key: value for key, value in os.environ.items()
                                if key not in {"VIRTUAL_ENV", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"}}
                    base_env["UV_LINK_MODE"] = "copy"
                    baseline = measure(base_backend, reports / "main", python, config, base_env)
                elif (base_backend / "services").exists():
                    raise InvalidCoverage("Main contains backend services but no dependency manifest")
        summary, failures = assess(current, baseline, sha or "initial main push")
        (reports / "summary.md").write_text(summary, encoding="utf-8")
        (reports / "comparison.json").write_text(json.dumps({
            "baseline_sha": sha,
            "baseline_available": baseline is not None,
            "failures": failures,
        }, indent=2) + "\n", encoding="utf-8")
        print(summary)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as stream:
                stream.write(summary)
        if failures:
            for failure in failures:
                print(f"ERROR: {failure}", file=sys.stderr)
            return 1
    except InvalidCoverage as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"ERROR: Backend checks could not access a required resource: {error}", file=sys.stderr)
        return 1
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: Backend checks failed ({type(error).__name__}); "
              "see the preceding check output. No passing baseline was assumed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
