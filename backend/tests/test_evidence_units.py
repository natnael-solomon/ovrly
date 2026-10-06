"""Evidence stages without a database (BE-09, #27): providers against synthetic cassettes,
BM25, retrieval, assessment, the overall label and citation validation."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from evidence_cassettes import BASE, KEY, Providers
from test_reports_api import assert_component

from services.api.schemas import Claim, ReportVersion
from services.evidence import bm25
from services.evidence.assessment import (
    LABELS,
    Assessor,
    CitationInvalid,
    overall,
    validate_citations,
)
from services.evidence.retrieval import (
    Budget,
    ClaimRetrieval,
    Retriever,
    dedupe,
    evidence_id,
    fallback_query,
)
from services.evidence.stages import expanded_version, merged_version
from services.jobs.retries import RateLimited
from services.providers.crossref import CrossrefClient, lookupable
from services.providers.fulltext import FullTextClient, paragraphs
from services.providers.http import ProviderError, ProviderRejected
from services.providers.papers import PapersClient, parse_paper
from services.providers.router import (
    BudgetExhausted,
    CallBudget,
    InvalidReply,
    RouterClient,
    clean_reply,
)
from services.reports import NextVersion

RESULTS = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "fixtures" / "results"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
BUDGET = Budget(
    max_queries=3,
    results_per_query=10,
    max_candidates=20,
    max_passages=4,
    max_full_text=2,
    max_llm_calls=4,
)


class FakeBucket:
    def __init__(self, tokens=1000):
        self.tokens = tokens
        self.taken = 0
        self.drained = False

    async def acquire(self):
        if self.tokens <= 0:
            raise RateLimited(30)
        self.tokens -= 1
        self.taken += 1

    async def drain(self):
        self.tokens = 0
        self.drained = True


def fixture_report() -> ReportVersion:
    document = json.loads((RESULTS / "complete.json").read_text(encoding="utf-8"))
    return ReportVersion.model_validate(document["investigation"]["report"])


def claims_only() -> ReportVersion:
    report = fixture_report()
    return report.model_copy(update={"evidence": [], "assessments": [], "provisional": True})


def clients(providers: Providers, bucket=None, federated=False):
    client = httpx.AsyncClient(transport=providers.transport(), follow_redirects=False)
    bucket = bucket or FakeBucket()
    return (
        client,
        RouterClient(client, BASE, KEY, bucket),
        PapersClient(client, BASE, KEY, bucket, federated),
        bucket,
    )


async def beat():
    return None


def retriever(client, router, papers):
    return Retriever(
        papers=papers,
        router=router,
        crossref=CrossrefClient(client, "synthetic@example.invalid"),
        full_text=FullTextClient(client),
        query_route="auto:cheap",
        now=lambda: NOW,
    )


# ---- BM25 ------------------------------------------------------------------------------


def test_bm25_ranks_overlap_first_and_handles_edges():
    docs = [
        "protein folding kinetics",
        "electric buses in the city fleet",
        "city fleet electric buses electric share",
    ]
    ranked = bm25.rank("share of electric city buses", docs)
    assert [index for index, _ in ranked][:2] == [2, 1]
    assert ranked[-1] == (0, 0.0)
    assert bm25.scores("the of and", docs) == [0.0, 0.0, 0.0]
    assert bm25.scores("electric", []) == []
    assert bm25.tokens("The Buses, 2024!") == ["buses", "2024"]
    tie = bm25.rank("bus", ["bus", "bus"])
    assert [index for index, _ in tie] == [0, 1]


# ---- Router hygiene and structured replies ---------------------------------------------


def test_clean_reply_strips_thinking_fences_and_preamble():
    text, flags = clean_reply('<think>x</think>Here:\n```json\n{"a": 1}\n```\nThanks')
    assert json.loads(text) == {"a": 1}
    assert flags == {"thinking_leaked": True, "fenced": True}
    assert clean_reply("no json at all") == ("", {"thinking_leaked": False, "fenced": False})


async def test_router_sends_documented_fields_and_repairs_once():
    providers = Providers()
    providers.router_replies = ['{"relations": [{"passage_id": "p9"', '{"queries": []}']
    client, router, _, bucket = clients(providers)
    from services.evidence.retrieval import QueryPlan

    with pytest.raises(InvalidReply):
        await router.structured("auto:cheap", "sys", "user", QueryPlan, CallBudget(4))
    calls = providers.calls("/router/chat/completions")
    assert len(calls) == 2
    body = json.loads(calls[0].content)
    assert set(body) == {"model", "messages", "temperature", "max_tokens"}
    assert body["model"] == "auto:cheap" and body["temperature"] == 0
    assert calls[0].headers["authorization"] == f"Bearer {KEY}"
    assert "x-request-id" in calls[0].headers
    repair = json.loads(calls[1].content)["messages"]
    assert repair[-1]["role"] == "user" and repair[-1]["content"].startswith("Repair")
    # A discarded reply is reported to the router as regenerated.
    feedback = providers.calls("/router/feedback")
    assert [json.loads(r.content) for r in feedback][0] == {
        "decision_id": "dec_synthetic_0001",
        "feedback": "regenerated",
    }
    with pytest.raises(BudgetExhausted):
        await router.structured("auto:cheap", "sys", "user", QueryPlan, CallBudget(0))
    await client.aclose()
    assert bucket.taken >= 3


async def test_router_status_mapping():
    from services.evidence.retrieval import QueryPlan

    for status, error in (
        (429, RateLimited),
        (401, ProviderRejected),
        (403, ProviderRejected),
        (502, ProviderError),
    ):
        providers = Providers(router_status=status)
        client, router, _, bucket = clients(providers)
        with pytest.raises(error):
            await router.structured("auto:quality", "s", "u", QueryPlan, CallBudget(2))
        assert bucket.drained is (status == 429)
        await client.aclose()


async def test_router_pins_a_named_model_through_models():
    providers = Providers()
    providers.router_replies = ['{"queries": [{"kind": "neutral", "text": "abc def"}]}']
    client, router, _, _ = clients(providers)
    from services.evidence.retrieval import QueryPlan

    await router.structured("openai/gpt-oss-20b", "s", "u", QueryPlan, CallBudget(2))
    body = json.loads(providers.calls("/router/chat/completions")[0].content)
    assert body["model"] == "auto:cheap" and body["models"] == ["openai/gpt-oss-20b"]
    await client.aclose()


# ---- Papers ----------------------------------------------------------------------------


async def test_papers_search_parses_and_maps_failures():
    providers = Providers()
    client, _, papers, bucket = clients(providers)
    result = await papers.search("city buses", 10)
    assert len(result.papers) == 5 and not result.unknown_sources
    body = json.loads(providers.calls("/papers/search")[0].content)
    assert body == {"searchFilterString": {"all": "city buses"}, "page": 0, "limit": 10}
    assert bucket.taken == 1
    for status, error in (
        (503, ProviderError),
        (400, ProviderError),
        (401, ProviderRejected),
        (403, ProviderRejected),
        (429, RateLimited),
    ):
        providers.papers_status = status
        with pytest.raises(error) as raised:
            await papers.search("x", 5)
        if status == 429:
            assert raised.value.retry_after_seconds == 120 and bucket.drained
    bucket.tokens = 100
    providers.papers_status = 200
    providers.papers_body = b"not json"
    with pytest.raises(ProviderError):
        await papers.search("x", 5)
    providers.papers_body = b'{"pagination": {}}'
    with pytest.raises(ProviderError):
        await papers.search("x", 5)
    await client.aclose()


async def test_federated_zero_sources_are_unknown_and_free_plan_falls_back():
    providers = Providers()
    client, _, papers, _ = clients(providers, federated=True)
    result = await papers.search("city buses", 10)
    assert result.unknown_sources == ["crossref", "pubmed"]
    assert [p.origin for p in result.papers] == ["arxiv:2601.00001"]
    assert result.papers[0].sources == ("openalex", "semantic_scholar")
    providers.federated_status = 403
    result = await papers.search("city buses", 10)
    assert result.federated_unavailable and len(result.papers) == 5
    providers.federated_status = 500
    with pytest.raises(ProviderError):
        await papers.search("city buses", 10)
    await client.aclose()


def test_paper_origins_and_dedupe_keep_latest_version_and_query_kinds():
    data = json.loads((Path(__file__).parent / "cassettes" / "papers_search.json").read_text())
    papers = [parse_paper(item, "arxiv") for item in data["data"]]
    assert papers[0].origin == papers[1].origin == "arxiv:2601.00001"
    assert papers[2].origin == "doi:10.5555/synthetic.0002"
    merged = dedupe([("neutral", papers[1]), ("disconfirming", papers[0]), ("neutral", papers[2])])
    assert [(p.arxiv_id, kinds) for p, kinds in merged] == [
        ("2601.00001v2", ["neutral", "disconfirming"]),
        ("2601.00002v1", ["neutral"]),
    ]
    assert parse_paper({"title": "  "}, "arxiv") is None
    assert parse_paper("not a dict", "arxiv") is None
    bare = parse_paper({"title": "T", "id": "http://arxiv.org/abs/2501.1v3", "year": 2020}, "x")
    assert bare.arxiv_id == "2501.1v3" and bare.published == "2020-01-01T00:00:00Z"
    by_doi = parse_paper({"title": "T", "doi": "10.48550/arXiv.2501.00002v2"}, "x")
    assert by_doi.origin == "arxiv:2501.00002"
    assert parse_paper({"title": "A  B"}, "x").origin == "title:a b"


# ---- Crossref and full text ------------------------------------------------------------


async def test_crossref_status_mapping():
    providers = Providers()
    client = httpx.AsyncClient(transport=providers.transport())
    crossref = CrossrefClient(client, "synthetic@example.invalid")
    assert await crossref.status("10.5555/synthetic.0003") == "retracted"
    assert await crossref.status("10.5555/synthetic.0002") == "none"
    assert await crossref.status(None) == "unknown"
    assert await crossref.status("10.48550/arXiv.2601.00001") == "unknown"
    assert providers.calls("/v1/works")[0].url.params["mailto"] == "synthetic@example.invalid"
    providers.crossref_status = 500
    assert await crossref.status("10.5555/synthetic.0002") == "unknown"
    assert not lookupable("not-a-doi") and lookupable("10.1234/abc")

    def notices(*kinds):
        items = [{"update-to": [{"DOI": "10.1234/x", "type": kind}]} for kind in kinds]
        return httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"items": items}})
        )

    for kinds, expected in (
        (("withdrawal",), "withdrawn"),
        (("erratum",), "corrected"),
        (("expression_of_concern",), "unknown"),
        (("correction", "retraction"), "retracted"),
    ):
        async with httpx.AsyncClient(transport=notices(*kinds)) as other:
            assert await CrossrefClient(other).status("10.1234/x") == expected
    await client.aclose()


async def test_full_text_paragraphs_skip_scripts_tables_and_entities():
    found = paragraphs(
        (Path(__file__).parent / "cassettes" / "europepmc_PMC0000002.xml").read_text()
    )
    assert len(found) == 2 and "37 diesel buses" in found[0]
    assert "entity that must never be expanded" not in " ".join(found)
    html = paragraphs((Path(__file__).parent / "cassettes" / "arxiv_2601.00001v2.html").read_text())
    assert len(html) == 2 and "412 of 630" in html[0]
    providers = Providers()
    async with httpx.AsyncClient(transport=providers.transport()) as client:
        full = FullTextClient(client)
        assert (await full.arxiv("2601.00001v2")).url == "https://arxiv.org/html/2601.00001v2"
        assert await full.arxiv("2601.00009v1") is None
        assert await full.arxiv("bad id!") is None
        assert await full.arxiv(None) is None
        pmc = await full.europepmc("10.5555/synthetic.0002")
        assert pmc.url == "https://europepmc.org/article/PMC/PMC0000002"
        assert await full.europepmc("10.5555/none") is None
        assert await full.europepmc(None) is None


# ---- Retrieval -------------------------------------------------------------------------


async def test_retrieval_queries_ranks_fetches_and_records_honestly():
    providers = Providers()
    client, router, papers, _ = clients(providers)
    claim = fixture_report().claims[1]
    result = await retriever(client, router, papers).retrieve(
        claim.id, claim.proposition, BUDGET, beat
    )
    assert result.status == "retrieved" and result.query_source == "router"
    assert result.queries == 2 and result.failed_queries == 0 and result.llm_calls_used == 1
    titles = [p.source.title for p in result.passages]
    assert len(result.passages) == len({p.origin for p in result.passages}) <= BUDGET.max_passages
    assert not any("protein" in title.lower() for title in titles)
    by_origin = {p.origin: p for p in result.passages}
    diesel = by_origin["doi:10.5555/synthetic.0002"]
    assert diesel.inspection_level == "full_text" and "37 diesel buses" in diesel.excerpt
    assert diesel.source_type == "peer_reviewed" and diesel.retraction_status == "none"
    assert diesel.source.url == "https://doi.org/10.5555/synthetic.0002"
    survey = by_origin["arxiv:2601.00001"]
    assert survey.source.url == "https://arxiv.org/abs/2601.00001v2"
    assert survey.source_type == "preprint" and survey.retraction_status == "unknown"
    assert set(survey.found_by) == {"neutral", "disconfirming"}
    retracted = by_origin["doi:10.5555/synthetic.0003"]
    assert retracted.retraction_status == "retracted"
    assert all(len(p.excerpt or "") <= 1000 for p in result.passages)
    assert all(p.evidence_id == evidence_id(claim.id, p.origin) for p in result.passages)
    assert {p.retrieval_relevance for p in result.passages} <= {"high", "medium", "low"}
    assert result.passages[0].retrieval_relevance == "high"
    await client.aclose()


async def test_retrieval_falls_back_and_marks_total_failure_unassessed():
    providers = Providers(router_status=502)
    client, router, papers, _ = clients(providers)
    claim = fixture_report().claims[0]
    result = await retriever(client, router, papers).retrieve(
        claim.id, claim.proposition, BUDGET, beat
    )
    assert result.query_source == "fallback" and result.queries == 1
    sent = json.loads(providers.calls("/papers/search")[0].content)
    assert sent["searchFilterString"]["all"] == fallback_query(claim.proposition)
    providers.papers_failures = 99
    providers.router_status = 200
    failed = await retriever(client, router, papers).retrieve(
        claim.id, claim.proposition, BUDGET, beat
    )
    assert failed.status == "unassessed" and failed.reason == "retrieval_unavailable"
    assert failed.queries == failed.failed_queries == 2 and failed.passages == []
    providers.papers_failures = 1
    partial = await retriever(client, router, papers).retrieve(
        claim.id, claim.proposition, BUDGET, beat
    )
    assert partial.status == "retrieved" and partial.failed_queries == 1 and partial.passages
    await client.aclose()


async def test_query_plan_requires_a_neutral_query_and_respects_the_budget():
    providers = Providers()
    many = [{"kind": "disconfirming", "text": f"query number {i}"} for i in range(5)]
    providers.router_replies = [
        json.dumps({"queries": many}),
        json.dumps({"queries": [{"kind": "neutral", "text": "base query words"}, *many]}),
    ]
    client, router, papers, _ = clients(providers)
    small = Budget(2, 10, 20, 4, 0, 4)
    result = await retriever(client, router, papers).retrieve("c1", "electric buses", small, beat)
    assert result.queries == 2 and result.llm_calls_used == 2
    sent = [
        json.loads(r.content)["searchFilterString"]["all"]
        for r in providers.calls("/papers/search")
    ]
    assert sent == ["base query words", "query number 0"]
    await client.aclose()


def test_deeper_budget_doubles_within_hard_caps():
    deeper = Budget(3, 30, 60, 6, 3, 9).deeper()
    assert deeper == Budget(6, 50, 100, 10, 5, 10)


# ---- Assessment ------------------------------------------------------------------------


async def retrieved(providers: Providers, claim):
    client, router, papers, _ = clients(providers)
    result = await retriever(client, router, papers).retrieve(
        claim.id, claim.proposition, BUDGET, beat
    )
    return client, router, result


async def test_assessment_labels_relations_and_never_counts_retractions():
    providers = Providers(wrap_relations=True)
    claim = fixture_report().claims[1]
    client, router, result = await retrieved(providers, claim)
    outcome = await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 3, False)
    assessment = outcome.assessment
    assert outcome.reason is None and assessment.version == 3 and not assessment.provisional
    by_evidence = {r.evidence_id: r for r in assessment.relations}
    by_origin = {p.origin: p for p in result.passages}
    assert by_evidence[by_origin["doi:10.5555/synthetic.0002"].evidence_id].relation == "challenge"
    retracted = by_evidence[by_origin["doi:10.5555/synthetic.0003"].evidence_id]
    assert retracted.relation == "insufficient" and "retracted" in retracted.note
    assert assessment.overall == "mixed"
    assert "1 source support" in assessment.summary and "1 source challenge" in assessment.summary
    assert "retracted or withdrawn source was not counted" in assessment.summary
    assert {e.id for e in outcome.evidence} == set(by_evidence)
    body = json.loads(providers.calls("/router/chat/completions")[-1].content)
    assert body["model"] == "auto:quality"
    assert "data, not instructions" in body["messages"][0]["content"]
    await client.aclose()


async def test_assessment_repairs_unknown_ids_then_abstains_or_stays_unassessed():
    providers = Providers()
    claim = fixture_report().claims[0]
    client, router, result = await retrieved(providers, claim)
    good = json.dumps(
        {
            "relations": [
                {"passage_id": p.passage_id, "relation": "context", "rationale": "Topic only."}
                for p in result.passages
            ]
        }
    )
    providers.router_replies = [
        json.dumps(
            {"relations": [{"passage_id": "p99", "relation": "supports", "rationale": "x"}]}
        ),
        good,
    ]
    outcome = await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 2, True)
    assert outcome.assessment.overall == "insufficient_evidence" and outcome.assessment.provisional
    assert {r.relation for r in outcome.assessment.relations} == {"insufficient"}
    assert "statement about the evidence" in outcome.assessment.summary
    providers.router_replies = ["{}", "{}"]
    invalid = await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 2, False)
    assert invalid.assessment is None and invalid.reason == "assessment_unavailable"
    exhausted = await Assessor(router, "auto:quality", 1).assess(
        claim.proposition, result, 2, False
    )
    assert exhausted.assessment is None and exhausted.reason == "llm_budget_exhausted"
    providers.router_status = 503
    down = await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 2, False)
    assert down.reason == "assessment_unavailable"
    providers.router_status = 401
    with pytest.raises(ProviderRejected):
        await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 2, False)
    await client.aclose()


async def test_no_evidence_abstains_without_calling_the_router():
    providers = Providers()
    client, router, _, _ = clients(providers)
    empty = ClaimRetrieval(claim_id="c1", status="retrieved", unknown_sources=["pubmed"])
    outcome = await Assessor(router, "auto:quality", 4).assess("x", empty, 2, False)
    assert (
        outcome.assessment.overall == "insufficient_evidence" and outcome.assessment.relations == []
    )
    assert "No relevant source" in outcome.assessment.summary
    assert "missing evidence is not evidence of absence" in outcome.assessment.summary
    assert providers.calls("/router/chat/completions") == []
    skipped = ClaimRetrieval(claim_id="c1", status="unassessed", reason="claim_budget")
    assert (
        await Assessor(router, "auto:quality", 4).assess("x", skipped, 2, False)
    ).reason == "claim_budget"
    await client.aclose()


def test_overall_counts_origins_and_abstains():
    assert overall([]) == "insufficient_evidence"
    assert overall([("insufficient", "a"), ("insufficient", "b")]) == "insufficient_evidence"
    assert overall([("support", "a"), ("support", "a")]) == "supported"
    assert overall([("challenge", "a")]) == "challenged"
    assert overall([("qualify", "a"), ("support", "b")]) == "qualified"
    assert overall([("support", "a"), ("challenge", "b")]) == "mixed"
    assert overall([("qualify", "a"), ("challenge", "b")]) == "mixed"
    assert overall([("mixed", "a")]) == "mixed"
    assert set(LABELS.values()) <= {"support", "challenge", "qualify", "insufficient"}


# ---- Version building and citation validation ------------------------------------------


def identity(version: int, supersedes: str | None) -> NextVersion:
    return NextVersion(
        id=uuid.UUID("00000000-0000-4000-8000-0000000000aa"),
        investigation_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        version=version,
        supersedes=supersedes,
        created_at=NOW,
    )


async def test_merged_version_is_citation_valid_and_keeps_out_of_scope_claims():
    providers = Providers()
    base = fixture_report()
    claim = base.claims[1]
    client, router, result = await retrieved(providers, claim)
    outcome = await Assessor(router, "auto:quality", 4).assess(claim.proposition, result, 3, False)
    built = merged_version(base, [outcome], {claim.id}, False, "standard")(identity(3, base.id))
    assert built.version == 3 and built.supersedes == base.id and not built.provisional
    kept = next(a for a in built.assessments if a.claim_id == base.claims[0].id)
    assert (
        kept.version == 3
        and kept.relations
        == next(a for a in base.assessments if a.claim_id == base.claims[0].id).relations
    )
    assert {e.claim_id for e in built.evidence} == {c.id for c in base.claims}
    assert built.change_summary.startswith("Evidence check: 1 of 1 claims")
    assert_component(json.loads(built.model_dump_json()), "ReportVersion")
    fresh = claims_only()
    missed = merged_version(
        fresh,
        [outcome, type(outcome)(fresh.claims[0].id, None, [], "retrieval_unavailable")],
        {claim.id},
        False,
        "deeper",
    )(identity(2, fresh.id))
    assert missed.provisional and "1 retrieval unavailable" in missed.change_summary
    assert "(deeper search)" in missed.change_summary
    await client.aclose()


def test_citation_validation_blocks_broken_versions():
    report = fixture_report()
    validate_citations(report)
    first = report.assessments[0]
    foreign = next(e for e in report.evidence if e.claim_id != first.claim_id)
    broken = [
        report.model_copy(update={"evidence": [*report.evidence, report.evidence[0]]}),
        report.model_copy(
            update={"evidence": [report.evidence[0].model_copy(update={"claim_id": "clm_missing"})]}
        ),
        report.model_copy(update={"assessments": [*report.assessments, first]}),
        report.model_copy(update={"assessments": [first.model_copy(update={"version": 9})]}),
        report.model_copy(
            update={
                "assessments": [
                    first.model_copy(
                        update={
                            "relations": [
                                first.relations[0].model_copy(update={"evidence_id": foreign.id})
                            ]
                        }
                    )
                ]
            }
        ),
        report.model_copy(
            update={
                "assessments": [first.model_copy(update={"relations": [], "overall": "supported"})]
            }
        ),
    ]
    for version in broken:
        with pytest.raises(CitationInvalid):
            validate_citations(version)


def test_expansion_brings_full_video_claims_without_collisions():
    clip = claims_only()
    full = fixture_report()
    source = uuid.UUID("00000000-0000-4000-8000-0000000000bb")
    built = expanded_version(clip, full, source, identity(clip.version + 1, clip.id))
    ids = [c.id for c in built.claims]
    assert len(ids) == len(set(ids)) == len(clip.claims) + len(full.claims)
    assert all(i.startswith(f"fv{full.version}_") for i in ids[len(clip.claims) :])
    assert len(built.assessments) == len(full.assessments)
    assert all(a.version == built.version for a in built.assessments)
    assert str(source) in built.change_summary and built.provisional
    assert_component(json.loads(built.model_dump_json()), "ReportVersion")
    long = clip.model_copy(update={"claims": [clip.claims[0].model_copy(update={"id": "c" * 128})]})
    assert len(expanded_version(clip, long, source, identity(2, clip.id)).claims[-1].id) == 128


def test_claim_model_round_trip_is_unchanged_by_the_stages():
    claim = fixture_report().claims[0]
    assert Claim.model_validate(claim.model_dump()) == claim
