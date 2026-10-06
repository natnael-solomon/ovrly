"""Run connected Android tests, retry failed tests once and report flakes.

Flake policy (#37, #76): a failing test is retried once in the same job. A test that passes
on the retry is a flake: the job passes, and the job summary and a warning name it. A test
that flakes twice in 48 hours, or twice on one PR, is quarantined with @Ignore and an issue
on the same day. Jobs are never re-run to get a green result.

Every test declared in app/src/androidTest must appear in the first attempt's results. Without
that check a process crash or an aborted run, which drops the remaining tests from the XML,
followed by a passing retry of the one reported failure, would look green. A first attempt
with missing or undeclared tests fails without a retry. The orchestrator (build.gradle.kts)
keeps a crash from dropping later tests in the first place.

Hangs fail fast and name the test: the runner applies a per-test timeout (timeout_msec), and
all Gradle runs share a time budget. A run that exceeds it is killed with its process group,
logcat and process state are saved next to its results, and the test that had started but
not finished is reported; nothing is retried after a timeout. Before the first run the device
must have booted with its system services published, and the emulator is always stopped here
(gracefully, then killed) so that a hung emulator shutdown cannot hold the job.

The first attempt's JUnit XML, HTML report and coverage data are moved under
app/build/instrumented/attempt-1/ and each retried test's under attempt-2/run-N/, so no run
overwrites another; jacocoDebugReport reads coverage from every run.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import android_emulator

ROOT = Path(__file__).resolve().parents[2]
ANDROID = ROOT / "android"
BUILD = ANDROID / "app" / "build"
OUTPUTS = {
    "results": BUILD / "outputs" / "androidTest-results" / "connected",
    "coverage": BUILD / "outputs" / "code_coverage" / "debugAndroidTest" / "connected",
    "report": BUILD / "reports" / "androidTests" / "connected",
}
ATTEMPTS = BUILD / "instrumented"
SOURCES = ANDROID / "app" / "src" / "androidTest" / "java"
# The 3-minute capture limit test runs for about 3.5 minutes; nothing else comes close.
TEST_TIMEOUT_MS = 300_000
TEST_BUDGET_S = 14 * 60
KILL_GRACE_S = 15
GRADLE = [
    "sh", "gradlew", "--no-daemon", "--console=plain", "--build-cache",
    "--dependency-verification=strict", "-Povrly.coverage=true",
    f"-Pandroid.testInstrumentationRunnerArguments.timeout_msec={TEST_TIMEOUT_MS}",
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


CLASS = re.compile(
    r"^[ \t]*(?:(?:public|internal|open|final|abstract)\s+)*class\s+(\w+)", re.M
)
TEST = re.compile(
    r"@Test\b(?:\s*\([^)]*\))?(?:\s+@[\w.]+(?:\([^)]*\))?)*"
    r"\s+(?:fun\s+`?(\w+)|(?:public\s+)?void\s+(\w+)\s*\()"
)


def expected_tests(sources=None):
    """Every `package.Class#method` with @Test under the androidTest sources."""
    tests = set()
    for path in sorted(Path(sources or SOURCES).rglob("*")):
        if path.suffix not in (".kt", ".java"):
            continue
        text = path.read_text(encoding="utf-8")
        package = re.search(r"^package\s+([\w.]+)", text, re.M)
        classes = [(match.start(), match.group(1)) for match in CLASS.finditer(text)]
        for match in TEST.finditer(text):
            owner = [name for start, name in classes if start < match.start()]
            if not owner:
                raise ValueError(f"@Test outside a class in {path}")
            prefix = f"{package.group(1)}." if package else ""
            tests.add(f"{prefix}{owner[-1]}#{match.group(1) or match.group(2)}")
    if not tests:
        raise ValueError("No instrumented tests were found in the sources")
    return tests


def inventory_problems(expected, cases):
    """Declared tests missing from the results, and reported tests nobody declared."""
    missing = sorted(set(expected) - set(cases))
    unknown = sorted(set(cases) - set(expected))
    return ([f"(not run) {name}" for name in missing]
            + [f"(not declared in the sources) {name}" for name in unknown])


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


def collect_run(target):
    """Move one Gradle run's outputs to target, out of the way of the next run."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name, source in OUTPUTS.items():
        if source.exists():
            shutil.move(str(source), str(target / name))
    return test_cases(target / "results")


def run_with_timeout(command, cwd, timeout):
    """Run command in its own process group; kill the whole group if it outlives timeout."""
    process = subprocess.Popen(command, cwd=cwd, start_new_session=True)
    try:
        return SimpleNamespace(returncode=process.wait(timeout=timeout), timed_out=False)
    except subprocess.TimeoutExpired:
        for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, None)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                break
            try:
                process.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        process.wait()
        return SimpleNamespace(returncode=process.returncode, timed_out=True)


class Run(SimpleNamespace):
    """One Gradle run: returncode, cases, and the test left running if it timed out."""


def run_gradle(target, extra, runner, timeout, emulator):
    for source in OUTPUTS.values():
        shutil.rmtree(source, ignore_errors=True)
    if timeout <= 0:
        collect_run(target)
        return Run(returncode=1, cases={}, timed_out=True, stuck=None)
    done = runner([*GRADLE, *extra], cwd=ANDROID, timeout=timeout)
    timed_out = getattr(done, "timed_out", False)
    cases = collect_run(target)
    stuck = emulator.diagnose(target / "diagnostics") if timed_out else None
    return Run(returncode=done.returncode, cases=cases, timed_out=timed_out, stuck=stuck)


def run_attempt(number, runner, deadline, emulator, clock=time.monotonic):
    return run_gradle(ATTEMPTS / f"attempt-{number}", [], runner, deadline - clock(), emulator)


def run_retry(tests, runner, deadline, emulator, clock=time.monotonic):
    """Retry each failed test in its own run: the runner's `class` argument honours one
    `Class#method` entry per run, so a combined list would leave tests unretried. A run that
    times out ends the retries; tests not retried stay failed."""
    returncode, cases, timeouts = 0, {}, []
    for index, name in enumerate(tests, start=1):
        run = run_gradle(ATTEMPTS / "attempt-2" / f"run-{index}", [CLASS_ARGUMENT + name],
                         runner, deadline - clock(), emulator)
        returncode = returncode or run.returncode
        cases.update(run.cases)
        if run.timed_out:
            timeouts.append(run.stuck or name)
            break
    return returncode, cases, timeouts


def evaluate(first_code, first, second_code=None, second=None, expected=None,
             first_timeout=None, retry_timeouts=()):
    """Return (passed, flaky, failed, reason) for one or two attempts.

    first_timeout is None when the first attempt finished, otherwise the test it was
    running when the time budget ran out ("" when unknown); retry_timeouts names tests
    whose retry ran out of time.
    """
    if first_timeout is not None:
        stuck = first_timeout or "unknown test (see diagnostics/logcat.txt)"
        return False, [], [f"(timed out) {stuck}"], (
            f"The first attempt exceeded the {TEST_BUDGET_S // 60}-minute budget and was "
            f"killed while running {stuck}; logcat and process state are in "
            "attempt-1/diagnostics. Not retried."
        )
    problems = inventory_problems(expected, first) if expected is not None else []
    if problems:
        return False, [], problems, (
            f"The first attempt did not report every declared test ({len(problems)} "
            "inventory problem(s)); a crash or abort may have dropped tests. Not retried."
        )
    retry, reason = plan_retry(first_code, first)
    if first_code == 0:
        if not first:
            return False, [], ["(no instrumented test results)"], "No test results were found."
        return True, [], [], reason
    if not retry or second is None:
        return False, [], retry or ["(Gradle failure without a failing test)"], reason
    flaky, failed = classify(first, second)
    if retry_timeouts:
        failed = sorted(set(failed) | {f"(timed out) {name}" for name in retry_timeouts})
        reason += " A retry ran out of time; see attempt-2/run-N/diagnostics."
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


class Emulator:
    """The CI emulator, through android_emulator; tests substitute a fake."""

    def wait(self):
        android_emulator.wait_until_ready()

    def diagnose(self, target):
        return android_emulator.diagnose(target)

    def stop(self):
        android_emulator.stop()


def main(argv=None, runner=run_with_timeout, sources=None, emulator=None,
         clock=time.monotonic):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="Device label, e.g. 'API 34'")
    args = parser.parse_args(argv)
    emulator = emulator or Emulator()
    shutil.rmtree(ATTEMPTS, ignore_errors=True)
    ATTEMPTS.mkdir(parents=True)
    try:
        return run_suite(args.label, runner, sources, emulator, clock)
    finally:
        try:
            emulator.stop()
        except android_emulator.EmulatorError as error:
            print(f"::error::{error}")


def run_suite(label, runner, sources, emulator, clock):
    expected = expected_tests(sources)
    try:
        emulator.wait()
    except android_emulator.EmulatorError as error:
        emulator.diagnose(ATTEMPTS / "boot-diagnostics")
        print(f"::error::{error}; no test was run (see boot-diagnostics).")
        return 1
    deadline = clock() + TEST_BUDGET_S
    first_run = run_attempt(1, runner, deadline, emulator, clock)
    first_code, first = first_run.returncode, first_run.cases
    first_timeout = (first_run.stuck or "") if first_run.timed_out else None
    retry, _ = plan_retry(first_code, first)
    second_code = second = None
    retry_timeouts = []
    if retry and first_timeout is None and not inventory_problems(expected, first):
        second_code, second, retry_timeouts = run_retry(retry, runner, deadline, emulator,
                                                        clock)
    passed, flaky, failed, reason = evaluate(first_code, first, second_code, second, expected,
                                             first_timeout, retry_timeouts)
    text = summary(label, first, second, flaky, failed, reason)
    (ATTEMPTS / "result.json").write_text(json.dumps({
        "label": label,
        "passed": passed,
        "flaky": flaky,
        "failed": failed,
        "expected": sorted(expected),
        "first_attempt": first,
        "retry": second,
        "timed_out": [name for name in failed if name.startswith("(timed out)")],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(text)
    for name in flaky:
        print(f"::warning::Flaky instrumented test on {label}: {name}")
    for name in failed:
        print(f"::error::Instrumented test failed on {label}: {name}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
