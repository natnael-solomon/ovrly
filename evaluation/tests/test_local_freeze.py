import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate import Invalid, ROOT, digest, main, read_json, validate_dataset


class LocalFreezeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manifest = read_json(ROOT / "corpus-local" / "dataset.json")
        self.rows = {
            name: [json.loads(line) for line in
                   (ROOT / "corpus-local" / name).read_text(encoding="utf-8").splitlines()]
            for name in self.manifest["files"]
        }

    def write(self):
        for name, rows in self.rows.items():
            path = self.directory / name
            path.write_bytes("".join(json.dumps(row) + "\n" for row in rows).encode())
            self.manifest["files"][name] = digest(path)
        (self.directory / "dataset.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def invalid(self, message):
        self.write()
        with self.assertRaisesRegex(Invalid, message):
            validate_dataset(self.directory, local_frozen=True)

    def test_snapshot_is_pinned_and_history_preserved(self):
        source = ROOT / "corpus-local"
        self.assertEqual("ac11b53dd4063ad79b02f94f4af3b509fd782527731c1b619d0ceadfe596bc41",
                         digest(source / "dataset.json"))
        self.assertEqual(11, validate_dataset(source, local_frozen=True))
        for name in self.manifest["files"]:
            self.assertEqual((ROOT / "freeze-candidate" / name).read_bytes(),
                             (source / name).read_bytes())
        self.assertEqual(259, sum(len(r["occurrences"]) for r in self.rows["annotations.jsonl"]))
        self.assertEqual(264, sum(len(r["decisions"]) for r in self.rows["adjudications.jsonl"]))
        self.assertEqual(7, sum(c["split"] == "dev" for c in self.rows["clips.jsonl"]))
        self.assertEqual("dd46f6016858a5df85243e2233ae8a913cefda5a64f9f0db175239468b594981",
                         self.manifest["freeze_approval"]["rights_review_sha256"])
        self.assertEqual(digest(ROOT / "freeze-candidate" / "dataset.json"),
                         self.manifest["freeze_approval"]["source_manifest_sha256"])

    def test_modes_cannot_be_confused_or_combined(self):
        for options in ({}, {"draft": True}, {"frozen": True},
                        {"local_frozen": True, "frozen": True},
                        {"local_frozen": True, "draft": True}):
            with self.subTest(options=options), self.assertRaises(Invalid):
                validate_dataset(ROOT / "corpus-local", **options)
        with self.assertRaisesRegex(Invalid, "kind does not match"):
            validate_dataset(ROOT / "freeze-candidate", local_frozen=True)

    def test_approval_cannot_relax_full_frozen_mode(self):
        self.manifest["kind"] = "frozen"
        self.write()
        with self.assertRaisesRegex(Invalid, "exclusively"):
            validate_dataset(self.directory, frozen=True)
        del self.manifest["freeze_approval"]
        self.write()
        with self.assertRaisesRegex(Invalid, "pending rights or credits"):
            validate_dataset(self.directory, frozen=True)

    def test_approval_and_limitations_required(self):
        baseline = copy.deepcopy(self.manifest)
        for field in ("freeze_approval", "limitations"):
            self.manifest = copy.deepcopy(baseline)
            del self.manifest[field]
            with self.subTest(field=field):
                self.invalid("required|requires")
        self.manifest = baseline
        self.manifest["limitations"] = [" "]
        self.invalid("whitespace")

    def test_approval_exact_clip_set(self):
        self.manifest["freeze_approval"]["approved_clip_ids"][-1] = "unapproved-clip"
        self.invalid("exactly the frozen clips")

    def test_approval_cannot_expand_scope(self):
        baseline = copy.deepcopy(self.manifest)
        for field in ("allows_hosted_processing", "allows_redistribution", "allows_training"):
            self.manifest = copy.deepcopy(baseline)
            self.manifest["freeze_approval"][field] = True
            with self.subTest(field=field):
                self.invalid("expected False")
        self.manifest = baseline
        self.manifest["freeze_approval"]["credits_signoff"] = "pending"
        self.invalid("expected 'approved'")

    def test_approval_timestamp_validation(self):
        for value in ("not-a-date-at-all-but-long", "2026-10-04T19:26:06.979"):
            self.manifest["freeze_approval"]["approved_at"] = value
            with self.subTest(value=value):
                self.invalid("timestamp")

    def test_negative_must_be_confirmed(self):
        negative = next(r for r in self.rows["main-arguments.jsonl"] if r["clip_id"] == "clip-m")
        negative["basis"] = "assessment"
        self.invalid("explicit confirmation for negative clips")
        negative["basis"] = "user-reported-negative"
        self.invalid("explicit confirmation for negative clips")

    def test_final_modality_must_be_known(self):
        self.rows["adjudications.jsonl"][0]["decisions"][0]["modality"] = "unverified"
        self.invalid("known final modality")

    def test_intervals_and_traceability_still_enforced(self):
        decision = self.rows["adjudications.jsonl"][0]["decisions"][0]
        end = decision["end_ms"]
        decision["end_ms"] = 999999
        self.invalid("interval must satisfy")
        decision["end_ms"] = end
        decision["references"][0]["occurrence_id"] = "unknown-occurrence"
        self.invalid("unknown occurrence reference")

    def test_split_isolation_still_enforced(self):
        clips = self.rows["clips.jsonl"]
        test_clip = next(c for c in clips if c["split"] == "test")
        test_clip["topic_ids"] = clips[0]["topic_ids"]
        self.invalid("split leakage")

    def test_media_hashes_still_enforced(self):
        root = self.directory / "media"
        for i, clip in enumerate(self.rows["clips.jsonl"]):
            path = root / f"clip-{i}.bin"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"synthetic test bytes {i}".encode())
            clip["media"] = {"path": path.name, "sha256": digest(path)}
        self.write()
        self.assertEqual(11, validate_dataset(self.directory, local_frozen=True, media_root=root))
        (root / "clip-0.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(Invalid, "media SHA-256 mismatch"):
            validate_dataset(self.directory, local_frozen=True, media_root=root)

    def test_snapshot_hashes_cannot_be_ignored(self):
        self.write()
        with (self.directory / "adjudications.jsonl").open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(Invalid, "snapshot SHA-256 mismatch"):
            validate_dataset(self.directory, local_frozen=True)

    def test_cli_reports_scope_and_unverified_media(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([str(ROOT / "corpus-local"), "--frozen-local"])
        self.assertEqual(0, code)
        self.assertIn("owner-approved local frozen", output.getvalue())
        self.assertIn("Media bytes NOT verified", output.getvalue())
        self.assertIn("NOT full-coverage certification", output.getvalue())


if __name__ == "__main__":
    unittest.main()
