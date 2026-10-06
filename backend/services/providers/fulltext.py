"""Open-access full text from arXiv HTML and Europe PMC (BE-09, #27).

Both are parsed into plain paragraphs with the standard library's HTML parser, which does
not expand entities from a document type definition, so a hostile body cannot trigger an
entity-expansion attack. A missing, closed or unparseable full text is simply absent: the
caller then relies on the abstract and records ``abstract_only``.
"""

import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

import httpx

from services.providers.http import ProviderError, send

ARXIV: Final = "arxiv"
EUROPEPMC: Final = "europepmc"
ARXIV_HTML: Final = "https://arxiv.org/html/"
EUROPEPMC_SEARCH: Final = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPEPMC_REST: Final = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
_ARXIV_ID = re.compile(r"^[A-Za-z0-9.\-/]+$")
_PMCID = re.compile(r"^PMC\d+$")
MIN_PARAGRAPH: Final = 80


class _Paragraphs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._depth = 0
        self._skip = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "math", "table", "ref-list", "fig"}:
            self._skip += 1
        elif tag == "p":
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "math", "table", "ref-list", "fig"} and self._skip:
            self._skip -= 1
        elif tag == "p" and self._depth:
            self._depth -= 1
            if not self._depth:
                text = " ".join("".join(self._buffer).split())
                if len(text) >= MIN_PARAGRAPH:
                    self.paragraphs.append(text)
                self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._depth and not self._skip:
            self._buffer.append(data)


def paragraphs(markup: str) -> list[str]:
    parser = _Paragraphs()
    parser.feed(markup)
    parser.close()
    return parser.paragraphs


@dataclass(frozen=True)
class FullText:
    paragraphs: list[str]
    url: str


@dataclass(frozen=True)
class FullTextClient:
    client: httpx.AsyncClient

    async def arxiv(self, arxiv_id: str | None) -> FullText | None:
        if not arxiv_id or not _ARXIV_ID.match(arxiv_id):
            return None
        url = f"{ARXIV_HTML}{arxiv_id}"
        try:
            response, raw = await send(self.client, ARXIV, "GET", url)
        except ProviderError:
            return None
        if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
            return None
        found = paragraphs(raw.decode("utf-8", errors="replace"))
        return FullText(found, url) if found else None

    async def europepmc(self, doi: str | None) -> FullText | None:
        if not doi:
            return None
        params: dict[str, str | int] = {
            "query": f'DOI:"{doi}"',
            "format": "json",
            "resultType": "lite",
            "pageSize": 1,
        }
        try:
            response, raw = await send(
                self.client, EUROPEPMC, "GET", EUROPEPMC_SEARCH, params=params
            )
            if response.status_code != 200:
                return None
            results = json.loads(raw)["resultList"]["result"]
        except (ProviderError, ValueError, KeyError, TypeError):
            return None
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return None
        first = results[0]
        pmcid = first.get("pmcid")
        if (
            first.get("isOpenAccess") != "Y"
            or not isinstance(pmcid, str)
            or not _PMCID.match(pmcid)
        ):
            return None
        url = f"{EUROPEPMC_REST}{pmcid}/fullTextXML"
        try:
            response, raw = await send(self.client, EUROPEPMC, "GET", url)
        except ProviderError:
            return None
        if response.status_code != 200:
            return None
        found = paragraphs(raw.decode("utf-8", errors="replace"))
        return FullText(found, f"https://europepmc.org/article/PMC/{pmcid}") if found else None
