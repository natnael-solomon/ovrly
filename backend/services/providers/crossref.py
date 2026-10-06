"""Crossref retraction and correction lookup (BE-09, #27).

Crossref records editorial updates on the notice that announces them, so the works that
update a DOI are listed with ``filter=updates:{doi}``. Anything Crossref cannot answer
(no DOI, an arXiv DataCite DOI, an error) is ``unknown``, never ``none``.
"""

import json
import re
from dataclasses import dataclass
from typing import Final, Literal

import httpx

from services.providers.http import ProviderError, send

PROVIDER: Final = "crossref"
API: Final = "https://api.crossref.org/v1/works"
RetractionStatus = Literal["none", "corrected", "retracted", "withdrawn", "unknown"]
_ARXIV_DOI = re.compile(r"^10\.48550/", re.IGNORECASE)
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_RETRACTED = {"retraction", "partial_retraction"}
_WITHDRAWN = {"withdrawal", "removal"}
_CORRECTED = {"correction", "erratum", "corrigendum", "addendum"}


def lookupable(doi: str | None) -> bool:
    return bool(doi and _DOI.match(doi) and not _ARXIV_DOI.match(doi))


@dataclass(frozen=True)
class CrossrefClient:
    client: httpx.AsyncClient
    mailto: str = ""

    async def status(self, doi: str | None) -> RetractionStatus:
        if not doi or not lookupable(doi):
            return "unknown"
        params: dict[str, str | int] = {"filter": f"updates:{doi}", "rows": 20}
        if self.mailto:
            params["mailto"] = self.mailto
        try:
            response, raw = await send(self.client, PROVIDER, "GET", API, params=params)
        except ProviderError:
            return "unknown"
        if response.status_code != 200:
            return "unknown"
        try:
            items = json.loads(raw)["message"]["items"]
        except (ValueError, KeyError, TypeError):
            return "unknown"
        kinds: set[str] = set()
        for item in items if isinstance(items, list) else []:
            for update in item.get("update-to", []) if isinstance(item, dict) else []:
                if (
                    isinstance(update, dict)
                    and str(update.get("DOI", "")).lower() == doi.lower()
                    and isinstance(update.get("type"), str)
                ):
                    kinds.add(update["type"].lower())
        if kinds & _RETRACTED:
            return "retracted"
        if kinds & _WITHDRAWN:
            return "withdrawn"
        if kinds & _CORRECTED:
            return "corrected"
        # An expression of concern or an unfamiliar update type is not a clean record.
        return "none" if not kinds else "unknown"
