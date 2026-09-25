"""Skip Android work only for known documentation or isolated evaluation paths."""

import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath


def is_documentation(name):
    path = PurePosixPath(name)
    return (
        (len(path.parts) == 1 and path.suffix == ".md")
        or (name.startswith("docs/") and path.suffix == ".md")
        or name in {"android/README.md", "backend/README.md"}
    )


def is_evaluation(name):
    return name.startswith("evaluation/") or name == ".github/workflows/evaluation.yml"


def needs_android(event_name, event, repository):
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
    elif event_name == "push":
        base = event["before"]
        if base == "0" * 40:
            return True, "Initial push: running all Android checks."
    else:
        return True, "Manual or other event: running all Android checks."

    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("Expected a full base commit SHA in the event payload")

    # Compare against the checked-out merge result for PRs, not just the PR tip.
    diff = subprocess.run(
        [
            "git", "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
            "--name-only", "-z", base, "HEAD", "--",
        ],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if diff.returncode:
        print("::warning::Could not compare commits; running all Android checks.")
        return True, "Diff unavailable (for example, after a force push): full checks."

    names = [name.decode("utf-8") for name in diff.stdout.split(b"\0") if name]
    if not names or any(not (is_documentation(name) or is_evaluation(name)) for name in names):
        return True, "Code, tooling, unknown paths or an empty diff: full checks."
    return False, f"Only documentation/evaluation changed ({len(names)} paths); Android work skipped."


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    required, reason = needs_android(
        os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_WORKSPACE"]
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"android={str(required).lower()}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"### Android checks\n\n{reason}\n")
    print(reason)


if __name__ == "__main__":
    main()
