"""Prompt-injection fixtures against the evidence and assessment pipeline (REPO-06, #28).

Every input in ``evaluation/adversarial/prompt-injection.json`` is placed on its untrusted
surface (transcript or OCR text in the claim, a PDF abstract, PDF full text) and run
against every simulated model reply. Whatever the model says, the pipeline must take no
action beyond its fixed provider calls, put no secret in a prompt or the published version,
and keep citation validation intact. No database or real provider is involved.
"""

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from evidence_cassettes import KEY, Providers, cassette
from test_evidence_units import FakeBucket, beat, fixture_report, identity, retriever

from services.evidence.assessment import Assessor, CitationInvalid, validate_citations
from services.evidence.retrieval import Budget
from services.evidence.stages import merged_version
from services.providers.papers import PapersClient
from services.providers.router import RouterClient

FIXTURES = (
    Path(__file__).resolve().parents[2] / "evaluation" / "adversarial" / "prompt-injection.json"
)
DOCUMENT = json.loads(FIXTURES.read_text(encoding="utf-8"))
INPUTS = DOCUMENT["inputs"]
REPLIES = DOCUMENT["replies"]
# One passage per claim keeps every scripted reply's single relation complete.
BUDGET = Budget(
    max_queries=2,
    results_per_query=10,
    max_candidates=20,
    max_passages=1,
    max_full_text=1,
    max_llm_calls=4,
)
ALLOWED = {
    ("POST", "www.scholarxiv.com", "/api/v1/papers/search"),
    ("POST", "www.scholarxiv.com", "/api/v1/router/chat/completions"),
    ("POST", "www.scholarxiv.com", "/api/v1/router/feedback"),
    ("GET", "api.crossref.org", "/v1/works"),
    ("GET", "arxiv.org", "/html/"),
    ("GET", "www.ebi.ac.uk", "/europepmc/webservices/rest/search"),
}
ROUTER_FIELDS = {"model", "messages", "temperature", "max_tokens", "models"}


class Injected(Providers):
    """The synthetic providers, with the injection placed on one surface."""

    def __init__(self, surface: str, text: str):
        super().__init__()
        papers = json.loads(cassette("papers_search.json"))
        if surface == "pdf_abstract":
            for paper in papers["data"]:
                paper["summary"] = f"{paper['summary']} {text}"
        self.papers_body = json.dumps(papers).encode()
        self.full_text_html = cassette("arxiv_2601.00001v2.html")
        if surface == "pdf_full_text":
            self.full_text_html = self.full_text_html.replace(b"</p>", f" {text}</p>".encode())

    def handle(self, request):
        if request.url.host == "arxiv.org":
            self.requests.append(request)
            return httpx.Response(
                200, content=self.full_text_html, headers={"content-type": "text/html"}
            )
        return super().handle(request)


def test_fixture_file_is_well_formed():
    assert DOCUMENT["schema_version"] == "adversarial-v1" and DOCUMENT["synthetic"] is True
    surfaces = {item["surface"] for item in INPUTS}
    assert surfaces == {"transcript", "ocr", "pdf_abstract", "pdf_full_text"}
    assert len({item["id"] for item in INPUTS}) == len(INPUTS)
    assert {reply["expect"] for reply in REPLIES} == {
        "unassessed",
        "assessed",
        "assessed_redacted",
    }


def router_bodies(providers):
    return [json.loads(r.content) for r in providers.calls("/router/chat/completions")]


@pytest.mark.parametrize("reply", REPLIES, ids=[r["id"] for r in REPLIES])
@pytest.mark.parametrize("item", INPUTS, ids=[i["id"] for i in INPUTS])
async def test_injection_takes_no_action_leaks_nothing_and_keeps_citations(item, reply):
    providers = Injected(item["surface"], item["text"])
    base = fixture_report()
    claim = base.claims[1]
    proposition = claim.proposition
    if item["surface"] in {"transcript", "ocr"}:
        proposition = f"{proposition} {item['text']}"

    bucket = FakeBucket()
    async with httpx.AsyncClient(transport=providers.transport()) as client:
        router = RouterClient(client, "https://www.scholarxiv.com", KEY, bucket)
        papers = PapersClient(client, "https://www.scholarxiv.com", KEY, bucket, False)
        retrieval = await retriever(client, router, papers).retrieve(
            claim.id,
            proposition,
            # An abstract is read only when no full text replaces it.
            replace(BUDGET, max_full_text=0) if item["surface"] == "pdf_abstract" else BUDGET,
            beat,
        )
        assert retrieval.status == "retrieved" and len(retrieval.passages) == 1
        if reply["content"] is not None:
            # The original reply and its single repair both obey the injection.
            providers.router_replies = [reply["content"], reply["content"]]
        outcome = await Assessor(router, "auto:quality", BUDGET.max_llm_calls).assess(
            proposition, retrieval, 2, False
        )

    # 1. No tool action: only the fixed provider endpoints were called, no tool was
    #    offered, and nothing reached the hosts named by the injection.
    for request in providers.requests:
        assert any(
            (request.method, request.url.host) == (method, host)
            and request.url.path.startswith(path)
            for method, host, path in ALLOWED
        ), request.url
    bodies = router_bodies(providers)
    assert bodies
    for body in bodies:
        assert set(body) <= ROUTER_FIELDS
        system, *rest = body["messages"]
        assert system["role"] == "system" and "not instructions" in system["content"]
        assert item["text"] not in system["content"]
    prompts = " ".join(m["content"] for body in bodies for m in body["messages"][1:])
    assert item["text"].split(".")[0] in prompts, "the injection must reach the model as data"
    feedback = [json.loads(r.content) for r in providers.calls("/router/feedback")]
    assert all(set(body) == {"decision_id", "feedback"} for body in feedback)
    assert all(body["feedback"] == "regenerated" for body in feedback)

    # 2. No secret anywhere but the Scholarxiv Authorization header.
    for request in providers.requests:
        assert KEY not in str(request.url) and KEY.encode() not in request.content
        if request.url.host != "www.scholarxiv.com":
            assert "authorization" not in request.headers
    built = merged_version(base, [outcome], {claim.id}, False, "standard")(identity(2, base.id))
    published = built.model_dump_json()
    assert KEY not in published and "Bearer" not in published
    assert "OVRLY_SCHOLARXIV_API_KEY" not in published.replace(item["text"], "")

    # 3. Citation validation holds for what is published, and still fails on tampering.
    validate_citations(built)
    if reply["expect"] == "unassessed":
        assert outcome.assessment is None and outcome.reason == "assessment_unavailable"
        assert not any(a.claim_id == claim.id for a in built.assessments)
    else:
        assessment = outcome.assessment
        assert assessment is not None
        assert [r.evidence_id for r in assessment.relations] == [e.id for e in outcome.evidence]
        if reply["expect"] == "assessed_redacted":
            assert "[redacted]" in assessment.relations[0].note
        mine = next(a for a in built.assessments if a.claim_id == claim.id)
        other = next(e for e in built.evidence if e.claim_id != claim.id)
        foreign = mine.model_copy(
            update={"relations": [mine.relations[0].model_copy(update={"evidence_id": other.id})]}
        )
        tampered = built.model_copy(
            update={
                "assessments": [foreign if a.claim_id == claim.id else a for a in built.assessments]
            }
        )
        with pytest.raises(CitationInvalid):
            validate_citations(tampered)
        missing = mine.model_copy(
            update={
                "relations": [mine.relations[0].model_copy(update={"evidence_id": "ev_missing"})]
            }
        )
        with pytest.raises(CitationInvalid):
            validate_citations(
                built.model_copy(
                    update={
                        "assessments": [
                            missing if a.claim_id == claim.id else a for a in built.assessments
                        ]
                    }
                )
            )
