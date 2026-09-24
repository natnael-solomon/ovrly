import unittest

from device_evidence import evaluate, evidence_rows, touches_device_paths

FILLED = """## What and why
Stop capture on projection loss.

## Device evidence

<!-- model and version only -->

| Device | Android | Route | What was verified | Result |
|---|---|---|---|---|
| Samsung SM-A217F | 12 | One UI Home · YouTube | Capture stops within 2 s of revoking projection | Pass |

## Limitations
None
"""

EMPTY = """## Device evidence

<!-- fill me -->

| Device | Android | Route | What was verified | Result |
|---|---|---|---|---|
| | | | | |

## Limitations
"""

NOT_APPLICABLE = """## Device evidence

Not applicable

## Limitations
"""


class TouchesDevicePaths(unittest.TestCase):
    def test_capture_overlay_voice_and_manifest_count(self):
        for name in (
            "android/app/src/main/java/app/ovrly/capture/CaptureService.kt",
            "android/app/src/main/java/app/ovrly/overlay/OverlayWindow.kt",
            "android/app/src/main/java/app/ovrly/voice/VoiceSession.kt",
            "android/app/src/main/AndroidManifest.xml",
        ):
            self.assertTrue(touches_device_paths([name]), name)

    def test_ui_tests_and_docs_do_not(self):
        for name in (
            "android/app/src/main/java/app/ovrly/ui/AppShell.kt",
            "android/app/src/test/java/app/ovrly/capture/CaptureModelTest.kt",
            "android/README.md",
            ".github/workflows/android.yml",
        ):
            self.assertFalse(touches_device_paths([name]), name)

    def test_lookalike_prefix_does_not_match(self):
        self.assertFalse(touches_device_paths(["android/app/src/main/java/app/ovrly/captured/X.kt"]))


class EvidenceRows(unittest.TestCase):
    def test_filled_row_is_found_and_header_and_comments_ignored(self):
        rows = evidence_rows(FILLED)
        self.assertEqual(1, len(rows))
        self.assertEqual("Samsung SM-A217F", rows[0][0])

    def test_empty_template_row_is_not_evidence(self):
        self.assertEqual([], evidence_rows(EMPTY))

    def test_missing_section_and_empty_body(self):
        self.assertEqual([], evidence_rows("## Something else\n| a | b | c |\n"))
        self.assertEqual([], evidence_rows(None))

    def test_row_with_fewer_than_three_cells_is_not_evidence(self):
        body = "## Device evidence\n| Device | Android | Route | Verified | Result |\n|---|---|---|---|---|\n| Pixel | | | | |\n"
        self.assertEqual([], evidence_rows(body))


class Evaluate(unittest.TestCase):
    device = ["android/app/src/main/java/app/ovrly/capture/CaptureService.kt"]

    def test_no_device_paths_passes_without_label(self):
        needs, passes, _ = evaluate(["android/app/src/main/java/app/ovrly/ui/AppShell.kt"], EMPTY)
        self.assertEqual((False, True), (needs, passes))

    def test_device_paths_with_evidence_passes_with_label(self):
        needs, passes, _ = evaluate(self.device, FILLED)
        self.assertEqual((True, True), (needs, passes))

    def test_device_paths_without_evidence_fails_with_label(self):
        needs, passes, reason = evaluate(self.device, EMPTY)
        self.assertEqual((True, False), (needs, passes))
        self.assertIn("empty", reason)

    def test_not_applicable_is_rejected_for_device_paths(self):
        needs, passes, reason = evaluate(self.device, NOT_APPLICABLE)
        self.assertEqual((True, False), (needs, passes))
        self.assertIn("Not applicable", reason)

    def test_missing_body_fails(self):
        needs, passes, _ = evaluate(self.device, None)
        self.assertEqual((True, False), (needs, passes))


if __name__ == "__main__":
    unittest.main()
