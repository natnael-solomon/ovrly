"""Run the same check-only Gradle gates on Windows, Linux and macOS."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def gradle_command(*args):
    wrapper = ["cmd.exe", "/d", "/c", r".\gradlew.bat"] if os.name == "nt" else ["sh", "gradlew"]
    return [*wrapper, "--no-daemon", "--console=plain", "--dependency-verification=strict", *args]


def main():
    try:
        return subprocess.run(
            gradle_command("--build-cache", "qualityCheck"),
            cwd=ROOT / "android", check=False, timeout=1200,
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"ERROR: Android quality checks could not run: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
