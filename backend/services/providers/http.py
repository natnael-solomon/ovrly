"""Shared request plumbing: correlation ids, bounded bodies and content-free logging."""

import logging
import uuid
from typing import Any, Final

import httpx

logger = logging.getLogger(__name__)

MAX_BODY_BYTES: Final = 4 * 1024 * 1024
USER_AGENT: Final = "ovrly-evidence/1.0 (+https://github.com/natnael-solomon/ovrly)"


class ProviderError(Exception):
    """A provider call that failed for this item only (5xx, timeout, malformed body)."""


class ProviderRejected(Exception):
    """The provider refused the credentials or the plan (401/403): a configuration fault."""


async def send(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    params: dict[str, str | int] | None = None,
) -> tuple[httpx.Response, bytes]:
    """Send one request with a fresh correlation id and a bounded body read.

    Logs the provider code, the correlation id and the status only: no URL, query, body or
    credential. Transport errors and oversized bodies raise :class:`ProviderError`.
    """
    request_id = uuid.uuid4()
    request_headers = {"User-Agent": USER_AGENT, "X-Request-Id": str(request_id)}
    request_headers.update(headers or {})
    try:
        async with client.stream(
            method, url, headers=request_headers, json=json, params=params
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    logger.warning("Provider %s request %s body too large", provider, request_id)
                    raise ProviderError(f"{provider} response too large")
    except httpx.HTTPError:
        logger.warning("Provider %s request %s failed in transport", provider, request_id)
        raise ProviderError(f"{provider} transport error") from None
    logger.info("Provider %s request %s returned %d", provider, request_id, response.status_code)
    return response, bytes(body)


def retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None
