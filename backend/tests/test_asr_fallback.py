"""RFC-D27 fallback policy without PostgreSQL: invented HTTP replies and an in-memory ledger.

The PostgreSQL recovery suites cover the same policy through the real ledger and worker
(``tests/recovery/test_speech.py``, ``test_speech_retry.py`` and
``test_capture_processing.py``).
"""

import typing
import uuid
from types import SimpleNamespace

import httpx
import pytest
from test_settings import URL

from services.api.schemas import SpeechReason
from services.asr import fallback
from services.asr.fallback import SpeechAudio, transcribe
from services.asr.groq import (
    FAILURE_BY_REASON,
    REASON_BY_FAILURE,
    ASRChunkTooLarge,
    ASRMissingAudio,
    ASRModelUnavailable,
    ASROffline,
    ASRQuotaExhausted,
    ASRUnavailable,
    ASRUnknownOutcome,
    GroqAdapter,
    failure_reason,
)
from services.pipeline.capture_media import BYTES_PER_MS, short_audio
from services.settings import Settings

PRIMARY, FALLBACK = "whisper-large-v3-turbo", "whisper-large-v3"
SEGMENT = {"start_ms": 0, "end_ms": 500, "text": "Invented words."}


def test_rfc_d27_models_are_the_defaults_and_must_differ():
    settings = Settings(database_url=URL, _env_file=None)
    assert fallback.models(settings) == (PRIMARY, FALLBACK)
    enabled = {
        "database_url": URL,
        "asr_enabled": True,
        "groq_api_key": "synthetic",
        "groq_account_id": "synthetic",
        "asr_limits_verified_on": "2026-10-07",
        "asr_requests_per_minute": 1,
        "asr_requests_per_day": 1,
        "asr_audio_seconds_per_hour": 1,
        "asr_audio_seconds_per_day": 1,
        "asr_minimum_billable_seconds": 1,
        "asr_audio_max_bytes": 1000,
        "_env_file": None,
    }
    assert Settings(**enabled).groq_fallback_model == FALLBACK
    for models in ({"groq_fallback_model": PRIMARY}, {"groq_fallback_model": " "}):
        with pytest.raises(ValueError, match="Hosted speech requires"):
            Settings(**enabled, **models)


def test_every_failure_maps_to_a_published_speech_reason():
    reasons = set(typing.get_args(SpeechReason))
    assert set(REASON_BY_FAILURE.values()) <= reasons
    assert set(FAILURE_BY_REASON) <= reasons
    for reason, error in FAILURE_BY_REASON.items():
        assert failure_reason(error.__name__) == reason
    assert failure_reason(None) == failure_reason("SomethingElse") == "provider_unavailable"
    assert failure_reason("uncertain") == "unknown_outcome"
    assert failure_reason("RateLimited") == "quota_exhausted"


@pytest.mark.parametrize(
    ("size", "short"),
    [(0, True), (31_999, True), (28_798, True), (28_800, False), (32_000, False)],
)
def test_missing_or_short_capture_audio_is_detected_before_any_call(size, short):
    assert BYTES_PER_MS == 32
    assert short_audio(bytes(size), 1000) is short


async def test_adapter_sends_the_requested_model():
    seen = []

    def replay(request):
        seen.append(request.content)
        return httpx.Response(200, json={"text": "", "segments": []})

    adapter = GroqAdapter(
        api_key="synthetic",
        model=PRIMARY,
        max_audio_bytes=100,
        transport=httpx.MockTransport(replay),
    )
    assert await adapter.transcribe(b"audio", duration_ms=1000) == []
    assert await adapter.transcribe(b"audio", duration_ms=1000, model=FALLBACK) == []
    assert f'name="model"\r\n\r\n{PRIMARY}\r\n'.encode() in seen[0]
    assert f'name="model"\r\n\r\n{FALLBACK}\r\n'.encode() in seen[1]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (httpx.Response(404, json={"error": {"code": "model_not_found"}}), ASRModelUnavailable),
        (httpx.Response(413, json={}), ASRChunkTooLarge),
        (httpx.ConnectError("private detail"), ASROffline),
        (httpx.ConnectTimeout("private detail"), ASROffline),
        (httpx.ReadTimeout("private detail"), ASRUnknownOutcome),
    ],
)
async def test_adapter_classifies_missing_model_size_and_offline(reply, error):
    def replay(request):
        if isinstance(reply, Exception):
            raise type(reply)(str(reply), request=request)
        return reply

    adapter = GroqAdapter(
        api_key="synthetic",
        model=PRIMARY,
        max_audio_bytes=100,
        transport=httpx.MockTransport(replay),
    )
    with pytest.raises(error) as failure:
        await adapter.transcribe(b"audio", duration_ms=1000)
    assert type(failure.value) is error
    assert "private" not in str(failure.value)


async def test_adapter_refuses_missing_and_over_cap_audio_without_a_request():
    adapter = GroqAdapter(
        api_key="synthetic",
        model=PRIMARY,
        max_audio_bytes=4,
        transport=httpx.MockTransport(lambda _: pytest.fail("No request may be sent")),
    )
    with pytest.raises(ASRMissingAudio):
        await adapter.transcribe(b"", duration_ms=1000)
    with pytest.raises(ASRMissingAudio):
        await adapter.transcribe(b"aud", duration_ms=0)
    with pytest.raises(ASRChunkTooLarge):
        await adapter.transcribe(b"audio", duration_ms=1000)


class Ledger:
    """In-memory stand-in for ``asr_requests`` at the job's current retry index."""

    def __init__(self, rows=None, quota=(PRIMARY, FALLBACK)):
        self.rows = dict(rows or {})
        self.quota = set(quota)
        self.reserved = []

    async def attempts(self, job, context):
        return {
            model: SimpleNamespace(model=model, outcome=outcome, result=result)
            for model, (outcome, result) in self.rows.items()
        }

    async def begin(self, job, context, settings, model, seconds, clock=None):
        if self.rows.get(model, ("", None))[0] == "reserved":
            self.rows[model] = ("uncertain", None)
            return model
        if model not in self.quota:
            raise ASRQuotaExhausted
        self.quota.discard(model)
        self.reserved.append(model)
        self.rows[model] = ("uncertain", None)
        return model

    async def complete(self, job, context, request_id, result, *, error=None):
        self.rows[request_id] = (type(error).__name__ if error else "completed", result)


class Provider:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def transcribe(self, audio, *, duration_ms, offset_ms=0, model=None):
        self.calls.append(model)
        answer = self.answers[model]
        if isinstance(answer, Exception):
            raise answer
        return answer


class Context:
    def __init__(self):
        self.points = []

    async def checkpoint(self, name, job):
        self.points.append(name)

    async def heartbeat(self):
        return None


async def _passthrough(context, call):
    return await call


async def run(monkeypatch, ledger, provider, *, load=None):
    monkeypatch.setattr(fallback, "attempts", ledger.attempts)
    monkeypatch.setattr(fallback, "begin", ledger.begin)
    monkeypatch.setattr(fallback, "complete", ledger.complete)
    monkeypatch.setattr(fallback, "_heartbeat_while", _passthrough)

    async def audio():
        return SpeechAudio(b"audio", 1000, 1.0, "a" * 64)

    def build(model, segments, speech):
        return {"model": model, "reason": None if segments else "no_speech", "n": len(segments)}

    job = SimpleNamespace(id=uuid.uuid4())
    settings = Settings(database_url=URL, _env_file=None)
    return await transcribe(
        job, Context(), settings, provider, load=load or audio, offset_ms=0, build=build
    )


@pytest.mark.parametrize(
    "primary_failure",
    [
        ASRQuotaExhausted(),
        ASRUnknownOutcome(),
        ASROffline(),
        ASRUnavailable(),
        ASRModelUnavailable(),
    ],
)
async def test_primary_failure_falls_back_once_and_records_both(monkeypatch, primary_failure):
    ledger = Ledger()
    provider = Provider({PRIMARY: primary_failure, FALLBACK: [SEGMENT]})
    result = await run(monkeypatch, ledger, provider)
    assert result == {"model": FALLBACK, "reason": None, "n": 1}
    assert provider.calls == [PRIMARY, FALLBACK]
    assert ledger.rows[PRIMARY][0] == type(primary_failure).__name__
    assert ledger.rows[FALLBACK][0] == "completed"


async def test_both_models_failing_is_unavailable_with_the_last_reason(monkeypatch):
    provider = Provider({PRIMARY: ASRQuotaExhausted(), FALLBACK: ASROffline()})
    with pytest.raises(ASROffline):
        await run(monkeypatch, Ledger(), provider)
    assert provider.calls == [PRIMARY, FALLBACK]


async def test_quota_on_both_models_is_quota_exhausted_without_any_request(monkeypatch):
    provider = Provider({})
    with pytest.raises(ASRQuotaExhausted):
        await run(monkeypatch, Ledger(quota=()), provider)
    assert provider.calls == []


async def test_full_primary_window_goes_straight_to_the_fallback(monkeypatch):
    ledger = Ledger(quota=(FALLBACK,))
    provider = Provider({FALLBACK: []})
    assert await run(monkeypatch, ledger, provider) == {
        "model": FALLBACK,
        "reason": "no_speech",
        "n": 0,
    }
    assert provider.calls == [FALLBACK]


async def test_unknown_primary_outcome_is_never_resent_and_tries_the_fallback(monkeypatch):
    ledger = Ledger({PRIMARY: ("uncertain", None)})
    provider = Provider({PRIMARY: [SEGMENT], FALLBACK: [SEGMENT]})
    assert (await run(monkeypatch, ledger, provider))["model"] == FALLBACK
    assert provider.calls == [FALLBACK]
    assert ledger.rows[PRIMARY][0] == "uncertain"


async def test_unknown_outcomes_on_both_models_never_call_again(monkeypatch):
    ledger = Ledger({PRIMARY: ("ASRUnknownOutcome", None), FALLBACK: ("uncertain", None)})
    provider = Provider({})
    with pytest.raises(ASRUnknownOutcome):
        await run(monkeypatch, ledger, provider)
    assert provider.calls == []


async def test_stored_success_is_reused_without_reading_audio_or_calling(monkeypatch):
    stored = {"model": FALLBACK, "reason": None, "n": 1}
    ledger = Ledger({PRIMARY: ("ASRQuotaExhausted", None), FALLBACK: ("completed", stored)})

    async def unreadable():
        pytest.fail("Stored speech must not reread audio")

    assert await run(monkeypatch, ledger, Provider({}), load=unreadable) == stored


async def test_owner_retry_reserved_only_the_fallback(monkeypatch):
    ledger = Ledger({FALLBACK: ("reserved", None)}, quota=())
    provider = Provider({FALLBACK: [SEGMENT]})
    assert (await run(monkeypatch, ledger, provider))["model"] == FALLBACK
    assert provider.calls == [FALLBACK]
    assert ledger.reserved == []


@pytest.mark.parametrize("error", [ASRMissingAudio, ASRChunkTooLarge])
async def test_audio_problems_are_unavailable_before_any_reservation(monkeypatch, error):
    ledger = Ledger()

    async def refused():
        raise error

    with pytest.raises(error):
        await run(monkeypatch, ledger, Provider({}), load=refused)
    assert ledger.reserved == []
