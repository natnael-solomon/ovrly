import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate import Invalid, ROOT, digest, read_json, validate_dataset


class ReviewedDraftTest(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "reviewed-draft"
        self.manifest = read_json(self.directory / "dataset.json")
        self.rows = {
            name: [json.loads(line) for line in
                   (self.directory / name).read_text(encoding="utf-8").splitlines()]
            for name in self.manifest["files"]
        }

    def write(self, directory):
        manifest = copy.deepcopy(self.manifest)
        for name, rows in self.rows.items():
            (directory / name).write_bytes(
                "".join(json.dumps(row) + "\n" for row in rows).encode()
            )
            manifest["files"][name] = digest(directory / name)
        (directory / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")

    def invalid(self, message):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write(directory)
            with self.assertRaisesRegex(Invalid, message):
                validate_dataset(directory, draft=True)

    def test_revision_valid_and_original_inputs_preserved(self):
        self.assertEqual(11, validate_dataset(self.directory, draft=True))
        self.assertEqual("res01-review-2026-10-04", self.manifest["dataset_version"])
        for name in ("clips.jsonl", "annotations.jsonl"):
            self.assertEqual((ROOT / "draft" / name).read_bytes(),
                             (self.directory / name).read_bytes())
        self.assertEqual(259, sum(len(r["decisions"])
                                 for r in self.rows["adjudications.jsonl"]))
        clips = self.rows["clips.jsonl"]
        self.assertEqual(7, sum(c["split"] == "dev" for c in clips))
        self.assertEqual(4, sum(c["split"] == "test" for c in clips))

    def test_all_card_decisions_preserve_content_intervals_and_references(self):
        count = 0
        historical = {
            r["clip_id"]: r for r in map(
                json.loads, (ROOT / "draft" / "adjudications.jsonl")
                .read_text(encoding="utf-8").splitlines()
            )
        }
        for review in self.rows["adjudications.jsonl"]:
            old = {d["gold_id"]: d for d in historical[review["clip_id"]]["decisions"]}
            for decision in review["decisions"]:
                if decision["timing_basis"] != "user-card-interval":
                    continue
                count += 1
                previous = old[decision["gold_id"]]
                self.assertEqual(
                    {k: v for k, v in previous.items() if k != "resolution"},
                    {k: v for k, v in decision.items() if k != "resolution"},
                )
                self.assertTrue(decision["resolution"].startswith(previous["resolution"]))
                self.assertIn("screenshot SHA-256", decision["resolution"])
                self.assertIn("not newly frame-measured", decision["resolution"])
        self.assertEqual(20, count)

    def test_non_social_user_confirmations(self):
        reviews = {r["clip_id"]: r for r in self.rows["adjudications.jsonl"]}
        for clip in ("clip-d", "clip-k"):
            self.assertIn("user confirmed this clip has no on-screen claims",
                          reviews[clip]["review_note"])
            self.assertEqual({"speech"}, {d["modality"] for d in reviews[clip]["decisions"]})
        self.assertEqual({"both"}, {d["modality"] for d in reviews["clip-i"]["decisions"]})
        originals = next(r for r in self.rows["annotations.jsonl"] if r["clip_id"] == "clip-i")
        self.assertEqual({"unverified"}, {o["modality"] for o in originals["occurrences"]})
        self.assertEqual(70021, reviews["clip-i"]["decisions"][-1]["end_ms"])
        self.assertEqual("provisional", reviews["clip-i"]["review_status"])

    def test_negative_is_recorded_without_inventing_other_approvals(self):
        negative = next(r for r in self.rows["main-arguments.jsonl"] if r["clip_id"] == "clip-m")
        self.assertEqual("user-confirmed-negative", negative["basis"])
        self.assertIn("User confirmed", negative["provenance"])
        review = next(r for r in self.rows["adjudications.jsonl"] if r["clip_id"] == "clip-m")
        self.assertEqual([], review["decisions"])
        self.assertIn("user-confirmed negative", review["review_note"])
        clip = next(r for r in self.rows["clips.jsonl"] if r["clip_id"] == "clip-m")
        self.assertEqual("unverified", clip["language"])
        self.assertEqual("pending", clip["rights"]["clearance"])
        self.assertNotIn("no-assessable-claims", clip["coverage"])

    def test_confirmed_negative_rejects_eligible_final_claim(self):
        argument = next(r for r in self.rows["main-arguments.jsonl"] if r["clip_id"] == "clip-d")
        argument["basis"] = "user-confirmed-negative"
        self.invalid("confirmed negative cannot have eligible final decisions")

    def test_confirmed_negative_does_not_waive_review_or_language(self):
        for table in ("annotations.jsonl", "adjudications.jsonl"):
            row = next(r for r in self.rows[table] if r["clip_id"] == "clip-m")
            row["review_status"] = "complete"
        self.invalid("unverified language")

    def test_confirmation_does_not_certify_coverage_on_a_provisional_pass(self):
        clip = next(r for r in self.rows["clips.jsonl"] if r["clip_id"] == "clip-m")
        clip["coverage"] = ["no-assessable-claims"]
        self.invalid("provisional review cannot certify")

    def test_revision_cannot_be_relabelled_frozen(self):
        self.manifest["kind"] = "frozen"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write(directory)
            with self.assertRaisesRegex(Invalid, "pending rights or credits"):
                validate_dataset(directory, frozen=True)

    def test_existing_human_notes_and_evidence_are_preserved(self):
        historical = {
            r["clip_id"]: r for r in map(
                json.loads, (ROOT / "draft" / "main-arguments.jsonl")
                .read_text(encoding="utf-8").splitlines()
            )
        }
        for argument in self.rows["main-arguments.jsonl"]:
            if argument["clip_id"] != "clip-m":
                self.assertEqual(historical[argument["clip_id"]], argument)


if __name__ == "__main__":
    unittest.main()
