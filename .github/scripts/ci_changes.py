"""Shared conservative Git diff and path classification for CI."""

import re
import subprocess
from pathlib import PurePosixPath


def is_documentation(name):
    path = PurePosixPath(name)
    return (
        (len(path.parts) == 1 and path.suffix == ".md")
        or (name.startswith("docs/") and path.suffix == ".md")
        or name in {"android/README.md", "backend/README.md"}
    )


# Paths that only one side builds or tests. Anything else, including packages/contracts,
# evaluation inputs read by backend tests, shared CI scripts and unknown paths, runs both.
ANDROID_ONLY_WORKFLOWS = {
    ".github/workflows/android.yml",
    ".github/workflows/android-instrumented.yml",
}
BACKEND_ONLY_WORKFLOWS = {".github/workflows/backend.yml"}


def _ci_script(name, prefix):
    path = PurePosixPath(name)
    return (
        len(path.parts) == 3
        and name.startswith(".github/scripts/")
        and path.suffix == ".py"
        and (path.name.startswith(prefix) or path.name.startswith("test_" + prefix))
    )


def is_android_only(name):
    return (
        name.startswith("android/")
        or name in ANDROID_ONLY_WORKFLOWS
        or _ci_script(name, "android_")
    )


def is_backend_only(name):
    return (
        name.startswith("backend/")
        or name in BACKEND_ONLY_WORKFLOWS
        or _ci_script(name, "backend_")
    )


def changed_paths(event_name, event, repository):
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    elif event_name == "push":
        base = event["before"]
        if base == "0" * 40:
            return None, "Initial push"
    else:
        return None, "Manual or other event"
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Expected a full base commit SHA in the event payload")
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
         "--name-only", "-z", base, "HEAD", "--"],
        cwd=repository, capture_output=True, check=False,
    )
    if diff.returncode:
        print("::warning::Could not compare commits; running all checks.")
        return None, "Diff unavailable"
    return [name.decode("utf-8") for name in diff.stdout.split(b"\0") if name], ""
