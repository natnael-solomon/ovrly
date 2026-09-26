"""Shared conservative Git diff and documentation classification for CI."""

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
