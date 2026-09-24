"""Decide whether a pull request needs physical-device evidence and whether it has it.

Paths under android/app/src/main/java/app/ovrly/{capture,overlay,voice} change behaviour that
CI cannot exercise (MediaProjection, playback capture, overlay windows, microphone). A PR that
touches them must carry at least one filled row in the "Device evidence" table of its body.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEVICE_PREFIXES = (
    "android/app/src/main/java/app/ovrly/capture/",
    "android/app/src/main/java/app/ovrly/overlay/",
    "android/app/src/main/java/app/ovrly/voice/",
    "android/app/src/main/AndroidManifest.xml",
)
LABEL = "needs-device-evidence"


def touches_device_paths(names):
    return any(name.startswith(DEVICE_PREFIXES) for name in names)


def evidence_rows(body):
    """Return the filled data rows of the first table under a '## Device evidence' heading."""
    body = body or ""
    match = re.search(r"^##\s+Device evidence\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    if not match:
        return []
    section = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S)
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0].lower() == "device":
            continue  # header
        if sum(1 for c in cells if c) >= 3:
            rows.append(cells)
    return rows


def explicitly_not_applicable(body):
    match = re.search(r"^##\s+Device evidence\s*$(.*?)(?=^##\s|\Z)", body or "", re.M | re.S)
    if not match:
        return False
    text = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.S)
    return re.search(r"\bnot applicable\b", text, re.I) is not None


def changed_files(base, head, repository):
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base, head, "--"],
        cwd=repository, capture_output=True, check=True,
    )
    return [n.decode("utf-8") for n in diff.stdout.split(b"\0") if n]


def evaluate(names, body):
    """Return (needs_label, passes, reason)."""
    if not touches_device_paths(names):
        return False, True, "No capture, overlay, voice or manifest paths changed; device evidence not required."
    rows = evidence_rows(body)
    if rows:
        return True, True, f"Device paths changed and the PR carries {len(rows)} device-evidence row(s)."
    if explicitly_not_applicable(body):
        return True, False, (
            "Device paths changed but the Device evidence section says 'Not applicable'. "
            "Capture, overlay, voice and manifest changes always need a device check; "
            "fill the table or explain in Limitations and ask a reviewer to override."
        )
    return True, False, (
        "Device paths changed but the Device evidence table in the PR body is empty. "
        "Add at least one row (model, Android version, route, what was verified, result)."
    )


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    pr = event["pull_request"]
    names = changed_files(pr["base"]["sha"], "HEAD", os.environ["GITHUB_WORKSPACE"])
    needs_label, passes, reason = evaluate(names, pr.get("body"))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        out.write(f"needs_label={str(needs_label).lower()}\n")
        out.write(f"label={LABEL}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"### Device evidence\n\n{reason}\n")
    print(reason)
    if not passes:
        print(f"::error::{reason}")
        sys.exit(1)


if __name__ == "__main__":
    main()
