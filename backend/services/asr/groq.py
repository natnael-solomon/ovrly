"""Bounded Groq HTTP boundary through the SSRF guard; tests may supply a replay transport."""

import asyncio
import json
import math
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx

from services.jobs.retries import RateLimited, Transient
from services.providers.egress import GuardedTransport, UnsafeUrl

# Whisper timestamps are quantised (20 ms) and the final segment often ends at or just past
# the audio length, which callers round down. Small overruns are clamped, not rejected.
OVERRUN_TOLERANCE_MS = 1000


def transcription_parameters(model: str) -> dict[str, str]:
    return {
        "model": model,
        "response_format": "verbose_json",
        "language": "en",
        "temperature": "0",
    }


class ASRUnavailable(Exception):
    pass


class ASRInvalidResponse(ASRUnavailable):
    pass


class ASRUnknownOutcome(ASRUnavailable):
    """No provider reconciliation exists: terminal, never a blind re-submission."""


class ASRQuotaExhausted(ASRUnavailable):
    pass


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _segments(data: Any, duration_ms: int, offset_ms: int) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise ASRInvalidResponse
    segments = data.get("segments")
    if not isinstance(segments, list) or (not segments and data["text"].strip()):
        raise ASRInvalidResponse
    result = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ASRInvalidResponse
        start, end, text = segment.get("start"), segment.get("end"), segment.get("text")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or not 0 <= start < end <= (duration_ms + OVERRUN_TOLERANCE_MS) / 1000
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not isinstance(text, str)
            or not text.strip()
        ):
            raise ASRInvalidResponse
        start_ms, end_ms = round(start * 1000), round(end * 1000)
        if start_ms >= end_ms:
            raise ASRInvalidResponse
        end_ms = min(end_ms, duration_ms)
        start_ms = min(start_ms, end_ms - 1)
        result.append(
            {"start_ms": offset_ms + start_ms, "end_ms": offset_ms + end_ms, "text": text}
        )
    return result


class ASRAdapter(Protocol):
    async def transcribe(
        self, audio: bytes, *, duration_ms: int, offset_ms: int = 0
    ) -> list[dict[str, Any]]: ...


class GroqAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        max_audio_bytes: int,
        max_response_bytes: int = 1048576,
        timeout_seconds: float = 120,
        max_retry_after_seconds: float = 60,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.max_audio_bytes = max_audio_bytes
        self.max_response_bytes = max_response_bytes
        self.timeout_seconds = timeout_seconds
        self.max_retry_after_seconds = max_retry_after_seconds
        self.transport = transport

    async def transcribe(
        self, audio: bytes, *, duration_ms: int, offset_ms: int = 0
    ) -> list[dict[str, Any]]:
        if not audio or len(audio) > self.max_audio_bytes or duration_ms <= 0 or offset_ms < 0:
            raise ASRUnavailable
        try:
            async with (
                asyncio.timeout(self.timeout_seconds),
                httpx.AsyncClient(
                    transport=self.transport if self.transport is not None else GuardedTransport(),
                    trust_env=False,
                    timeout=self.timeout_seconds,
                ) as client,
                client.stream(
                    "POST",
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Accept-Encoding": "identity",
                    },
                    data=transcription_parameters(self.model),
                    files={"file": ("audio.wav", audio, "audio/wav")},
                ) as response,
            ):
                # Do not decompress untrusted compressed bodies into unbounded allocations.
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ASRInvalidResponse
                raw = bytearray()
                async for block in response.aiter_bytes(chunk_size=65536):
                    if len(raw) + len(block) > self.max_response_bytes:
                        raise ASRInvalidResponse
                    raw.extend(block)
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    data = None
                if response.status_code == 429:
                    code = (
                        data.get("error", {}).get("code")
                        if isinstance(data, dict) and isinstance(data.get("error"), dict)
                        else None
                    )
                    delay = _retry_after(response.headers.get("retry-after"))
                    if (
                        code in ("insufficient_quota", "quota_exceeded", "quota_exhausted")
                        or delay is None
                        or delay > self.max_retry_after_seconds
                    ):
                        raise ASRQuotaExhausted
                    raise RateLimited(delay)
                if response.status_code == 503:
                    raise Transient
                if response.status_code == 408 or 500 <= response.status_code <= 599:
                    raise ASRUnknownOutcome
                if response.status_code != 200:
                    raise ASRUnavailable
                return _segments(data, duration_ms, offset_ms)
        except UnsafeUrl:
            # Refused before connecting, so nothing reached the provider.
            raise ASRUnavailable from None
        except (httpx.HTTPError, TimeoutError):
            raise ASRUnknownOutcome from None
