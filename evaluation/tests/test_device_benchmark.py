"""Synthetic tests for RES-02b host helpers. Invented data, not device evidence."""

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import device_benchmark as db  # noqa: E402


def write_capture(root, chunks, duration, skipped=(), gaps=(), corrupt=None):
    (root / "chunks").mkdir(parents=True)
    rows = []
    for seq, start, end, pcm_bytes in chunks:
        meta = {"seq": seq, "start_ms": start, "end_ms": end, "timebase": "capture",
                "audio": {"bytes": pcm_bytes}, "text_observations": [], "recognizer": None,
                "sampling": {"policy": "fixed"}}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in (("chunk.json", json.dumps(meta).encode()),
                               ("audio-16000-mono-s16le.pcm", b"\0" * pcm_bytes)):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(info, data)
        data = buffer.getvalue()
        name = f"chunk-{seq:03d}.zip"
        (root / "chunks" / name).write_bytes(data if seq != corrupt else data + b"x")
        rows.append({"seq": seq, "startMs": start, "endMs": end, "modality": "speech", "file": name,
                     "sizeBytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "audioBytes": pcm_bytes, "frameOffsetsMs": [start + 10]})
    manifest = {"manifestVersion": 2, "timebase": "capture", "chunkDurationMs": 10000,
                "durationMs": duration, "chunks": rows, "skippedSeqs": list(skipped),
                "gaps": list(gaps), "evictedSeqs": [], "stopReason": "test"}
    (root / "capture.json").write_text(json.dumps(manifest), encoding="utf-8")


def run_verify(root, out):
    args = type("A", (), {"capture": str(root), "chunk_ms": 10000, "upload_cap": 1 << 28,
                          "out": str(out)})
    with redirect_stdout(io.StringIO()):
        code = db.verify_capture(args)
    return code, json.loads(Path(out).read_text(encoding="utf-8"))


class SegmentTest(unittest.TestCase):
    def test_whisper_segments_kept_raw(self):
        row = {"status": "ok", "segments": [{"start_ms": 0, "end_ms": 12000, "text": " hi "}]}
        segments = db.raw_segments(row, 10000)
        self.assertEqual(segments, [{"start": 0.0, "end": 12.0, "text": "hi"}])
        self.assertEqual(db.timestamp_faults(segments, 10000)["end_after_chunk"], 1)

    def test_vosk_results_use_word_bounds(self):
        row = {"status": "ok", "results": [
            {"result": [{"start": 1.2, "end": 1.5, "word": "a"}, {"start": 1.6, "end": 2.0, "word": "b"}],
             "text": "a b"},
            {"text": ""},
        ]}
        self.assertEqual(db.raw_segments(row, 10000), [{"start": 1.2, "end": 2.0, "text": "a b"}])

    def test_failed_row_has_no_segments(self):
        self.assertEqual(db.raw_segments({"status": "ASR_UNAVAILABLE"}, 10000), [])

    def test_non_speech_annotation_is_not_scored(self):
        row = {"status": "ok", "segments": [{"start_ms": 0, "end_ms": 10000, "text": " [BLANK_AUDIO]"},
                                            {"start_ms": 0, "end_ms": 900, "text": " (music) real"}]}
        segments = db.raw_segments(row, 10000)
        self.assertTrue(segments[0]["annotation"])
        self.assertEqual(db.chunk_text(segments), "(music) real")

    def test_reversed_and_negative(self):
        faults = db.timestamp_faults([{"start": 2, "end": 1, "text": "x"},
                                      {"start": -0.1, "end": 1, "text": "y"}], 10000)
        self.assertEqual(faults, {"reversed_or_empty": 1, "negative_start": 1, "end_after_chunk": 0})

    def test_read_run_accepts_multiline_vosk_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text('{"type":"init"}\n{"type":"chunk","id":"a","results":[{\n  "text" : ""\n}]}\n'
                            '{"type":"end","items":1}\n', encoding="utf-8")
            init, chunks, end = db.read_run(path)
            self.assertEqual(chunks["a"]["results"], [{"text": ""}])
            self.assertEqual(end["items"], 1)

    def test_read_run_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.jsonl"
            path.write_text('{"type":"init"}\n{"type":"chunk","id":"a"}\n{"type":"chunk","id":"a"}\n',
                            encoding="utf-8")
            with self.assertRaises(db.Invalid):
                db.read_run(path)


class CaptureTest(unittest.TestCase):
    def test_valid_capture_with_short_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cap"
            write_capture(root, [(0, 0, 10000, 320000), (1, 10000, 12500, 80000)], 12500)
            code, report = run_verify(root, Path(tmp) / "r.json")
            self.assertEqual(code, 0, report["checks"])
            self.assertEqual(report["final_chunk"]["end_ms"], 12500)
            self.assertEqual(report["pcm_minus_nominal_total_ms"], 0)

    def test_three_minute_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cap"
            write_capture(root, [(s, s * 10000, (s + 1) * 10000, 320000) for s in range(18)], 180000)
            code, report = run_verify(root, Path(tmp) / "r.json")
            self.assertEqual(code, 0)
            self.assertEqual(report["final_chunk"]["seq"], 17)

    def test_missing_seq_and_tampering_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cap"
            write_capture(root, [(0, 0, 10000, 320000), (2, 20000, 30000, 320000)], 30000, corrupt=2)
            code, report = run_verify(root, Path(tmp) / "r.json")
            failed = {c["check"] for c in report["checks"] if not c["ok"]}
            self.assertEqual(code, 1)
            self.assertIn("seq_contiguous_with_skips", failed)
            self.assertIn("sha256_ok", failed)

    def test_skipped_seq_is_accounted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cap"
            write_capture(root, [(0, 0, 10000, 320000), (2, 20000, 30000, 320000)], 30000, skipped=[1],
                          gaps=[{"kind": "interrupted", "startMs": 10000, "endMs": 20000}])
            code, report = run_verify(root, Path(tmp) / "r.json")
            self.assertEqual(code, 0, report["checks"])
            self.assertEqual(len(report["gaps"]), 1)

    def test_audio_shortfall_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cap"
            write_capture(root, [(0, 0, 10000, 318400)], 10000)
            _, report = run_verify(root, Path(tmp) / "r.json")
            self.assertEqual(report["pcm_minus_nominal_total_ms"], -50)


class WindowTest(unittest.TestCase):
    def test_token_recall_is_multiset(self):
        self.assertEqual(db.token_recall(["a", "a", "b"], ["a", "b"]), 2 / 3)
        self.assertIsNone(db.token_recall([], ["a"]))

    def test_temporal_and_recognition_misses_are_separate(self):
        shots = {"card-0": {"regions": [{"text": "Brief card"}]}}
        window = {"clip_id": "social-01", "id": "window-18000-20000", "start_ms": 18000, "end_ms": 20000}
        rows = [
            {"probe_ms": 15000, "fixed_tick": True, "decision": "unchanged", "lines": []},
            {"probe_ms": 18000, "fixed_tick": False, "decision": "changed",
             "lines": [{"text": "brief"}]},
            {"probe_ms": 20000, "fixed_tick": True, "decision": "unchanged", "lines": []},
        ]
        kept = [r for r in rows if r["decision"] == "changed"]
        fixed = [r for r in rows if r["fixed_tick"]]
        result = db.window_result(window, rows, kept, fixed, shots)
        self.assertEqual(result["card_screenshot"], "card-0")
        self.assertTrue(result["fixed_5s"]["temporally_missed"])
        self.assertFalse(result["change_triggered"]["temporally_missed"])
        self.assertEqual(result["change_triggered"]["best_token_recall"], 0.5)
        self.assertTrue(result["change_triggered"]["read_at_half_or_more"])
        summary = db.window_summary([result])
        self.assertEqual(summary["fixed_5s"]["brief_windows_temporally_missed"], 1)
        self.assertEqual(summary["change_triggered"]["brief_windows_temporally_missed"], 0)


class ResourceTest(unittest.TestCase):
    def test_parsers_and_summary(self):
        battery = "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n" \
                  "  level: 87\n  scale: 100\n  temperature: 312\n  status: 2\n"
        thermal = "Thermal Status: 1\nCurrent temperatures from HAL:\n" \
                  "\tTemperature{mValue=36.5, mType=2, mName=battery, mStatus=0}\n"
        self.assertEqual(db.parse_battery(battery)["level"], "87")
        self.assertEqual(db.parse_thermal(thermal)["status"], 1)
        rows = [{"t_s": 0, "label": "x", "battery": db.parse_battery(battery),
                 "thermal": db.parse_thermal(thermal), "current_now": "-1200"},
                {"t_s": 180, "label": "x", "battery": db.parse_battery(battery.replace("87", "86")),
                 "thermal": db.parse_thermal(thermal), "current_now": "abc"}]
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = Path(tmp) / "s.json"
            args = type("A", (), {"log": str(log), "out": str(out)})
            with redirect_stdout(io.StringIO()):
                db.summarize_resources(args)
            summary = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual((summary["battery_start_permille"], summary["battery_end_permille"]), (870, 860))
        self.assertEqual(summary["battery_temperature_start_millicelsius"], 31200)
        self.assertTrue(summary["plugged_any_sample"])
        self.assertEqual(summary["current_now_samples"], 1)


if __name__ == "__main__":
    unittest.main()
