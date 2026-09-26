"""Skip Android work only for known documentation or isolated evaluation paths."""

import json
import os
from pathlib import Path

from ci_changes import changed_paths, is_documentation


def is_evaluation(name):
    return name.startswith("evaluation/") or name == ".github/workflows/evaluation.yml"


def needs_android(event_name, event, repository):
    # Compare against the checked-out merge result for PRs, not just the PR tip.
    names, reason = changed_paths(event_name, event, repository)
    if names is None:
        return True, f"{reason}: running all Android checks."
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
