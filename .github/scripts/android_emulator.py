"""Boot readiness, diagnostics and a bounded stop for the CI Android emulator.

reactivecircus/android-emulator-runner declares the emulator booted as soon as
`sys.boot_completed` is 1, and its step only ends when the emulator process exits. On the
API 29 image a resumed snapshot reported boot before the input service existed, and the
emulator could hang for good in its own shutdown after `emu kill`. So tests wait here for the
system services they use, and the emulator is always stopped here: gracefully, then killed
after a grace period, so a hung shutdown can never hold the job.
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

SERVICES = ("input", "package", "activity", "window", "settings")
EMULATOR_PROCESSES = ("qemu-system", "/emulator/emulator")
ADB_TIMEOUT_S = 30
READY_TIMEOUT_S = 180
STOP_GRACE_S = 60
POLL_S = 2
ANIMATION_SETTINGS = (
    "window_animation_scale", "transition_animation_scale", "animator_duration_scale",
)


class EmulatorError(RuntimeError):
    pass


def adb(*args, timeout=ADB_TIMEOUT_S):
    """Run one adb command; return (exit code, stdout). A hung adb counts as a failure."""
    try:
        done = subprocess.run(["adb", *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, str(error)
    return done.returncode, done.stdout


def readiness(run=adb):
    """Return the first unmet readiness condition, or None when the device is usable."""
    code, out = run("shell", "getprop", "sys.boot_completed")
    if code or out.strip() != "1":
        return "sys.boot_completed is not 1"
    for service in SERVICES:
        code, out = run("shell", "service", "check", service)
        if code or ": found" not in out:
            return f"service {service} is not published"
    code, out = run("shell", "pm", "path", "android")
    if code or not out.strip().startswith("package:"):
        return "the package manager cannot resolve android"
    return None


def wait_until_ready(timeout_s=READY_TIMEOUT_S, run=adb, clock=time.monotonic,
                     sleep=time.sleep):
    """Poll until the device is usable; raise EmulatorError naming the unmet condition."""
    deadline = clock() + timeout_s
    while True:
        problem = readiness(run)
        if problem is None:
            break
        if clock() >= deadline:
            raise EmulatorError(f"Emulator not ready after {timeout_s} s: {problem}")
        sleep(POLL_S)
    run("shell", "input", "keyevent", "82")
    run("shell", "wm", "dismiss-keyguard")
    for setting in ANIMATION_SETTINGS:
        run("shell", "settings", "put", "global", setting, "0")


def running_processes(check=None):
    """PIDs of emulator processes still alive."""
    check = check or (lambda pattern: subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, text=True, check=False).stdout)
    pids = set()
    for pattern in EMULATOR_PROCESSES:
        pids.update(line.strip() for line in check(pattern).splitlines() if line.strip())
    return pids


def stop(grace_s=STOP_GRACE_S, run=adb, alive=running_processes, kill=None,
         clock=time.monotonic, sleep=time.sleep):
    """Ask the emulator to exit, then kill it after grace_s. Return True if it exited itself."""
    kill = kill or (lambda: [subprocess.run(["pkill", "-KILL", "-f", pattern], check=False)
                             for pattern in EMULATOR_PROCESSES])
    run("emu", "kill", timeout=20)
    deadline = clock() + grace_s
    while alive():
        if clock() >= deadline:
            print(f"::warning::The emulator did not exit within {grace_s} s of emu kill; "
                  "killing it.")
            kill()
            sleep(POLL_S)
            if alive():
                raise EmulatorError("Emulator processes survived SIGKILL")
            return False
        sleep(POLL_S)
    return True


STARTED = re.compile(r"TestRunner: started: (\w+)\(([\w.$]+)\)")
FINISHED = re.compile(r"TestRunner: finished: (\w+)\(([\w.$]+)\)")


def running_test(logcat):
    """The `Class#method` the runner started last and never finished, or None."""
    open_tests = []
    for line in logcat.splitlines():
        started = STARTED.search(line)
        if started:
            open_tests.append(f"{started.group(2)}#{started.group(1)}")
            continue
        finished = FINISHED.search(line)
        if finished:
            name = f"{finished.group(2)}#{finished.group(1)}"
            if name in open_tests:
                open_tests.remove(name)
    return open_tests[-1] if open_tests else None


def diagnose(target, run=adb):
    """Save logcat and process state under target; return the test that was running."""
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    _, logcat = run("logcat", "-d", "-v", "threadtime", timeout=60)
    (target / "logcat.txt").write_text(logcat, encoding="utf-8")
    _, processes = run("shell", "ps", "-A")
    (target / "processes.txt").write_text(processes, encoding="utf-8")
    _, activities = run("shell", "dumpsys", "activity", "activities")
    (target / "activities.txt").write_text(activities, encoding="utf-8")
    return running_test(logcat)


def save_snapshot(run=adb):
    """Save the quickboot snapshot explicitly, so stopping never has to write it."""
    code, out = run("emu", "avd", "snapshot", "save", "default_boot", timeout=180)
    if code or "OK" not in out:
        raise EmulatorError(f"Snapshot save failed: {out.strip()}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wait", "stop", "snapshot"))
    args = parser.parse_args(argv)
    try:
        if args.command == "wait":
            wait_until_ready()
        elif args.command == "stop":
            stop()
        else:
            wait_until_ready()
            save_snapshot()
            stop()
    except EmulatorError as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
