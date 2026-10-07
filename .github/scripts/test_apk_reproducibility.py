import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import apk_reproducibility as repro

FIXED = (2026, 1, 1, 0, 0, 0)


def write_build(directory, entries, mapping="map\n", date_time=FIXED):
    directory.mkdir(parents=True)
    with zipfile.ZipFile(directory / "app-release-unsigned.apk", "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name, date_time), data)
    (directory / "mapping.txt").write_text(mapping, encoding="utf-8")


class ReproducibilityTest(unittest.TestCase):
    ENTRIES = [("AndroidManifest.xml", b"manifest"), ("classes.dex", b"dex"), ("assets/dexopt/baseline.prof", b"p")]

    def run_compare(self, first, second, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_build(root / "a", first)
            write_build(root / "b", second, **kwargs)
            return repro.compare(root / "a", root / "b")

    def test_identical_builds_pass(self):
        ok, lines = self.run_compare(self.ENTRIES, self.ENTRIES)
        self.assertTrue(ok)
        self.assertIn("byte-for-byte identical", "\n".join(lines))

    def test_unexplained_differences_fail_and_are_named(self):
        changed = [("AndroidManifest.xml", b"manifest"), ("classes.dex", b"DEX"), ("assets/dexopt/baseline.prof", b"p")]
        ok, lines = self.run_compare(self.ENTRIES, changed)
        self.assertFalse(ok)
        self.assertIn("| `classes.dex` | content | **unexplained** |", lines)

    def test_known_nondeterminism_passes_with_its_reason(self):
        changed = [("AndroidManifest.xml", b"manifest"), ("classes.dex", b"dex"), ("assets/dexopt/baseline.prof", b"q")]
        with patch.dict(repro.KNOWN_NONDETERMINISM, {"assets/dexopt/baseline.prof*": "fixture reason"}):
            ok, lines = self.run_compare(self.ENTRIES, changed)
        self.assertTrue(ok)
        self.assertIn("| `assets/dexopt/baseline.prof` | content | fixture reason |", lines)

    def test_missing_extra_reordered_and_timestamp_differences(self):
        ok, lines = self.run_compare(self.ENTRIES, list(reversed(self.ENTRIES[:2])) + [("extra.bin", b"x")])
        self.assertFalse(ok)
        report = "\n".join(lines)
        self.assertIn("only in the first build", report)
        self.assertIn("only in the second build", report)
        self.assertIn("entry order", report)
        ok, lines = self.run_compare(self.ENTRIES, self.ENTRIES, date_time=(2026, 1, 2, 0, 0, 0))
        self.assertFalse(ok)
        self.assertIn("zip date_time", "\n".join(lines))

    def test_mapping_difference_fails_even_with_identical_apks(self):
        ok, lines = self.run_compare(self.ENTRIES, self.ENTRIES, mapping="other\n")
        self.assertFalse(ok)
        self.assertIn("R8 mappings differ", "\n".join(lines))

    def test_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_build(root / "a", self.ENTRIES)
            (root / "b").mkdir()
            ok, lines = repro.compare(root / "a", root / "b")
        self.assertFalse(ok)
        self.assertIn("Missing", lines[-1])


if __name__ == "__main__":
    unittest.main()
