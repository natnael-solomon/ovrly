import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from android_emulator import (
    EmulatorError, SERVICES, diagnose, readiness, running_test, save_snapshot, stop,
    wait_until_ready,
)


class FakeAdb:
    """Answers adb commands from a table; records every call."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, *args, timeout=None):
        self.calls.append(args)
        key = " ".join(args)
        for prefix, answer in self.answers.items():
            if key.startswith(prefix):
                return answer(self) if callable(answer) else answer
        return 0, ""


def ready_device(overrides=None):
    answers = {
        "shell getprop sys.boot_completed": (0, "1\n"),
        "shell pm path android": (0, "package:/system/framework/framework-res.apk\n"),
    }
    for service in SERVICES:
        answers[f"shell service check {service}"] = (0, f"Service {service}: found\n")
    answers.update(overrides or {})
    return FakeAdb(answers)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class ReadinessTest(unittest.TestCase):
    def test_ready_device(self):
        self.assertIsNone(readiness(ready_device()))

    def test_boot_completed_alone_is_not_enough(self):
        adb = ready_device({"shell service check input": (0, "Service input: not found\n")})
        self.assertEqual("service input is not published", readiness(adb))
        adb = ready_device({"shell service check input": (1, "error: closed")})
        self.assertEqual("service input is not published", readiness(adb))

    def test_boot_and_package_manager_are_checked(self):
        self.assertEqual("sys.boot_completed is not 1",
                         readiness(ready_device({"shell getprop sys.boot_completed": (0, "")})))
        self.assertEqual("the package manager cannot resolve android",
                         readiness(ready_device({"shell pm path android": (1, "Error")})))

    def test_wait_retries_until_services_appear_then_prepares_the_device(self):
        clock = Clock()
        answers = iter([(0, "Service input: not found")] * 3)

        def input_service(_):
            return next(answers, (0, "Service input: found"))
        adb = ready_device({"shell service check input": input_service})
        wait_until_ready(timeout_s=60, run=adb, clock=clock, sleep=clock.sleep)
        self.assertEqual(6.0, clock.now)
        commands = [" ".join(call) for call in adb.calls]
        self.assertIn("shell input keyevent 82", commands)
        self.assertIn("shell settings put global animator_duration_scale 0", commands)
        first_input = commands.index("shell input keyevent 82")
        self.assertGreater(first_input, max(i for i, c in enumerate(commands)
                                            if c.startswith("shell service check")))

    def test_wait_is_bounded_and_names_the_problem(self):
        clock = Clock()
        adb = ready_device({"shell service check input": (0, "Service input: not found")})
        with self.assertRaisesRegex(EmulatorError, "after 10 s: service input"):
            wait_until_ready(timeout_s=10, run=adb, clock=clock, sleep=clock.sleep)
        self.assertNotIn(("shell", "input", "keyevent", "82"), adb.calls)


class StopTest(unittest.TestCase):
    def test_graceful_exit(self):
        clock = Clock()
        alive = iter([{"1"}, {"1"}, set()])
        killed = []
        self.assertTrue(stop(grace_s=60, run=FakeAdb(), alive=lambda: next(alive),
                             kill=lambda: killed.append(True), clock=clock,
                             sleep=clock.sleep))
        self.assertEqual([], killed)

    def test_hung_shutdown_is_killed_after_the_grace_period(self):
        clock = Clock()
        killed = []
        adb = FakeAdb()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            graceful = stop(grace_s=10, run=adb, alive=lambda: set() if killed else {"9"},
                            kill=lambda: killed.append(True), clock=clock, sleep=clock.sleep)
        self.assertFalse(graceful)
        self.assertEqual([True], killed)
        self.assertEqual([("emu", "kill")], adb.calls)
        self.assertLessEqual(clock.now, 14)
        self.assertIn("::warning::", output.getvalue())

    def test_unkillable_emulator_is_an_error(self):
        clock = Clock()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(EmulatorError):
            stop(grace_s=4, run=FakeAdb(), alive=lambda: {"9"}, kill=lambda: None,
                 clock=clock, sleep=clock.sleep)


class DiagnosticsTest(unittest.TestCase):
    LOGCAT = (
        "10-06 15:00:01.0 1 2 I TestRunner: started: a(app.ovrly.A)\n"
        "10-06 15:00:02.0 1 2 I TestRunner: finished: a(app.ovrly.A)\n"
        "10-06 15:00:03.0 1 2 I TestRunner: started: hangs(app.ovrly.capture.B)\n"
        "10-06 15:00:04.0 1 2 W Other: noise\n"
    )

    def test_running_test_is_the_started_unfinished_one(self):
        self.assertEqual("app.ovrly.capture.B#hangs", running_test(self.LOGCAT))
        self.assertIsNone(running_test(self.LOGCAT.replace("W Other: noise",
                          "I TestRunner: finished: hangs(app.ovrly.capture.B)")))
        self.assertIsNone(running_test(""))

    def test_diagnose_saves_logcat_and_process_state(self):
        adb = FakeAdb({"logcat": (0, self.LOGCAT), "shell ps": (0, "PID NAME\n"),
                       "shell dumpsys": (0, "ACTIVITY\n")})
        with tempfile.TemporaryDirectory() as temporary:
            stuck = diagnose(Path(temporary) / "d", run=adb)
            self.assertEqual("app.ovrly.capture.B#hangs", stuck)
            for name in ("logcat.txt", "processes.txt", "activities.txt"):
                self.assertTrue((Path(temporary) / "d" / name).is_file())

    def test_snapshot_save_must_be_confirmed(self):
        save_snapshot(FakeAdb({"emu avd snapshot save": (0, "OK\n")}))
        with self.assertRaises(EmulatorError):
            save_snapshot(FakeAdb({"emu avd snapshot save": (0, "KO: failed\n")}))


if __name__ == "__main__":
    unittest.main()
