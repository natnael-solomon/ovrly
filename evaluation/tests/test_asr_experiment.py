import contextlib
import copy
import io
import json
import sys
import tempfile
import types
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asr_experiment import (
    MODELS,
    PLAN,
    attempt_name,
    checked_plan,
    prepare,
    read_srt,
    run,
    sample_clock,
    subtitle_timing,
    summarize,
    wav_bytes,
)
from validate import Invalid, check_schema, digest


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "existing.srt"

    def test_preserves_multiline_wording_and_bom(self):
        raw = "\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\nNot 10 kg.\r\nAgain.\r\n"
        self.path.write_bytes(raw.encode())
        self.assertEqual(
            read_srt(self.path, 2000),
            [{"start_ms": 0, "end_ms": 1000, "text": "Not 10 kg.\nAgain."}],
        )
        self.assertEqual(self.path.read_bytes(), raw.encode())

    def test_rejects_invalid_sources_without_repair(self):
        samples = [
            "",
            "2\n00:00:00,000 --> 00:00:01,000\nText",
            "1\n00:00:00,000 --> 00:00:02,001\nText",
            "1\n00:00:00,000 --> 00:00:00,000\nText",
            "1\n00:00:60,000 --> 00:00:61,000\nText",
            "1\n00:00:00,000 --> 00:00:01,000\n<b>Text</b>",
            "1\n00:00:00,000 --> 00:00:01,000\n...",
            "1\n00:00:00,000 --> 00:00:01,000\nFirst\n\n2\n00:00:00,999 --> 00:00:02,000\nSecond",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.path.write_text(text)
                with self.assertRaises(Invalid):
                    read_srt(self.path, 2000)
                self.assertEqual(self.path.read_text(), text)

    def test_wav_is_exact_mono_pcm(self):
        for duration in (10, 15000):
            pcm = b"\x00\x00" * duration * 16
            encoded = wav_bytes(pcm)
            self.assertEqual(len(encoded), 44 + duration * 32)
            with wave.open(io.BytesIO(encoded)) as stream:
                self.assertEqual(
                    (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()),
                    (1, 2, 16000),
                )
                self.assertEqual(stream.readframes(stream.getnframes()), pcm)

    def test_plan_mismatch_is_rejected_before_loading_media(self):
        self.path.write_text("{}")
        with self.assertRaisesRegex(Invalid, "approval digest mismatch"):
            checked_plan(self.path, "0" * 64)

    def test_timestamp_audit_matches_only_complete_cues_and_offsets_once(self):
        reference = [
            {"start_ms": 10100, "end_ms": 10800, "text": "not ten"},
            {"start_ms": 11000, "end_ms": 11800, "text": "more words"},
        ]
        chunks = [
            {
                "seq": 1,
                "start_ms": 10000,
                "end_ms": 12000,
                "segments": [
                    {"start": 0.2, "end": 0.9, "text": "not ten"},
                    {"start": 1.1, "end": 1.5, "text": "more"},
                    {"start": 1.6, "end": 2.1, "text": "words"},
                ],
            }
        ]
        result = subtitle_timing(reference, chunks)
        self.assertEqual(result["matched_segments"], 1)
        self.assertEqual(result["matches"][0]["start_ms"], 10200)
        self.assertEqual(result["start_signed_ms"]["mean"], 100)
        self.assertEqual(len(result["outside_chunk_segments"]), 1)
        self.assertEqual(result["outside_chunk_segments"][0]["end_ms"], 12100)

    def test_timestamp_audit_rejects_nonfinite_and_does_not_match_errors(self):
        reference = [{"start_ms": 0, "end_ms": 1000, "text": "not ten"}]
        chunk = {
            "seq": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "segments": [{"start": 0, "end": 1, "text": "ten"}],
        }
        self.assertEqual(subtitle_timing(reference, [chunk])["matched_segments"], 0)
        chunk["segments"][0]["end"] = float("nan")
        with self.assertRaises(Invalid):
            subtitle_timing(reference, [chunk])

    def test_quantized_timestamps_preserve_continuous_samples(self):
        frame = types.SimpleNamespace(
            pts=24, time_base=Fraction(1, 1000), sample_rate=48000, samples=1024
        )
        self.assertEqual(sample_clock(frame, 1168), (2192, 16))
        self.assertEqual(frame.pts, 1168)
        self.assertEqual(frame.time_base, Fraction(1, 48000))

    def test_real_clock_discontinuity_is_not_silently_corrected(self):
        frame = types.SimpleNamespace(
            pts=30, time_base=Fraction(1, 1000), sample_rate=48000, samples=1024
        )
        with self.assertRaisesRegex(Invalid, "source clock discontinuity"):
            sample_clock(frame, 1168)


class HostedRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "plan.json"
        (self.path.parent / "chunk.wav").write_bytes(wav_bytes(b"\x00\x00" * 160))
        self.row = {
            "clip_id": "example-dev",
            "chunk_ms": 10000,
            "chunks": [{"seq": 0, "path": "chunk.wav"}],
        }
        self.plan = {"observations": [self.row], "settings": {}, "maximum_requests": 2}
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        response = types.SimpleNamespace(status_code=200, text='{"text": "Test"}', headers={})
        self.client.post.return_value = response
        self.httpx = types.SimpleNamespace(
            Client=MagicMock(return_value=self.client),
            RequestError=type("RequestError", (Exception,), {}),
        )
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, {"httpx": self.httpx}))
        stack.enter_context(patch.dict("os.environ", {"GROQ_API_KEY": "test-only"}))
        stack.enter_context(patch("asr_experiment.checked_plan", return_value=self.plan))
        stack.enter_context(patch("asr_experiment.time.sleep"))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_paired_no_redirects_and_completed_resume_does_not_upload(self):
        self.assertEqual(run(self.path, "a" * 64)["completed_requests"], 2)
        self.assertEqual(self.client.post.call_count, 2)
        self.httpx.Client.assert_called_once_with(timeout=120, follow_redirects=False)
        run(self.path, "a" * 64)
        self.assertEqual(self.client.post.call_count, 2)
        artifacts = "".join(p.read_text() for p in (self.path.parent / "attempts").glob("*.json"))
        self.assertNotIn("test-only", artifacts)

    def test_failed_request_is_recorded_and_stops_without_retry(self):
        self.client.post.return_value = types.SimpleNamespace(
            status_code=429, text="limited", headers={"retry-after": "60"}
        )
        with self.assertRaisesRegex(Invalid, "HTTP 429"):
            run(self.path, "a" * 64)
        self.assertEqual(self.client.post.call_count, 1)
        with self.assertRaisesRegex(Invalid, "previous failure"):
            run(self.path, "a" * 64)
        self.assertEqual(self.client.post.call_count, 1)

    def test_interrupted_attempt_cannot_be_reissued(self):
        directory = self.path.parent / "attempts"
        directory.mkdir()
        name = attempt_name(self.row, self.row["chunks"][0], MODELS[0])
        (directory / f"{name}.started.json").write_text("{}")
        with self.assertRaisesRegex(Invalid, "interrupted"):
            run(self.path, "a" * 64)
        self.client.post.assert_not_called()

    def test_transport_error_does_not_log_credentials(self):
        self.client.post.side_effect = self.httpx.RequestError("test-only private transport detail")
        with self.assertRaisesRegex(Invalid, "provider transport failed"):
            run(self.path, "a" * 64)
        self.assertEqual(self.client.post.call_count, 1)
        artifacts = "".join(p.read_text() for p in (self.path.parent / "attempts").glob("*.json"))
        self.assertNotIn("test-only", artifacts)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "dataset.json").write_text("{}")
        (self.root / "media.wav").write_bytes(wav_bytes(b"\x00\x00" * 320))
        self.source = self.root / "original.srt"
        self.source.write_text("1\n00:00:00,000 --> 00:00:00,020\nTest.")
        self.clip = {
            "clip_id": "test-dev",
            "split": "dev",
            "duration_ms": 20,
            "media": {"path": "media.wav", "sha256": digest(self.root / "media.wav")},
        }
        self.specification = self.root / "spec.json"
        self.spec = {
            "approval": "invented-test-only",
            "references": [
                {"clip_id": "test-dev", "path": str(self.source), "sha256": digest(self.source)}
            ],
            "limits": {
                "file_cap_bytes": 25000000,
                "minimum_audio_ms": 10000,
                "audio_ms_per_hour": 7200000,
                "requests_per_day": 2000,
                "provenance": "Invented test limits",
            },
        }
        self.specification.write_text(json.dumps(self.spec))
        self.output = self.root / "run"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.dict(sys.modules, {"av": types.SimpleNamespace(__version__="test")})
        )
        stack.enter_context(patch("asr_experiment.corpus", return_value=({}, [self.clip])))
        stack.enter_context(
            patch(
                "asr_experiment.pcm_audio",
                return_value=(
                    b"\x00\x00" * 320,
                    {
                        "zero_filled_samples": 0,
                        "out_of_media_samples_removed": 0,
                        "maximum_source_clock_quantization_samples": 0,
                        "source_sample_rate": 16000,
                    },
                ),
            )
        )

    def prepared(self):
        result = prepare(self.root, self.root, self.specification, self.output)
        return self.output / "plan.json", result["plan_sha256"]

    def test_references_are_reused_and_paired_chunks_match(self):
        check_schema(PLAN)
        before = self.source.read_bytes()
        path, sha = self.prepared()
        plan = checked_plan(path, sha)
        self.assertEqual(plan["maximum_requests"], 4)
        self.assertEqual(
            plan["observations"][0]["chunks"][0]["sha256"],
            plan["observations"][1]["chunks"][0]["sha256"],
        )
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(list(self.output.glob("*.srt")), [])
        with self.assertRaises(FileExistsError):
            self.prepared()

    def test_holdout_fails_before_any_output(self):
        self.clip["split"] = "test"
        with self.assertRaisesRegex(Invalid, "holdout"):
            self.prepared()
        self.assertFalse(self.output.exists())

    def test_budget_is_enforced_before_preparation(self):
        self.spec["limits"]["audio_ms_per_hour"] = 1
        self.specification.write_text(json.dumps(self.spec))
        with self.assertRaisesRegex(Invalid, "hourly audio budget"):
            self.prepared()
        self.assertFalse(self.output.exists())

    def test_chunk_tampering_fails(self):
        path, sha = self.prepared()
        plan = json.loads(path.read_text())
        chunk = self.output / plan["observations"][0]["chunks"][0]["path"]
        chunk.write_bytes(b"changed")
        with self.assertRaisesRegex(Invalid, "changed"):
            checked_plan(path, sha)

    def test_plan_shape_and_source_identity_are_validated(self):
        path, _ = self.prepared()
        original = json.loads(path.read_text())
        cases = [
            ("settings", {"language": "en", "temperature": "1", "response_format": "verbose_json"}),
            ("observations", original["observations"][:1]),
            ("maximum_requests", 9),
            ("limits", None),
        ]
        for field, value in cases:
            with self.subTest(field=field):
                plan = copy.deepcopy(original)
                plan[field] = value
                path.write_text(json.dumps(plan))
                with self.assertRaises(Invalid):
                    checked_plan(path, digest(path))

    def test_partial_summary_does_not_leave_output_that_blocks_completion(self):
        path, sha = self.prepared()
        with self.assertRaises(FileNotFoundError):
            summarize(path, sha)
        self.assertFalse((self.output / "scores").exists())

    def completed(self):
        path, sha = self.prepared()
        plan = json.loads(path.read_text())
        attempts = self.output / "attempts"
        attempts.mkdir()
        for row in plan["observations"]:
            for chunk in row["chunks"]:
                for model in MODELS:
                    record = {
                        "plan_sha256": sha,
                        "status": 200,
                        "wall_ms": 2,
                        "response": '{"text": "Test"}',
                    }
                    (attempts / f"{attempt_name(row, chunk, model)}.json").write_text(
                        json.dumps(record)
                    )
        return path, sha

    def test_summary_scores_real_contract_and_hashes_exact_inputs(self):
        path, sha = self.completed()
        with patch("benchmark.corpus", return_value=({}, [self.clip])):
            result = summarize(path, sha)
        self.assertEqual(len(result["comparisons"]), 4)
        self.assertTrue(all(r["word_errors"]["wer"] == 0 for r in result["comparisons"]))
        for file in (self.output / "scores").glob("*.score.json"):
            score = json.loads(file.read_text())
            source = file.with_name(file.name.replace(".score.json", ".input.json"))
            self.assertEqual(score["input_sha256"], digest(source))
            self.assertEqual(score["metrics"]["timestamp_drift_ms"]["paired_segments"], 0)
        with self.assertRaisesRegex(Invalid, "already exists"):
            summarize(path, sha)

    def test_invalid_provider_text_is_not_silently_empty_speech(self):
        path, sha = self.completed()
        file = next((self.output / "attempts").glob("*.json"))
        record = json.loads(file.read_text())
        record["response"] = '{"text": null}'
        file.write_text(json.dumps(record))
        with patch("benchmark.corpus", return_value=({}, [self.clip])):
            with self.assertRaisesRegex(Invalid, "missing transcription text"):
                summarize(path, sha)
        self.assertFalse((self.output / "scores").exists())


if __name__ == "__main__":
    unittest.main()
