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


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manifest = read_json(ROOT / "draft" / "dataset.json")
        self.rows = {
            name: [json.loads(line) for line in
                   (ROOT / "draft" / name).read_text(encoding="utf-8").splitlines()]
            for name in self.manifest["files"]
        }

    def write(self):
        for name, rows in self.rows.items():
            path = self.directory / name
            path.write_bytes("".join(json.dumps(row) + "\n" for row in rows).encode())
        self.manifest["files"] = {name: digest(self.directory / name) for name in self.rows}
        (self.directory / "dataset.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def invalid(self, message, **options):
        self.write()
        with self.assertRaisesRegex(Invalid, message):
            validate_dataset(self.directory, **({"draft": True} | options))

    def test_committed_draft_shape_and_split(self):
        self.assertEqual(11, validate_dataset(ROOT / "draft", draft=True))
        clips = self.rows["clips.jsonl"]
        self.assertEqual(
            {"social-01", "social-02", "social-03", "social-04", "social-05", "social-07", "clip-d"},
            {c["clip_id"] for c in clips if c["split"] == "dev"},
        )
        self.assertEqual({"clip-k", "clip-f", "clip-i", "clip-m"},
                         {c["clip_id"] for c in clips if c["split"] == "test"})
        self.assertEqual(1480665, sum(c["duration_ms"] for c in clips))
        annotations = self.rows["annotations.jsonl"]
        adjudications = self.rows["adjudications.jsonl"]
        self.assertEqual(11, len(annotations))
        self.assertEqual(11, len(adjudications))
        originals = {o["occurrence_id"]: o for a in annotations for o in a["occurrences"]}
        decisions = {d["references"][0]["occurrence_id"]: d
                     for a in adjudications for d in a["decisions"]}
        self.assertEqual(259, len(originals))
        self.assertEqual(originals.keys(), decisions.keys())
        self.assertEqual(20, sum(o["timing_basis"] == "user-card-interval" for o in originals.values()))
        for a in annotations:
            self.assertEqual("ai-assisted", a["annotator_kind"])
            self.assertFalse(a["independent"])
            self.assertFalse(a["blind_to_model_output"])

    def test_corrected_windows_and_eligibility_preserve_source(self):
        originals = {o["occurrence_id"]: o for a in self.rows["annotations.jsonl"]
                     for o in a["occurrences"]}
        decisions = {d["references"][0]["occurrence_id"]: d
                     for a in self.rows["adjudications.jsonl"] for d in a["decisions"]}
        for oid, old, new in (
            ("social-01-ai-013", (55000, 57000), (57000, 59000)),
            ("social-01-ai-014", (57000, 70000), (59000, 70000)),
            ("social-01-ai-015", (57000, 70000), (59000, 70000)),
            ("social-01-ai-016", (57000, 70000), (59000, 70000)),
        ):
            self.assertEqual(old, (originals[oid]["start_ms"], originals[oid]["end_ms"]))
            self.assertEqual(new, (decisions[oid]["start_ms"], decisions[oid]["end_ms"]))
        for oid in ("clip-i-ai-002", "social-04-ai-013", "social-01-ai-010"):
            self.assertFalse(originals[oid]["eligible"])
            self.assertTrue(decisions[oid]["eligible"])
        self.assertEqual(decisions["social-05-ai-002"]["proposition_id"],
                         decisions["social-05-ai-011"]["proposition_id"])
        self.assertEqual(70021, decisions["clip-i-ai-018"]["end_ms"])
        self.assertIn("70144 ms", decisions["clip-i-ai-018"]["resolution"])
        self.assertNotIn("final interval blank", decisions["clip-i-ai-018"]["resolution"])

    def test_main_arguments_keep_four_human_notes_and_sources(self):
        arguments = self.rows["main-arguments.jsonl"]
        self.assertEqual(11, len(arguments))
        self.assertEqual({"social-01", "social-03", "social-04", "social-07"},
                         {a["clip_id"] for a in arguments if a["human_note"] is not None})
        self.assertEqual(22, len({s["url"] for a in arguments for s in a["sources"]}))

    def test_provisional_negative_is_not_certified(self):
        clip = next(c for c in self.rows["clips.jsonl"] if c["clip_id"] == "clip-m")
        self.assertEqual("unverified", clip["language"])
        self.assertNotIn("no-assessable-claims", clip["coverage"])
        for name in ("annotations.jsonl", "adjudications.jsonl"):
            row = next(a for a in self.rows[name] if a["clip_id"] == "clip-m")
            self.assertEqual("provisional", row["review_status"])
            self.assertEqual([], row.get("occurrences", row.get("decisions")))
        clip["coverage"] = ["no-assessable-claims"]
        self.invalid("provisional review cannot certify")

    def test_draft_requires_explicit_mode(self):
        self.write()
        for options in ({}, {"frozen": True}, {"draft": True, "frozen": True}):
            with self.subTest(options=options), self.assertRaises(Invalid):
                validate_dataset(self.directory, **options)

    def test_manifest_requires_limitations_and_companion(self):
        for field in ("limitations", "main-arguments.jsonl"):
            with self.subTest(field=field):
                self.setUp()
                if field == "limitations":
                    del self.manifest[field]
                else:
                    del self.rows[field]
                self.invalid("draft requires")
        self.setUp()
        self.manifest["limitations"] = [" "]
        self.invalid("whitespace")

    def test_pending_rights_cannot_claim_permission(self):
        for update in ({"clearance": "cleared"}, {"allows_redistribution": True}):
            with self.subTest(update=update):
                self.setUp()
                self.rows["clips.jsonl"][0]["rights"].update(update)
                self.invalid("pending rights cannot claim")
        self.setUp()
        del self.rows["clips.jsonl"][0]["rights"]["clearance"]
        self.invalid("explicit clearance")

    def test_draft_reviews_and_source_fields_required(self):
        for table in ("annotations.jsonl", "adjudications.jsonl"):
            with self.subTest(table=table):
                self.setUp()
                del self.rows[table][0]["review_status"]
                self.invalid("explicit review_status")
        for field in ("timing_basis", "source"):
            with self.subTest(field=field):
                self.setUp()
                del self.rows["annotations.jsonl"][0]["occurrences"][0][field]
                self.invalid("draft occurrences require")
        self.setUp()
        self.rows["annotations.jsonl"][0]["occurrences"][0]["source"]["units"] = " "
        self.invalid("source provenance must not be whitespace")

    def test_complete_review_cannot_claim_unknown_modality_or_language(self):
        for table in ("annotations.jsonl", "adjudications.jsonl"):
            with self.subTest(table=table):
                self.setUp()
                row = next(a for a in self.rows[table] if a["clip_id"] == "clip-i")
                row["review_status"] = "complete"
                self.invalid("unverified modality")
        self.setUp()
        for name in ("annotations.jsonl", "adjudications.jsonl"):
            row = next(a for a in self.rows[name] if a["clip_id"] == "clip-m")
            row["review_status"] = "complete"
        self.invalid("unverified language")

    def test_companion_hash_and_links_checked(self):
        self.write()
        with (self.directory / "main-arguments.jsonl").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(Invalid, "SHA-256"):
            validate_dataset(self.directory, draft=True)
        for change, message in (
            (lambda rows: rows.pop(), "exactly all clips"),
            (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate record"),
            (lambda rows: rows[0].update(human_note=None), "human note must match"),
            (lambda rows: rows[0].update(human_note=" "), "whitespace"),
            (lambda rows: rows[0].update(provenance=" "), "whitespace"),
            (lambda rows: rows[0]["sources"][0].update(url="local-file"), "pattern mismatch"),
        ):
            with self.subTest(message=message):
                self.setUp()
                change(self.rows["main-arguments.jsonl"])
                self.invalid(message)

    def test_draft_still_checks_intervals_references_hashes_and_split_leakage(self):
        for change, message in (
            (lambda: self.rows["annotations.jsonl"][0]["occurrences"][0].update(end_ms=999999), "interval"),
            (lambda: self.rows["adjudications.jsonl"][0]["decisions"].pop(), "every original"),
            (lambda: self.rows["clips.jsonl"][-1].update(
                topic_ids=self.rows["clips.jsonl"][0]["topic_ids"]), "split leakage"),
            (lambda: self.rows["clips.jsonl"][-1]["media"].update(
                sha256=self.rows["clips.jsonl"][0]["media"]["sha256"]), "identical media"),
        ):
            with self.subTest(message=message):
                self.setUp()
                change()
                self.invalid(message)
        self.setUp()
        self.write()
        with self.assertRaisesRegex(Invalid, "media path not found"):
            validate_dataset(self.directory, draft=True, media_root=self.directory)
        with (self.directory / "annotations.jsonl").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(Invalid, "SHA-256"):
            validate_dataset(self.directory, draft=True)

    def test_cli_labels_success_as_draft_not_freeze(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main([str(ROOT / "draft"), "--draft"]))
        self.assertIn("NOT frozen or benchmark-ready", output.getvalue())
        self.assertIn("Media bytes NOT verified", output.getvalue())


if __name__ == "__main__":
    unittest.main()
