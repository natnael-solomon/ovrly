"""Invented HTTP responses only; no private recordings or live provider calls."""

import httpx
import pytest

from services.asr.groq import (
    ASRInvalidResponse,
    ASRQuotaExhausted,
    ASRUnavailable,
    ASRUnknownOutcome,
    GroqAdapter,
)
from services.jobs.retries import RateLimited, Transient


async def test_groq_transcription_preserves_timed_speech_on_original_timebase():
    def replay(request):
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        assert request.url == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert b'name="model"\r\n\r\nsynthetic-model' in request.content
        assert b"verbose_json" in request.content
        return httpx.Response(
            200,
            json={
                "text": "The invented bell rings.",
                "segments": [{"start": 0.125, "end": 0.875, "text": "The invented bell rings."}],
            },
        )

    adapter = GroqAdapter(
        api_key="synthetic-key",
        model="synthetic-model",
        max_audio_bytes=100,
        transport=httpx.MockTransport(replay),
    )
    result = await adapter.transcribe(b"synthetic-audio", duration_ms=1000, offset_ms=4000)
    assert result == [{"start_ms": 4125, "end_ms": 4875, "text": "The invented bell rings."}]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"text": "Unplaced speech", "segments": []},
        {"text": "", "segments": "wrong"},
        {"text": "x", "segments": [{"start": -1, "end": 0.8, "text": "x"}]},
        {"text": "x", "segments": [{"start": 0, "end": 1.001, "text": "x"}]},
        {"text": "x", "segments": [{"start": True, "end": 1, "text": "x"}]},
        {"text": "x", "segments": [{"start": "0", "end": 1, "text": "x"}]},
        {"text": "x", "segments": [{"start": 0.9, "end": 0.5, "text": "x"}]},
        {"text": "x", "segments": [{"start": 0.1001, "end": 0.1002, "text": "x"}]},
        {"text": "x", "segments": [{"start": 0, "end": 1, "text": ""}]},
        {"text": "x", "segments": [None]},
        {"text": "x", "segments": [{"start": 0, "end": 10**400, "text": "x"}]},
    ],
)
async def test_malformed_speech_never_becomes_success(body):
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    )
    with pytest.raises(ASRInvalidResponse):
        await adapter.transcribe(b"audio", duration_ms=1000)


@pytest.mark.parametrize(
    ("status", "body", "headers", "error"),
    [
        (429, {"error": {"code": "insufficient_quota"}}, {}, ASRQuotaExhausted),
        (429, {"error": {"code": []}}, {}, ASRQuotaExhausted),
        (429, {"error": {"code": {}}}, {}, ASRQuotaExhausted),
        (429, {"error": {"code": "rate_limit_exceeded"}}, {"Retry-After": "2"}, RateLimited),
        (429, {}, {}, ASRQuotaExhausted),
        (429, {}, {"Retry-After": "3600"}, ASRQuotaExhausted),
        (503, {}, {}, Transient),
        (408, {}, {}, ASRUnknownOutcome),
        (500, {}, {}, ASRUnknownOutcome),
        (502, {}, {}, ASRUnknownOutcome),
        (504, {}, {}, ASRUnknownOutcome),
        (401, {"error": "private secret"}, {}, ASRUnavailable),
        (400, {}, {}, ASRUnavailable),
        (302, {}, {"Location": "https://invalid.example"}, ASRUnavailable),
        (200, None, {}, ASRInvalidResponse),
    ],
)
async def test_provider_failures_are_safe_bounded_and_explicit(status, body, headers, error):
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body, headers=headers)),
    )
    with pytest.raises(error) as failure:
        await adapter.transcribe(b"audio", duration_ms=1000)
    assert "private" not in str(failure.value)
    if error is RateLimited:
        assert failure.value.retry_after_seconds == 2


async def test_transport_timeout_is_unknown_not_retryable_submission():
    def replay(request):
        raise httpx.ReadTimeout("private provider data", request=request)

    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(replay),
    )
    with pytest.raises(ASRUnknownOutcome):
        await adapter.transcribe(b"audio", duration_ms=1000)


async def test_audio_and_response_size_limits_are_enforced_at_http_boundary():
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=5,
        max_response_bytes=20,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 21)),
    )
    with pytest.raises(ASRUnavailable):
        await adapter.transcribe(b"too much audio", duration_ms=1000)
    with pytest.raises(ASRInvalidResponse):
        await adapter.transcribe(b"audio", duration_ms=1000)


async def test_empty_speech_is_a_successful_empty_result():
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"text": "", "segments": []})
        ),
    )
    assert await adapter.transcribe(b"audio", duration_ms=1000) == []


@pytest.mark.parametrize(
    "raw",
    [
        b'{"text":"x","segments":[{"start":NaN,"end":1,"text":"x"}]}',
        b'{"text":"x","segments":[{"start":0,"end":Infinity,"text":"x"}]}',
        b"\xff",
        b"not json",
    ],
)
async def test_nonfinite_or_non_json_output_is_invalid(raw):
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=raw)),
    )
    with pytest.raises(ASRInvalidResponse):
        await adapter.transcribe(b"audio", duration_ms=1000)


@pytest.mark.parametrize(
    "hint", ["NaN", "Infinity", "-1", "invalid", "Wed, 01 Jan 2020 00:00:00 GMT"]
)
async def test_unusable_retry_after_is_terminal_not_unbounded(hint):
    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(429, json={}, headers={"Retry-After": hint})
        ),
    )
    with pytest.raises(ASRQuotaExhausted):
        await adapter.transcribe(b"audio", duration_ms=1000)


async def test_compressed_provider_output_is_rejected_without_decompression():
    class Compressed(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("Compressed response must not be consumed")
            yield b""  # pragma: no cover

    adapter = GroqAdapter(
        api_key="synthetic",
        model="synthetic",
        max_audio_bytes=100,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=Compressed())
        ),
    )
    with pytest.raises(ASRInvalidResponse):
        await adapter.transcribe(b"audio", duration_ms=1000)
