"""Skip backend execution only for an entirely known-documentation diff."""

import json
import os
from pathlib import Path

from ci_changes import changed_paths, is_documentation


def needs_backend(event_name, event, repository):
    names, reason = changed_paths(event_name, event, repository)
    if names is None:
        return True, f"{reason}: running all backend checks."
    if not names or any(not is_documentation(name) for name in names):
        return True, "Code, tooling, unknown paths or an empty diff: full backend checks."
    return False, f"Only documentation changed ({len(names)} paths); backend work skipped."


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    required, reason = needs_backend(
        os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_WORKSPACE"]
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"backend={str(required).lower()}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"### Backend change detection\n\n{reason}\n")
    print(reason)


if __name__ == "__main__":
    main()
