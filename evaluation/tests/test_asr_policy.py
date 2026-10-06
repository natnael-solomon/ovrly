"""Local test doubles for the RES-02b ASR adapter policy. Synthetic, not provider evidence."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asr_policy import (  # noqa: E402
    NO_SPEECH,
    OK,
    REASON_INVALID_RESPONSE,
    REASON_MISSING_AUDIO,
    REASON_MODEL_UNAVAILABLE,
    REASON_OFFLINE,
    REASON_OUTAGE,
    REASON_QUOTA,
    REASON_TOO_LARGE,
    REASON_UNKNOWN_OUTCOME,
    UNAVAILABLE,
    Chunk,
    ProviderError,
    transcribe_chunk,
)


def chunk(seq=3, length_ms=10000, pcm=None):
    start = seq * 10000
    return Chunk(seq, start, start + length_ms, b"\0\0" * (length_ms * 16) if pcm is None else pcm)


class Double:
    def __init__(self, name, result):
        self.name = name
        self.result = result
        self.calls = 0

    def transcribe(self, _chunk):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def said(*segments):
    return {"segments": [{"start": s, "end": e, "text": t} for s, e, t in segments]}


class PolicyTest(unittest.TestCase):
    def test_success_adds_chunk_offset_once(self):
        c = chunk()
        out = transcribe_chunk(c, Double("hosted", said((0.5, 2.0, "not true"))))
        self.assertEqual(out.status, OK)
        self.assertEqual(out.capture_segments(c), [(30500, 32000, "not true")])

    def test_empty_observed_speech_is_no_speech_not_failure(self):
        out = transcribe_chunk(chunk(), Double("hosted", said()))
        self.assertEqual(out.status, NO_SPEECH)
        self.assertEqual(out.segments, [])
        self.assertIsNone(out.reason)

    def test_invalid_timestamps_are_flagged_not_clamped(self):
        c = chunk(length_ms=4000)
        out = transcribe_chunk(
            c,
            Double("hosted", said((0.0, 1.0, "fine"), (3.5, 9.0, "overrun"), (2.0, 1.0, "rev"))),
        )
        self.assertEqual(out.status, OK)
        self.assertEqual(out.invalid_timestamps, 2)
        overrun = out.segments[1]
        self.assertFalse(overrun.timing_valid)
        self.assertIsNone(overrun.end_ms)
        self.assertEqual(overrun.raw_end_s, 9.0)
        self.assertEqual(out.capture_segments(c), [(30000, 31000, "fine")])

    def test_non_finite_timestamp_is_invalid(self):
        out = transcribe_chunk(chunk(), Double("hosted", said((float("nan"), 1.0, "x"))))
        self.assertEqual(out.invalid_timestamps, 1)
        self.assertIsNone(out.segments[0].raw_start_s)

    def test_missing_samples_are_unavailable_without_calling_anyone(self):
        primary = Double("hosted", said((0, 1, "x")))
        for pcm in (b"", b"\0", b"\0\0" * 100):
            out = transcribe_chunk(chunk(pcm=pcm), primary)
            self.assertEqual((out.status, out.reason), (UNAVAILABLE, REASON_MISSING_AUDIO))
        self.assertEqual(primary.calls, 0)

    def test_short_final_chunk_is_valid(self):
        out = transcribe_chunk(chunk(seq=17, length_ms=2500), Double("hosted", said((0, 2.4, "end"))))
        self.assertEqual(out.status, OK)

    def test_file_cap(self):
        out = transcribe_chunk(chunk(), Double("hosted", said()), file_cap_bytes=1000)
        self.assertEqual(out.reason, REASON_TOO_LARGE)

    def test_quota_without_fallback_is_explicit_unavailable(self):
        out = transcribe_chunk(chunk(), Double("hosted", ProviderError("rate_limited")))
        self.assertEqual((out.status, out.reason), (UNAVAILABLE, REASON_QUOTA))
        self.assertEqual(out.segments, [])

    def test_outage_uses_on_device_fallback_and_names_it(self):
        fallback = Double("vosk/small-en", said((1, 2, "fallback text")))
        out = transcribe_chunk(chunk(), Double("hosted", ProviderError("transient")), fallback)
        self.assertEqual((out.status, out.recognizer), (OK, "vosk/small-en"))
        self.assertEqual([a["result"] for a in out.attempts], ["transient", "ok"])

    def test_fallback_model_unavailable_reports_last_reason(self):
        out = transcribe_chunk(
            chunk(),
            Double("hosted", ProviderError("rate_limited")),
            Double("vosk/small-en", ProviderError("model_unavailable")),
        )
        self.assertEqual((out.status, out.reason), (UNAVAILABLE, REASON_MODEL_UNAVAILABLE))
        self.assertEqual(len(out.attempts), 2)

    def test_offline(self):
        out = transcribe_chunk(chunk(), Double("hosted", ProviderError("offline")))
        self.assertEqual(out.reason, REASON_OFFLINE)
        out = transcribe_chunk(chunk(), Double("hosted", ProviderError("transient")))
        self.assertEqual(out.reason, REASON_OUTAGE)

    def test_invalid_response_shape(self):
        out = transcribe_chunk(chunk(), Double("hosted", {"text": "no segments"}))
        self.assertEqual(out.reason, REASON_INVALID_RESPONSE)

    def test_unknown_outcome_retry_is_visible_in_local_ledger(self):
        ledger = set()
        primary = Double("hosted", ProviderError("unknown_outcome"))
        first = transcribe_chunk(chunk(), primary, ledger=ledger)
        self.assertEqual(first.reason, REASON_UNKNOWN_OUTCOME)
        primary.result = said((0, 1, "late"))
        second = transcribe_chunk(chunk(), primary, ledger=ledger)
        self.assertEqual(primary.calls, 1)
        self.assertEqual((second.status, second.reason), (UNAVAILABLE, REASON_UNKNOWN_OUTCOME))
        self.assertEqual(second.attempts, [{"recognizer": "hosted", "result": "duplicate_send_blocked"}])

    def test_never_success_shaped_on_failure(self):
        for kind in ("rate_limited", "transient", "unknown_outcome", "offline", "model_unavailable"):
            out = transcribe_chunk(chunk(), Double("hosted", ProviderError(kind)))
            self.assertEqual(out.status, UNAVAILABLE)
            self.assertIsNotNone(out.reason)
            self.assertEqual(out.segments, [])


if __name__ == "__main__":
    unittest.main()
