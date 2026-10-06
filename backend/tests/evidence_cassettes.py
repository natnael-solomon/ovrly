"""Synthetic provider cassettes for the evidence stages (BE-09, #27).

``Providers`` is an ``httpx.MockTransport`` handler that answers Scholarxiv Papers and
Router, Crossref, arXiv and Europe PMC from the committed synthetic files in
``tests/cassettes`` and records every request, so no test can reach a real provider. The
"router" is a deterministic stand-in: it writes queries and labels passages by title.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

CASSETTES = Path(__file__).resolve().parent / "cassettes"
BASE = "https://www.scholarxiv.com"
KEY = "sxv_synthetic-test-only"
_PASSAGE = re.compile(r"^\[(p\d+)\] \([^)]*\) ([^:]*):", re.MULTILINE)


def cassette(name: str) -> bytes:
    return (CASSETTES / name).read_bytes()


def completion(content: str, decision_id: str = "dec_synthetic_0001") -> dict[str, Any]:
    return {
        "id": "cmpl_synthetic",
        "model": "synthetic/router-stand-in",
        "decision_id": decision_id,
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def label_for(title: str) -> str:
    lowered = title.lower()
    if "electric bus adoption" in lowered:
        return "supports"
    if "diesel buses remain" in lowered:
        return "challenges"
    if "retracted" in lowered:
        return "supports"
    return "context"


@dataclass
class Providers:
    """Request handler; tweak the fields to script failures for one test."""

    requests: list[httpx.Request] = field(default_factory=list)
    papers_status: int = 200
    papers_body: bytes | None = None
    papers_failures: int = 0
    federated_status: int = 200
    router_status: int = 200
    router_replies: list[str] = field(default_factory=list)
    relation_override: str | None = None
    wrap_relations: bool = False
    crossref_status: int = 200
    arxiv_status: int = 200
    retry_after: str = "120"

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(path)]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "www.scholarxiv.com" and path == "/api/v1/papers/search":
            if self.papers_failures:
                self.papers_failures -= 1
                return httpx.Response(503, json={"error": "synthetic outage"})
            if self.papers_status != 200:
                headers = {"Retry-After": self.retry_after} if self.papers_status == 429 else {}
                return httpx.Response(self.papers_status, json={"error": "x"}, headers=headers)
            return httpx.Response(200, content=self.papers_body or cassette("papers_search.json"))
        if host == "www.scholarxiv.com" and path == "/api/v1/papers/federated/search":
            if self.federated_status != 200:
                return httpx.Response(self.federated_status, json={"error": "plan"})
            return httpx.Response(200, content=cassette("papers_federated.json"))
        if host == "www.scholarxiv.com" and path == "/api/v1/router/chat/completions":
            if self.router_status != 200:
                return httpx.Response(self.router_status, json={"error": "router"})
            return httpx.Response(200, json=completion(self.router_reply(request)))
        if host == "www.scholarxiv.com" and path == "/api/v1/router/feedback":
            return httpx.Response(200, json={"ok": True})
        if host == "api.crossref.org":
            if self.crossref_status != 200:
                return httpx.Response(self.crossref_status)
            doi = request.url.params.get("filter", "").removeprefix("updates:")
            if doi == "10.5555/synthetic.0003":
                return httpx.Response(200, content=cassette("crossref_retracted.json"))
            return httpx.Response(200, json={"status": "ok", "message": {"items": []}})
        if host == "arxiv.org" and path == "/html/2601.00001v2":
            if self.arxiv_status != 200:
                return httpx.Response(self.arxiv_status)
            return httpx.Response(
                200,
                content=cassette("arxiv_2601.00001v2.html"),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        if host == "www.ebi.ac.uk" and path.endswith("/rest/search"):
            if "10.5555/synthetic.0002" in request.url.params.get("query", ""):
                return httpx.Response(200, content=cassette("europepmc_search.json"))
            return httpx.Response(200, json={"resultList": {"result": []}})
        if host == "www.ebi.ac.uk" and path.endswith("/PMC0000002/fullTextXML"):
            return httpx.Response(200, content=cassette("europepmc_PMC0000002.xml"))
        return httpx.Response(404)

    def router_reply(self, request: httpx.Request) -> str:
        if self.router_replies:
            return self.router_replies.pop(0)
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        if "search queries" in system:
            return json.dumps(
                {
                    "queries": [
                        {"kind": "neutral", "text": "city bus fleet electric share survey"},
                        {"kind": "disconfirming", "text": "diesel buses remain city fleet"},
                    ]
                }
            )
        relations = [
            {
                "passage_id": passage_id,
                "relation": self.relation_override or label_for(title),
                "rationale": f"Synthetic reading of {title.strip()}.",
            }
            for passage_id, title in _PASSAGE.findall(user)
        ]
        reply = json.dumps({"relations": relations})
        if self.wrap_relations:
            return f"<think>private reasoning</think>Sure, here it is:\n```json\n{reply}\n```"
        return reply
