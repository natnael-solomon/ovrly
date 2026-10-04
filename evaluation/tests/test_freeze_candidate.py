import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate import Invalid, ROOT, digest, read_json, validate_dataset


def load(directory, table):
    return [json.loads(line) for line in
            (directory / f"{table}.jsonl").read_text(encoding="utf-8").splitlines()]


class FreezeCandidateTest(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "freeze-candidate"
        self.reviews = {r["clip_id"]: r for r in load(self.directory, "adjudications")}
        self.previous = {r["clip_id"]: r for r in load(ROOT / "reviewed-draft", "adjudications")}

    def test_valid_candidate_preserves_history_and_split(self):
        self.assertEqual(11, validate_dataset(self.directory, draft=True))
        for table in ("clips", "annotations", "main-arguments"):
            self.assertEqual((ROOT / "reviewed-draft" / f"{table}.jsonl").read_bytes(),
                             (self.directory / f"{table}.jsonl").read_bytes())
        self.assertEqual(259, sum(len(r["occurrences"])
                                 for r in load(self.directory, "annotations")))
        self.assertEqual(264, sum(len(r["decisions"]) for r in self.reviews.values()))
        self.assertEqual(7, sum(r["split"] == "dev" for r in load(self.directory, "clips")))

    def test_all_106_speech_mappings_preserve_claim_identity(self):
        count = 0
        mutable = {"start_ms", "end_ms", "timing_basis", "resolution"}
        for clip, review in self.reviews.items():
            if not clip.startswith("social-"):
                continue
            previous = {d["gold_id"]: d for d in self.previous[clip]["decisions"]}
            for decision in review["decisions"]:
                if decision["modality"] != "speech":
                    continue
                count += 1
                old = previous[decision["gold_id"]]
                self.assertEqual({k: v for k, v in old.items() if k not in mutable},
                                 {k: v for k, v in decision.items() if k not in mutable})
                self.assertEqual("subtitle-cue", decision["timing_basis"])
                self.assertTrue(decision["resolution"].startswith(old["resolution"]))
                self.assertRegex(
                    decision["resolution"],
                    r"Speech reconciliation 2026-10-04: user-reviewed SRT SHA-256 [a-f0-9]{64}, cues \d+-\d+",
                )
        self.assertEqual(106, count)

    def test_representative_corrected_intervals(self):
        expected = {
            "social-01-ai-013-reviewed": (55781, 58140),
            "social-02-ai-012-reviewed": (54328, 58729),
            "social-03-ai-011-reviewed": (58429, 65133),
            "social-04-ai-006-reviewed": (16481, 29629),
            "social-05-ai-028-reviewed": (162888, 165937),
            "social-07-ai-011-reviewed": (67721, 74960),
        }
        decisions = {d["gold_id"]: d for r in self.reviews.values() for d in r["decisions"]}
        for gold_id, interval in expected.items():
            with self.subTest(gold_id=gold_id):
                self.assertEqual(interval, (decisions[gold_id]["start_ms"], decisions[gold_id]["end_ms"]))

    def test_five_discoveries_do_not_invent_original_references(self):
        discoveries = [d for r in self.reviews.values() for d in r["decisions"] if not d["references"]]
        self.assertEqual(5, len(discoveries))
        self.assertTrue(all(d["modality"] == "on-screen-text" and d["eligible"] for d in discoveries))
        for decision in discoveries:
            self.assertIn("Adjudicator-discovered", decision["resolution"])
            self.assertRegex(decision["resolution"], r"[a-f0-9]{64}")
        annual = [d for d in discoveries if "epi-annual" in d["gold_id"]]
        self.assertEqual(4, len(annual))
        values = {
            ("1948-1979", "productivity", "2.5"),
            ("1948-1979", "compensation", "2.1"),
            ("1979-2025", "productivity", "1.4"),
            ("1979-2025", "compensation", "0.6"),
        }
        for period, measure, rate in values:
            match = [d for d in annual if f"{measure} growth of {rate}% during {period}" in d["proposition"]]
            self.assertEqual(1, len(match))
            self.assertEqual((73000, 80000), (match[0]["start_ms"], match[0]["end_ms"]))
            self.assertIn("average annual", match[0]["proposition"])
        meme = next(d for d in discoveries if "meme-" in d["gold_id"])
        self.assertEqual((0, 12933), (meme["start_ms"], meme["end_ms"]))
        self.assertEqual("media-reviewed", meme["timing_basis"])
        self.assertIn("without numerical amounts", meme["proposition"])
        self.assertFalse(re.search(r"\d", meme["proposition"]))

    def test_existing_twenty_cards_and_non_social_decisions_unchanged(self):
        cards = 0
        for clip, review in self.reviews.items():
            current = {d["gold_id"]: d for d in review["decisions"]}
            for decision in self.previous[clip]["decisions"]:
                if decision["timing_basis"] == "user-card-interval":
                    cards += 1
                    self.assertEqual(decision, current[decision["gold_id"]])
            if not clip.startswith("social-"):
                self.assertEqual(self.previous[clip]["decisions"], review["decisions"])
        self.assertEqual(20, cards)
        self.assertIn("no on-screen text card", self.reviews["clip-f"]["review_note"])
        self.assertIn("author/publication-time metadata", self.reviews["social-05"]["review_note"])

    def test_candidate_does_not_waive_frozen_rights_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            manifest = read_json(self.directory / "dataset.json")
            manifest["kind"] = "frozen"
            for name in manifest["files"]:
                (directory / name).write_bytes((self.directory / name).read_bytes())
                self.assertEqual(manifest["files"][name], digest(directory / name))
            (directory / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(Invalid, "pending rights or credits"):
                validate_dataset(directory, frozen=True)
        negative = next(r for r in load(self.directory, "main-arguments") if r["clip_id"] == "clip-m")
        self.assertEqual("user-confirmed-negative", negative["basis"])


if __name__ == "__main__":
    unittest.main()
