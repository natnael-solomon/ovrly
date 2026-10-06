"""Reference ASR chunk adapter policy for RES-02b (#103), handed to BE-07 (#20).

Framework-neutral and dependency-free. It defines how one capture chunk becomes
an explicit transcription outcome, so that a provider failure, a missing
model, invalid timestamps or missing audio is never reported as an empty
success, never clamped and never replaced with microphone audio.

The production handler in `backend/services` owns transport, persistence and
job retries; this module only fixes the observable contract and is exercised
with local test doubles in `tests/test_asr_policy.py`. Synthetic failures in
those tests are not observed provider incidents.
"""

import math
from dataclasses import dataclass, field

PCM_BYTES_PER_MS = 32  # 16 kHz mono signed 16-bit
OK = "ok"
NO_SPEECH = "no_speech"
UNAVAILABLE = "ASR_UNAVAILABLE"

# Reasons are stable identifiers for the unavailable outcome.
REASON_MISSING_AUDIO = "missing_audio"
REASON_QUOTA = "quota_exhausted"
REASON_OUTAGE = "provider_unavailable"
REASON_UNKNOWN_OUTCOME = "unknown_outcome"
REASON_MODEL_UNAVAILABLE = "model_unavailable"
REASON_INVALID_RESPONSE = "invalid_response"
REASON_OFFLINE = "offline"
REASON_TOO_LARGE = "chunk_exceeds_file_cap"


class ProviderError(Exception):
    """Raised by a recognizer double or adapter. `kind` selects the policy branch."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # rate_limited | transient | unknown_outcome | offline | model_unavailable


@dataclass(frozen=True)
class Chunk:
    seq: int
    start_ms: int
    end_ms: int
    pcm: bytes

    @property
    def length_ms(self):
        return self.end_ms - self.start_ms


@dataclass
class Segment:
    start_ms: int | None  # chunk-relative; None when the recognizer timing was invalid
    end_ms: int | None
    text: str
    raw_start_s: float | None = None
    raw_end_s: float | None = None
    timing_valid: bool = True


@dataclass
class Outcome:
    seq: int
    status: str
    recognizer: str | None
    segments: list = field(default_factory=list)
    reason: str | None = None
    attempts: list = field(default_factory=list)
    invalid_timestamps: int = 0

    def capture_segments(self, chunk):
        """Capture-relative segments: the chunk offset is added exactly once."""
        return [
            (chunk.start_ms + s.start_ms, chunk.start_ms + s.end_ms, s.text)
            for s in self.segments
            if s.timing_valid
        ]


def audio_problem(chunk, file_cap_bytes):
    """Missing samples are a visible gap, not silence."""
    expected = chunk.length_ms * PCM_BYTES_PER_MS
    if not chunk.pcm or len(chunk.pcm) % 2:
        return REASON_MISSING_AUDIO
    if len(chunk.pcm) < expected * 0.9:
        return REASON_MISSING_AUDIO
    if file_cap_bytes is not None and len(chunk.pcm) + 44 > file_cap_bytes:
        return REASON_TOO_LARGE
    return None


def validate_segments(raw, length_ms):
    """Keep text; mark (never clamp) segments whose raw times fall outside the chunk."""
    segments, invalid = [], 0
    for item in raw:
        start, end, text = item.get("start"), item.get("end"), (item.get("text") or "").strip()
        if not text:
            continue
        numeric = all(type(v) in (int, float) and math.isfinite(v) for v in (start, end))
        valid = numeric and 0 <= start < end and end * 1000 <= length_ms
        if not valid:
            invalid += 1
        segments.append(
            Segment(
                start_ms=round(start * 1000) if valid else None,
                end_ms=round(end * 1000) if valid else None,
                text=text,
                raw_start_s=start if numeric else None,
                raw_end_s=end if numeric else None,
                timing_valid=valid,
            )
        )
    return segments, invalid


def _segment_items(raw):
    """Each segment must be an object whose text is a string or absent."""
    for item in raw:
        if not isinstance(item, dict):
            raise ProviderError("invalid_response", "segment is not an object")
        text = item.get("text")
        if text is not None and not isinstance(text, str):
            raise ProviderError("invalid_response", "segment text is not a string")
    return raw


def _attempt(recognizer, chunk):
    """Run one recognizer; returns (outcome fields) or raises ProviderError."""
    response = recognizer.transcribe(chunk)
    if not isinstance(response, dict) or not isinstance(response.get("segments"), list):
        raise ProviderError("invalid_response", "missing segments list")
    return validate_segments(_segment_items(response["segments"]), chunk.length_ms)


REASON_BY_KIND = {
    "rate_limited": REASON_QUOTA,
    "transient": REASON_OUTAGE,
    "unknown_outcome": REASON_UNKNOWN_OUTCOME,
    "offline": REASON_OFFLINE,
    "model_unavailable": REASON_MODEL_UNAVAILABLE,
    "invalid_response": REASON_INVALID_RESPONSE,
}


def transcribe_chunk(chunk, primary, fallback=None, *, file_cap_bytes=None, ledger=None):
    """Return an explicit Outcome for one chunk.

    `primary` and `fallback` expose `name` and `transcribe(chunk) -> {"segments": [...]}`
    with segment times in seconds relative to the chunk. `ledger` (a set) records
    `(recognizer, seq)` pairs whose send succeeded or ended with an unknown outcome, so
    resending them is blocked and visible rather than silently duplicated. Known failures
    (rate limited, offline, transient, model unavailable, invalid response) are not
    recorded and can be retried. The ledger is local bookkeeping, not proof of what the
    provider billed or processed.
    """
    problem = audio_problem(chunk, file_cap_bytes)
    if problem is not None:
        return Outcome(chunk.seq, UNAVAILABLE, None, reason=problem)
    attempts = []
    for recognizer in (r for r in (primary, fallback) if r is not None):
        key = (recognizer.name, chunk.seq)
        if ledger is not None and key in ledger:
            attempts.append({"recognizer": recognizer.name, "result": "duplicate_send_blocked"})
            continue
        try:
            segments, invalid = _attempt(recognizer, chunk)
        except ProviderError as error:
            if ledger is not None and error.kind == "unknown_outcome":
                ledger.add(key)
            attempts.append({"recognizer": recognizer.name, "result": error.kind})
            continue
        if ledger is not None:
            ledger.add(key)
        attempts.append({"recognizer": recognizer.name, "result": "ok"})
        return Outcome(
            chunk.seq,
            OK if segments else NO_SPEECH,
            recognizer.name,
            segments=segments,
            attempts=attempts,
            invalid_timestamps=invalid,
        )
    last = attempts[-1]["result"] if attempts else "unknown_outcome"
    reason = REASON_BY_KIND.get(last, REASON_UNKNOWN_OUTCOME)
    if last == "duplicate_send_blocked":
        reason = REASON_UNKNOWN_OUTCOME
    return Outcome(chunk.seq, UNAVAILABLE, None, reason=reason, attempts=attempts)
