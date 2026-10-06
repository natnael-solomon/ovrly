"""Scholarxiv Router chat completions, as used by the evidence stages (BE-09, #27).

Only documented request fields are sent (``model``, ``messages``, ``models``,
``max_tokens``, ``temperature``); ``response_format`` and ``tools`` are not relied on, so
JSON is enforced here: the reply is cleaned (a leading ``<think>`` block, code fences and
any preamble before the first ``{`` are removed), validated against a Pydantic model and
repaired at most once. A discarded reply is reported to the router as ``regenerated``.
Provider content is treated as data: the system prompt says so and no tools are offered.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Final, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from services.jobs.retries import RateLimited
from services.providers.budget import TokenBucket
from services.providers.http import (
    RATE_LIMIT_ATTEMPTS,
    ProviderError,
    ProviderRejected,
    retry_after,
    send,
)

PROVIDER: Final = "scholarxiv_router"
COMPLETIONS: Final = "/api/v1/router/chat/completions"
FEEDBACK: Final = "/api/v1/router/feedback"
_THINK = re.compile(r"^\s*<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)

Model = TypeVar("Model", bound=BaseModel)
logger = logging.getLogger(__name__)


class InvalidReply(Exception):
    """The reply stayed invalid after the single repair attempt."""


@dataclass
class CallBudget:
    """Remaining router calls for one claim; repairs count too."""

    remaining: int

    def take(self) -> None:
        if self.remaining <= 0:
            raise BudgetExhausted("router call budget exhausted")
        self.remaining -= 1


class BudgetExhausted(Exception):
    pass


def clean_reply(content: str) -> tuple[str, dict[str, bool]]:
    """Strip reasoning, fences and preamble; report which hygiene steps fired."""
    flags = {"thinking_leaked": False, "fenced": False}
    text = content
    stripped = _THINK.sub("", text, count=1)
    if stripped != text:
        flags["thinking_leaked"] = True
        text = stripped
    if "```" in text:
        flags["fenced"] = True
        text = _FENCE.sub("", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        return "", flags
    return text[start : end + 1], flags


@dataclass(frozen=True)
class RouterClient:
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    bucket: TokenBucket
    max_tokens: int = 1200

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _complete(self, route: str, messages: list[dict[str, str]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": route if route.startswith("auto:") else "auto:cheap",
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if not route.startswith("auto:"):
            body["models"] = [route]
        for attempt in range(RATE_LIMIT_ATTEMPTS):
            async with self.bucket.request():
                response, raw = await send(
                    self.client,
                    PROVIDER,
                    "POST",
                    self._url(COMPLETIONS),
                    headers=self._headers(),
                    json=body,
                )
            if response.status_code != 429:
                break
            # Hold every worker for the provider's Retry-After, then try again.
            await self.bucket.block(retry_after(response))
            if attempt == RATE_LIMIT_ATTEMPTS - 1:
                raise RateLimited(retry_after(response))
        if response.status_code in {401, 403}:
            raise ProviderRejected(f"Router refused the request ({response.status_code})")
        if response.status_code != 200:
            raise ProviderError(f"Router returned {response.status_code}")
        try:
            payload = json.loads(raw)
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("Router returned a malformed completion") from None
        if not isinstance(content, str):
            raise ProviderError("Router completion has no text content")
        return {
            "content": content,
            "decision_id": payload.get("decision_id"),
            "model": payload.get("model"),
            "finish_reason": choice.get("finish_reason"),
        }

    async def _feedback(self, decision_id: Any) -> None:
        if not isinstance(decision_id, str) or not decision_id:
            return
        try:
            async with self.bucket.request():
                await send(
                    self.client,
                    PROVIDER,
                    "POST",
                    self._url(FEEDBACK),
                    headers=self._headers(),
                    json={"decision_id": decision_id, "feedback": "regenerated"},
                )
        except (ProviderError, RateLimited):
            # Feedback is best effort; it never changes the outcome of the call.
            return

    async def structured(
        self,
        route: str,
        system: str,
        user: str,
        model: type[Model],
        budget: CallBudget,
        check: Any = None,
    ) -> Model:
        """One JSON reply validated as ``model`` (and by ``check``), with one repair."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(2):
            budget.take()
            completion = await self._complete(route, messages)
            cleaned, flags = clean_reply(completion["content"])
            if flags["thinking_leaked"] or flags["fenced"]:
                logger.info("Router reply needed hygiene (%d attempt)", attempt + 1)
            problem: str | None = None
            try:
                parsed = model.model_validate_json(cleaned)
                if check is not None:
                    problem = check(parsed)
                if problem is None:
                    return parsed
            except ValidationError as exc:
                problem = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['type']}" for e in exc.errors()
                )[:500]
            await self._feedback(completion["decision_id"])
            messages = [
                *messages,
                {"role": "assistant", "content": completion["content"][:4000]},
                {
                    "role": "user",
                    "content": "Repair the previous reply once. Return only one JSON object "
                    "matching the requested shape, using only the supplied identifiers. "
                    f"Problems: {problem}",
                },
            ]
        raise InvalidReply("router reply invalid after one repair")
