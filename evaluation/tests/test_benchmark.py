import contextlib
import copy
import io
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmark import (
    VERSION,
    error_counts,
    input_schema,
    main,
    plan,
    score,
    tokens,
)
from validate import ROOT, Invalid, check_schema, digest


def common(task):
    return {
        "schema_version": VERSION,
        "kind": "synthetic",
        "task": task,
        "run_id": "invented-one",
        "clip_id": "invented-clip",
        "corpus_manifest_sha256": None,
        "duration_ms": 10000,
        "timebase": "media",
        "model": "vosk/small-en" if task == "asr" else "ml-kit/latin-v2",
        "model_revision": "invented-revision",
        "hardware": "synthetic-device",
        "provenance": "Invented contract observations; no model or device run.",
        "limitations": ["Synthetic only."],
        "resources": None,
        "hosted_processing_approval": None,
    }


def asr():
    return {
        **common("asr"),
        "reference_basis": "media-reviewed-verbatim",
        "timing_basis": "media-reviewed",
        "reference": [{"start_ms": 6000, "end_ms": 7000, "text": "It is not 10 kg."}],
        "entities": [
            {"category": "negation", "start_token": 2, "end_token": 3},
            {"category": "number", "start_token": 3, "end_token": 4},
            {"category": "unit", "start_token": 4, "end_token": 5},
        ],
        "chunks": [
            {
                "seq": 0,
                "start_ms": 0,
                "end_ms": 5000,
                "size_bytes": 160044,
                "wall_ms": 10,
                "status": "ok",
                "segments": [],
            },
            {
                "seq": 1,
                "start_ms": 5000,
                "end_ms": 10000,
                "size_bytes": 160044,
                "wall_ms": 20,
                "status": "ok",
                "segments": [{"start_ms": 1100, "end_ms": 1900, "text": "It is not 10 kg."}],
            },
        ],
        "timestamp_pairs": [{"reference_index": 0, "chunk_seq": 1, "segment_index": 0}],
        "limits": None,
    }


def ocr():
    box = [100, 100, 4000, 2000]
    return {
        **common("ocr"),
        "reference_basis": "full-screen-media-reviewed",
        "iou_threshold_permille": 500,
        "reference": [
            {
                "id": "brief-card",
                "start_ms": 1000,
                "end_ms": 2000,
                "text": "Not 10 kg",
                "box": box,
            }
        ],
        "frames": [
            {
                "id": "frame-zero",
                "presentation_ms": 0,
                "sha256": "a" * 64,
                "wall_ms": 10,
                "status": "ok",
                "detections": [],
            },
            {
                "id": "frame-change",
                "presentation_ms": 1000,
                "sha256": "b" * 64,
                "wall_ms": 20,
                "status": "ok",
                "detections": [{"text": "Not 10 kg", "box": box}],
            },
            {
                "id": "frame-persistent",
                "presentation_ms": 1500,
                "sha256": "c" * 64,
                "wall_ms": 30,
                "status": "ok",
                "detections": [{"text": "Not 10 kg", "box": box}],
            },
            {
                "id": "frame-five",
                "presentation_ms": 5000,
                "sha256": "d" * 64,
                "wall_ms": 10,
                "status": "ok",
                "detections": [],
            },
        ],
        "fixed_frame_ids": ["frame-zero", "frame-five"],
        "fixed_tolerance_ms": 100,
        "change_triggered_frame_ids": ["frame-zero", "frame-change"],
    }


class BenchmarkTest(unittest.TestCase):
    def scored(self, data):
        return score(data, ROOT / "corpus-local")

    def invalid(self, data, message):
        with self.assertRaisesRegex(Invalid, message):
            self.scored(data)

    def test_schemas_use_supported_subset(self):
        for task in ("asr", "ocr"):
            check_schema(input_schema(task))

    def test_plan_uses_new_main_snapshot_not_historical_draft(self):
        result = plan(ROOT / "corpus-local")
        self.assertEqual("res01-local-frozen-2026-10-04", result["dataset_version"])
        self.assertEqual(7, len(result["dev_clip_ids"]))
        self.assertEqual(4, len(result["excluded_holdout_ids"]))
        self.assertFalse(set(result["dev_clip_ids"]) & set(result["excluded_holdout_ids"]))
        self.assertEqual([18, 12], [r["requests_per_3min"] for r in result["proposed_pcm_trials"]])
        self.assertEqual(
            [320044, 480044],
            [r["pcm_wav_bytes_per_full_chunk"] for r in result["proposed_pcm_trials"]],
        )
        self.assertEqual({"pending"}, set(result["rights_clearance"].values()))
        self.assertFalse(result["hosted_processing_authorized_by_plan"])
        with self.assertRaises(Invalid):
            plan(ROOT / "draft")

    def test_wer_and_normalization_keep_meaningful_numbers(self):
        self.assertEqual(["it's", "not", "-1.5", "kg", "10", "%"], tokens("It's NOT -1.5 kg, 10%!"))
        counts, _ = error_counts(tokens("a b c"), tokens("a x c d"))
        self.assertEqual(1, counts["substitution"])
        self.assertEqual(1, counts["insertion"])
        self.assertEqual(0, counts["deletion"])
        self.assertAlmostEqual(2 / 3, counts["wer"])
        self.assertNotEqual(tokens("1.5"), tokens("15"))
        self.assertNotEqual(tokens("-5"), tokens("5"))
        self.assertNotEqual(tokens("favour"), tokens("favor"))

    def test_empty_reference_not_perfect_score(self):
        counts, _ = error_counts([], ["hallucination"])
        self.assertIsNone(counts["wer"])
        self.assertEqual(1, counts["insertion"])
        counts, _ = error_counts(["speech"], [])
        self.assertEqual(1, counts["deletion"])
        self.assertEqual(1, counts["wer"])
        with self.assertRaises(Invalid):
            error_counts(["a"] * 5001, [])

    def test_alignment_reconstructs_inputs_and_minimizes_edits(self):
        def distance(left, right):
            if not left or not right:
                return len(left) + len(right)
            return min(
                distance(left[1:], right[1:]) + (left[0] != right[0]),
                distance(left[1:], right) + 1,
                distance(left, right[1:]) + 1,
            )

        sequences = [
            list(p) for length in range(4) for p in itertools.product(("a", "b"), repeat=length)
        ]
        for reference, hypothesis in itertools.product(sequences, repeat=2):
            counts, edits = error_counts(reference, hypothesis)
            self.assertEqual(
                distance(reference, hypothesis),
                sum(counts[k] for k in ("substitution", "deletion", "insertion")),
            )
            self.assertEqual(reference, [reference[i] for op, i, _ in edits if op != "insertion"])
            self.assertEqual(hypothesis, [hypothesis[j] for op, _, j in edits if op != "deletion"])

    def test_chunk_offsets_and_drift(self):
        result = self.scored(asr())["metrics"]
        self.assertEqual(0, result["word_errors"]["wer"])
        self.assertEqual(6100, result["timebase_relative_segments"][0]["start_ms"])
        self.assertEqual(100, result["timestamp_drift_ms"]["start_signed"]["mean"])
        self.assertEqual(-100, result["timestamp_drift_ms"]["end_signed"]["mean"])
        self.assertEqual(100, result["timestamp_drift_ms"]["absolute_endpoints"]["p95"])
        self.assertEqual(20, result["wall_ms_per_chunk"]["p95"])
        self.assertIsNone(result["critical_entities"]["name"]["error_rate"])
        self.assertIsNone(result["quota_estimate"])

    def test_entity_errors_and_interior_insertions(self):
        data = asr()
        data["chunks"][1]["segments"][0]["text"] = "It is 100 g."
        result = self.scored(data)["metrics"]
        for category in ("negation", "number", "unit"):
            self.assertEqual(1, result["critical_entities"][category]["incorrect_spans"])
        data["reference"][0]["text"] = "It is safe"
        data["entities"] = [{"category": "negation", "start_token": 1, "end_token": 3}]
        data["chunks"][1]["segments"][0]["text"] = "It is not safe"
        self.assertEqual(
            1,
            self.scored(data)["metrics"]["critical_entities"]["negation"]["incorrect_spans"],
        )

    def test_failed_chunks_remain_errors_and_cannot_return_text(self):
        data = asr()
        data["chunks"][1]["status"] = "ASR_UNAVAILABLE"
        self.invalid(data, "cannot carry")
        data["chunks"][1]["segments"] = []
        data["timestamp_pairs"] = []
        result = self.scored(data)["metrics"]
        self.assertEqual(1, result["failed_chunks"])
        self.assertEqual(1, result["word_errors"]["wer"])
        self.assertIsNone(result["timestamp_drift_ms"]["absolute_endpoints"])

    def test_chunk_gaps_order_tail_and_segment_bounds(self):
        mutations = [
            ("start_ms", 5001),
            ("start_ms", 4999),
            ("seq", 4),
            ("end_ms", 9000),
        ]
        for field, value in mutations:
            data = asr()
            data["chunks"][1][field] = value
            with self.subTest(field=field, value=value):
                self.invalid(data, "contiguously|tail")
        data = asr()
        data["chunks"][1]["segments"][0]["end_ms"] = 5100
        self.invalid(data, "outside duration")
        data = asr()
        data["chunks"][1]["segments"] *= 2
        self.invalid(data, "overlap")

    def test_quota_and_exact_file_cap(self):
        data = asr()
        data["model"] = "groq/whisper-large-v3"
        self.invalid(data, "dated provider limits")
        data["limits"] = {
            "file_cap_bytes": 160044,
            "minimum_audio_ms": 10000,
            "audio_ms_per_hour": 7200000,
            "requests_per_day": 2000,
            "provenance": "Invented limits for a unit test; not current quota evidence.",
        }
        result = self.scored(data)["metrics"]["quota_estimate"]
        self.assertEqual(2, result["attempted_requests"])
        self.assertEqual(20000, result["estimated_audio_ms"])
        self.assertFalse(result["actual_consumption_verified"])
        self.assertFalse(result["is_three_minute_clip"])
        data["chunks"][1]["size_bytes"] += 1
        self.invalid(data, "file cap")

    def test_subtitle_envelopes_cannot_be_timestamp_evidence(self):
        data = asr()
        data["timing_basis"] = "cue-envelope"
        self.invalid(data, "cannot establish")
        data["timestamp_pairs"] = []
        data["reference_basis"] = "subtitle-reference"
        result = self.scored(data)
        self.assertEqual("subtitle-reference", result["reference_basis"])
        self.assertIsNone(result["metrics"]["timestamp_drift_ms"]["start_signed"])

    def test_pairs_entities_and_resources_fail_closed(self):
        data = asr()
        data["timestamp_pairs"] *= 2
        self.invalid(data, "one-to-one")
        data = asr()
        data["timestamp_pairs"][0]["chunk_seq"] = 9
        self.invalid(data, "unknown reference")
        data = asr()
        data["entities"][0]["end_token"] = 999
        self.invalid(data, "invalid or duplicate")
        data = asr()
        data["resources"] = {
            "duration_ms": 10000,
            "battery_start_permille": 800,
            "battery_end_permille": 797,
            "temperature_start_millicelsius": 30000,
            "temperature_end_millicelsius": 31000,
            "thermal_status_start": "none",
            "thermal_status_end": "light",
            "provenance": "Invented observation.",
        }
        self.assertEqual(data["resources"], self.scored(data)["resource_observation"])
        data["resources"]["duration_ms"] += 1
        self.invalid(data, "interval must equal")

    def test_local_score_pins_corpus_and_excludes_holdout(self):
        data = asr()
        data["kind"] = "local-measurement"
        self.invalid(data, "manifest hash mismatch")
        data["corpus_manifest_sha256"] = digest(ROOT / "corpus-local" / "dataset.json")
        data["clip_id"] = "clip-m"
        self.invalid(data, "only known dev")
        data["clip_id"] = "social-01"
        self.invalid(data, "duration mismatch")
        data["duration_ms"] = 95734
        data["chunks"][-1]["end_ms"] = 95734
        self.assertFalse(self.scored(data)["media_bytes_verified"])
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(Invalid):
            score(data, ROOT / "corpus-local", Path(temp))
        data["timebase"] = "capture"
        self.invalid(data, "uses media time")
        data["timebase"] = "media"
        data["model"] = "groq/whisper-large-v3"
        self.invalid(data, "approval reference")

    def test_synthetic_cannot_claim_corpus_media_verification(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(Invalid):
            score(asr(), ROOT / "corpus-local", Path(temp))

    def test_invalid_corpus_manifest_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            (path / "dataset.json").write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(Invalid, "explicit local"):
                plan(path)

    def test_corpus_jsonl_preserves_unicode_line_separators(self):
        source = ROOT / "corpus-local"
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            manifest = json.loads((source / "dataset.json").read_text())
            for name in manifest["files"]:
                (directory / name).write_bytes((source / name).read_bytes())
            clip_path = directory / "clips.jsonl"
            clips = [json.loads(line) for line in clip_path.read_text().splitlines()]
            clips[0]["rights"]["attribution"] += "\u2028Synthetic test addition"
            clip_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in clips),
                encoding="utf-8",
            )
            manifest["files"]["clips.jsonl"] = digest(clip_path)
            (directory / "dataset.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(7, len(plan(directory)["dev_clip_ids"]))

    def test_strict_input_types_and_fields(self):
        for key, value in (
            ("unexpected", 1),
            ("duration_ms", True),
            ("duration_ms", 1.5),
            ("duration_ms", "10000"),
            ("task", "unknown"),
            ("model", "paid/whisper-1"),
            ("resources", {}),
            ("limitations", []),
            ("schema_version", "res02-v0"),
            ("wall_ms", float("nan")),
        ):
            data = asr()
            if key == "wall_ms":
                data["chunks"][0][key] = value
            else:
                data[key] = value
            with self.subTest(key=key):
                self.invalid(data, ".")
        data = asr()
        data["duration_ms"] = 10000.0
        self.assertEqual(0, self.scored(data)["metrics"]["word_errors"]["wer"])

    def test_ocr_sampling_brief_card_and_deduplication(self):
        result = self.scored(ocr())["metrics"]
        self.assertEqual(1, result["fixed_5s"]["temporal_miss_rate"])
        self.assertEqual(0, result["change_triggered"]["temporal_miss_rate"])
        self.assertEqual(0, result["change_triggered"]["recognition_miss_rate"])
        self.assertEqual(0, result["frame_weighted_word_errors"]["wer"])
        self.assertEqual(1, len(result["deduplicated_tracks"]))
        self.assertEqual(
            [1000, 1500],
            [o["presentation_ms"] for o in result["deduplicated_tracks"][0]["observations"]],
        )
        self.assertEqual(
            [100, 100, 4000, 2000],
            result["deduplicated_tracks"][0]["observations"][0]["box"],
        )

    def test_ocr_end_exclusive_visibility(self):
        data = ocr()
        data["reference"][0]["end_ms"] = 1000
        data["reference"][0]["start_ms"] = 500
        result = self.scored(data)["metrics"]
        self.assertEqual(1, result["change_triggered"]["temporal_miss_rate"])
        self.assertEqual(2, result["unmatched_detections"])
        self.assertIsNone(result["frame_weighted_word_errors"]["wer"])
        self.assertEqual(6, result["frame_weighted_word_errors"]["insertion"])

    def test_ocr_same_frames_across_models_and_reference_digest(self):
        data = ocr()
        first = self.scored(data)
        data["model"] = "tesseract/eng-best"
        self.assertEqual(
            first["metrics"]["frame_set_sha256"],
            self.scored(data)["metrics"]["frame_set_sha256"],
        )
        self.assertEqual(first["reference_sha256"], self.scored(data)["reference_sha256"])
        data["frames"][0]["sha256"] = "e" * 64
        self.assertNotEqual(
            first["metrics"]["frame_set_sha256"],
            self.scored(data)["metrics"]["frame_set_sha256"],
        )

    def test_ocr_one_to_one_spatial_matching(self):
        data = ocr()
        data["frames"][1]["detections"] *= 2
        result = self.scored(data)["metrics"]
        self.assertEqual(1, result["unmatched_detections"])
        self.assertEqual(3, result["frame_weighted_word_errors"]["insertion"])
        data["reference_basis"] = "selected-regions"
        self.assertEqual(0, self.scored(data)["metrics"]["frame_weighted_word_errors"]["insertion"])
        data = ocr()
        data["frames"][1]["detections"][0]["box"] = [6000, 6000, 9000, 9000]
        result = self.scored(data)["metrics"]
        self.assertEqual(1, result["change_triggered"]["recognition_miss_rate"])

    def test_ocr_missing_output_and_track_break(self):
        data = ocr()
        data["frames"][1]["status"] = "OCR_UNAVAILABLE"
        self.invalid(data, "cannot carry")
        data["frames"][1]["detections"] = []
        result = self.scored(data)["metrics"]
        self.assertEqual(1, result["failed_frames"])
        self.assertEqual(1, result["change_triggered"]["recognition_miss_rate"])
        self.assertEqual(0, result["change_triggered"]["temporal_miss_rate"])
        data = ocr()
        data["frames"].insert(
            2,
            {
                "id": "frame-absent",
                "presentation_ms": 1200,
                "sha256": "e" * 64,
                "wall_ms": 1,
                "status": "ok",
                "detections": [],
            },
        )
        self.assertEqual(2, len(self.scored(data)["metrics"]["deduplicated_tracks"]))

    def test_ocr_cadence_and_frame_validation(self):
        mutations = [
            ("fixed_frame_ids", ["frame-zero"]),
            ("fixed_frame_ids", ["frame-zero", "frame-change"]),
            ("change_triggered_frame_ids", ["unknown"]),
            ("change_triggered_frame_ids", ["frame-zero", "frame-zero"]),
        ]
        for key, value in mutations:
            data = ocr()
            data[key] = value
            with self.subTest(key=key):
                self.invalid(data, "sample|tolerance|unknown")
        data = ocr()
        data["frames"][3]["presentation_ms"] = 5100
        self.scored(data)
        data["frames"][3]["presentation_ms"] += 1
        self.invalid(data, "tolerance")
        data = ocr()
        data["reference"][0]["box"][2] = 50
        self.invalid(data, "positive area")
        data = ocr()
        data["frames"][2]["id"] = data["frames"][1]["id"]
        self.invalid(data, "duplicate frame")

    def test_cli_outputs_errors_and_input_hash_without_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "observations.json"
            path.write_text(json.dumps(asr()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(["score", str(path)])
            self.assertEqual(0, code)
            self.assertEqual(digest(path), json.loads(output.getvalue())["input_sha256"])
            data = asr()
            data["chunks"][1].update(status="ASR_UNAVAILABLE", segments=[])
            data["timestamp_pairs"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(1, main(["score", str(path)]))
            for text in (
                '{"task": "asr", "task": "ocr"}',
                '{"bad": NaN}',
                "[]",
                '{"task": []}',
                '{"task": {}}',
            ):
                path.write_text(text, encoding="utf-8")
                error = io.StringIO()
                with contextlib.redirect_stderr(error):
                    self.assertEqual(2, main(["score", str(path)]))
                self.assertIn("RES-02 failed:", error.getvalue())
            self.assertEqual([path], list(Path(temp).iterdir()))

    def test_scoring_does_not_mutate_observations(self):
        for data in (asr(), ocr()):
            original = copy.deepcopy(data)
            self.scored(data)
            self.assertEqual(original, data)


if __name__ == "__main__":
    unittest.main()
