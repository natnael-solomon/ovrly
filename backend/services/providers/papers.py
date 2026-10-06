"""Scholarxiv Papers search (BE-09, #27).

The Free plan serves the single-source arXiv index through ``POST /api/v1/papers/search``.
Federated search (Go plan and above) is opt-in; a Free key gets 403 and the client falls back
to single-source search, recording that the federated sources were not consulted. A
federated source that reports ``{count: 0, hasMore: false}`` may have failed silently (the
provider documents this), so it is recorded as unknown, never as evidence of absence.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from services.jobs.retries import RateLimited
from services.providers.budget import TokenBucket
from services.providers.http import ProviderError, ProviderRejected, retry_after, send

PROVIDER: Final = "scholarxiv_papers"
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)
_VERSION = re.compile(r"v\d+$")


@dataclass(frozen=True)
class Paper:
    """One search hit, normalized across the single-source and federated shapes."""

    title: str
    summary: str
    authors: tuple[str, ...]
    published: str | None
    doi: str | None
    arxiv_id: str | None
    url: str | None
    journal_ref: str | None
    comment: str | None
    sources: tuple[str, ...]

    @property
    def origin(self) -> str:
        """Identity of the underlying work: a journal DOI, else the arXiv id without version.

        Two hits with one origin are one source, whichever query or version found them.
        """
        if self.doi and not _ARXIV_DOI.match(self.doi):
            return f"doi:{self.doi.lower()}"
        arxiv_doi = _ARXIV_DOI.match(self.doi) if self.doi else None
        arxiv_id = self.arxiv_id or (arxiv_doi.group(1) if arxiv_doi else None)
        if arxiv_id:
            return f"arxiv:{_VERSION.sub('', arxiv_id).lower()}"
        return f"title:{' '.join(self.title.lower().split())}"


@dataclass
class SearchResult:
    papers: list[Paper] = field(default_factory=list)
    # Federated sources that reported nothing and may have failed: unknown, not empty.
    unknown_sources: list[str] = field(default_factory=list)
    federated_unavailable: bool = False


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


def parse_paper(item: Any, fallback_source: str) -> Paper | None:
    if not isinstance(item, dict):
        return None
    title = _text(item.get("title"))
    if title is None:
        return None
    authors = item.get("authors")
    arxiv_id = _text(item.get("extractedID"))
    if arxiv_id is None and isinstance(item.get("id"), str) and "arxiv.org/abs/" in item["id"]:
        arxiv_id = item["id"].rsplit("/abs/", 1)[1]
    published = _text(item.get("published"))
    if published is None and isinstance(item.get("year"), int):
        published = f"{item['year']:04d}-01-01T00:00:00Z"
    sources = item.get("sources")
    return Paper(
        title=title,
        summary=_text(item.get("summary")) or "",
        authors=tuple(a for a in authors if isinstance(a, str))
        if isinstance(authors, list)
        else (),
        published=published,
        doi=_text(item.get("doi")),
        arxiv_id=arxiv_id,
        url=_text(item.get("url")) or _text(item.get("id")) or _text(item.get("pdfLink")),
        journal_ref=_text(item.get("journalRef")),
        comment=_text(item.get("comment")),
        sources=tuple(s for s in sources if isinstance(s, str))
        if isinstance(sources, list)
        else (_text(item.get("source")) or fallback_source,),
    )


@dataclass(frozen=True)
class PapersClient:
    client: httpx.AsyncClient
    base_url: str
    api_key: str
    bucket: TokenBucket
    federated: bool = False

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _post(self, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        await self.bucket.acquire()
        response, raw = await send(
            self.client,
            PROVIDER,
            "POST",
            f"{self.base_url.rstrip('/')}{path}",
            headers=self._headers(),
            json=body,
        )
        if response.status_code == 429:
            await self.bucket.drain()
            raise RateLimited(retry_after(response))
        if response.status_code == 401:
            raise ProviderRejected("Scholarxiv rejected the API key")
        if response.status_code >= 500:
            raise ProviderError(f"Scholarxiv returned {response.status_code}")
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            raise ProviderError("Scholarxiv returned a malformed body") from None
        return response.status_code, payload

    async def search(self, query: str, limit: int) -> SearchResult:
        """Search one query; a 4xx other than auth and rate limit is a failed query."""
        result = SearchResult()
        if self.federated:
            status, payload = await self._post(
                "/api/v1/papers/federated/search", {"q": query, "page": 0, "limit": limit}
            )
            if status == 200 and isinstance(payload, dict):
                result.papers = _papers(payload, "federated")
                by_source = payload.get("bySource")
                if isinstance(by_source, dict):
                    result.unknown_sources = sorted(
                        name
                        for name, stats in by_source.items()
                        if isinstance(stats, dict)
                        and stats.get("count") == 0
                        and stats.get("hasMore") is False
                    )
                return result
            if status != 403:
                raise ProviderError(f"Scholarxiv federated search returned {status}")
            result.federated_unavailable = True
        status, payload = await self._post(
            "/api/v1/papers/search",
            {"searchFilterString": {"all": query}, "page": 0, "limit": limit},
        )
        if status == 403:
            raise ProviderRejected("Scholarxiv refused the search for this plan")
        if status != 200 or not isinstance(payload, dict):
            raise ProviderError(f"Scholarxiv search returned {status}")
        result.papers = _papers(payload, "arxiv")
        return result


def _papers(payload: dict[str, Any], source: str) -> list[Paper]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderError("Scholarxiv search body has no data list")
    return [paper for item in data if (paper := parse_paper(item, source)) is not None]
