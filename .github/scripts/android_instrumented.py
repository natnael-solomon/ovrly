"""Run connected Android tests, retry failed tests once and report flakes.

Flake policy (#37, #76): a failing test is retried once in the same job. A test that passes
on the retry is a flake: the job passes, and the job summary and a warning name it. A test
that flakes twice in 48 hours, or twice on one PR, is quarantined with @Ignore and an issue
on the same day. Jobs are never re-run to get a green result.

Each attempt's JUnit XML, HTML report and coverage data are moved under
app/build/instrumented/attempt-N/ so the retry cannot overwrite them; jacocoDebugReport reads
coverage from every attempt.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"
BUILD = ANDROID / "app" / "build"
OUTPUTS = {
    "results": BUILD / "outputs" / "androidTest-results" / "connected",
    "coverage": BUILD / "outputs" / "code_coverage" / "debugAndroidTest" / "connected",
    "report": BUILD / "reports" / "androidTests" / "connected",
}
ATTEMPTS = BUILD / "instrumented"
GRADLE = [
    "sh", "gradlew", "--no-daemon", "--console=plain", "--build-cache",
    "--dependency-verification=strict", "-Povrly.coverage=true",
    ":app:connectedDebugAndroidTest",
]
CLASS_ARGUMENT = "-Pandroid.testInstrumentationRunnerArguments.class="
PASSED, FAILED, SKIPPED = "passed", "failed", "skipped"
POLICY = (
    "Flake policy: one automatic retry. A test that flakes twice in 48 hours, or twice on "
    "one PR, is quarantined with `@Ignore` and an issue on the same day. Never re-run a job "
    "to get a green result."
)


def test_cases(results):
    """Map `class#method` to passed, failed or skipped from every JUnit XML under results."""
    cases = {}
    for path in sorted(Path(results).rglob("*.xml")):
        root = ET.parse(path).getroot()
        for case in root.iter("testcase"):
            name = f"{case.get('classname')}#{case.get('name')}"
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = FAILED
            elif case.find("skipped") is not None:
                outcome = SKIPPED
            else:
                outcome = PASSED
            # A test reported twice counts as failed if either report failed.
            if cases.get(name) != FAILED:
                cases[name] = outcome
    return cases


def failures(cases):
    return sorted(name for name, outcome in cases.items() if outcome == FAILED)


def plan_retry(returncode, cases):
    """Return (tests to retry, reason); no retry when nothing failed or no test failed."""
    if returncode == 0:
        return [], "All instrumented tests passed on the first attempt."
    failed = failures(cases)
    if not failed:
        return [], (
            "Gradle failed without a failing test (build, install or instrumentation crash); "
            "not retried."
        )
    return failed, f"{len(failed)} test(s) failed on the first attempt; retrying them once."


def classify(first, second):
    """Split first-attempt failures into flakes (passed on retry) and real failures."""
    flaky, failed = [], []
    for name in failures(first):
        (flaky if second.get(name) == PASSED else failed).append(name)
    return flaky, failed


def collect_attempt(number):
    """Move one attempt's outputs out of the way of the next Gradle run."""
    target = ATTEMPTS / f"attempt-{number}"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name, source in OUTPUTS.items():
        if source.exists():
            shutil.move(str(source), str(target / name))
    return test_cases(target / "results")


def run_attempt(number, extra, runner=subprocess.run):
    for source in OUTPUTS.values():
        shutil.rmtree(source, ignore_errors=True)
    returncode = runner([*GRADLE, *extra], cwd=ANDROID, check=False).returncode
    return returncode, collect_attempt(number)


def evaluate(first_code, first, second_code=None, second=None):
    """Return (passed, flaky, failed, reason) for one or two attempts."""
    retry, reason = plan_retry(first_code, first)
    if first_code == 0:
        if not first:
            return False, [], ["(no instrumented test results)"], "No test results were found."
        return True, [], [], reason
    if not retry or second is None:
        return False, [], retry or ["(Gradle failure without a failing test)"], reason
    flaky, failed = classify(first, second)
    if second_code != 0 and not failed:
        failed = ["(retry failed without a failing test)"]
    return not failed, flaky, failed, reason


def summary(label, first, second, flaky, failed, reason):
    counted = {outcome: sum(1 for value in first.values() if value == outcome)
               for outcome in (PASSED, FAILED, SKIPPED)}
    lines = [
        f"### Android instrumented tests ({label})",
        "",
        f"First attempt: {counted[PASSED]} passed, {counted[FAILED]} failed, "
        f"{counted[SKIPPED]} skipped. {reason}",
    ]
    if second is not None:
        lines.append(f"Retry: {len(second)} test(s) run.")
    if flaky:
        lines += ["", "**Flaky (failed, then passed on the retry):**", ""]
        lines += [f"- `{name}`" for name in flaky]
    if failed:
        lines += ["", "**Failed:**", ""]
        lines += [f"- `{name}`" for name in failed]
    lines += ["", POLICY, ""]
    return "\n".join(lines)


def main(argv=None, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Device label, e.g. 'API 34'")
    args = parser.parse_args(argv)
    shutil.rmtree(ATTEMPTS, ignore_errors=True)
    first_code, first = run_attempt(1, [], runner)
    retry, _ = plan_retry(first_code, first)
    second_code = second = None
    if retry:
        second_code, second = run_attempt(2, [CLASS_ARGUMENT + ",".join(retry)], runner)
    passed, flaky, failed, reason = evaluate(first_code, first, second_code, second)
    text = summary(args.label, first, second, flaky, failed, reason)
    (ATTEMPTS / "result.json").write_text(json.dumps({
        "label": args.label,
        "passed": passed,
        "flaky": flaky,
        "failed": failed,
        "first_attempt": first,
        "retry": second,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(text)
    for name in flaky:
        print(f"::warning::Flaky instrumented test on {args.label}: {name}")
    for name in failed:
        print(f"::error::Instrumented test failed on {args.label}: {name}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
