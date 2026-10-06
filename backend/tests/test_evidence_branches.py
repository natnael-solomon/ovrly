"""Failure branches of the evidence providers and stages (BE-09, #27), without a database."""

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from evidence_cassettes import BASE, KEY, Providers
from test_evidence_units import FakeBucket, clients

from services.evidence import retrieval as retrieval_module
from services.evidence.assessment import RelationReply, check_reply, summary
from services.evidence.retrieval import ClaimRetrieval, Passage, SourceRecord
from services.evidence.stages import EvidenceStages, expansion_delay, with_heartbeat
from services.jobs.handlers import CancellationRequested
from services.jobs.retries import NonRetriableInput, RateLimited
from services.providers import http as http_module
from services.providers.budget import TokenBucket
from services.providers.crossref import CrossrefClient
from services.providers.fulltext import FullTextClient
from services.providers.http import ProviderError, send
from services.providers.papers import Paper, PapersClient
from services.providers.router import RouterClient
from services.settings import Settings


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def refuse(request):
    raise httpx.ConnectError("synthetic connection refused", request=request)


async def test_send_maps_transport_errors_and_oversized_bodies(monkeypatch):
    async with transport(refuse) as client:
        with pytest.raises(ProviderError, match="transport"):
            await send(client, "crossref", "GET", "https://api.crossref.org/v1/works")
    monkeypatch.setattr(http_module, "MAX_BODY_BYTES", 16)
    async with transport(lambda request: httpx.Response(200, content=b"x" * 64)) as client:
        with pytest.raises(ProviderError, match="too large"):
            await send(client, "crossref", "GET", "https://api.crossref.org/v1/works")
    async with transport(lambda request: httpx.Response(200, content=b"{}")) as client:
        response, body = await send(client, "crossref", "GET", "https://api.crossref.org/x")
    assert response.status_code == 200 and body == b"{}"
    assert http_module.retry_after(httpx.Response(429, headers={"Retry-After": "soon"})) is None
    assert http_module.retry_after(httpx.Response(429, headers={"Retry-After": "-1"})) is None


async def test_router_rejects_malformed_completions_and_survives_feedback_failures():
    from services.evidence.retrieval import QueryPlan
    from services.providers.router import CallBudget

    for body in (b"not json", b'{"choices": []}', b'{"choices": [{"message": {"content": 7}}]}'):
        async with transport(lambda request, body=body: httpx.Response(200, content=body)) as c:
            router = RouterClient(c, BASE, KEY, FakeBucket())
            with pytest.raises(ProviderError):
                await router.structured("auto:cheap", "s", "u", QueryPlan, CallBudget(2))
    async with transport(refuse) as client:
        router = RouterClient(client, BASE, KEY, FakeBucket())
        await router._feedback(None)
        await router._feedback("dec_synthetic")
        await RouterClient(client, BASE, KEY, FakeBucket(tokens=0))._feedback("dec_synthetic")


async def test_crossref_transport_and_malformed_bodies_are_unknown():
    async with transport(refuse) as client:
        assert await CrossrefClient(client).status("10.1234/x") == "unknown"
    async with transport(lambda request: httpx.Response(200, content=b"not json")) as client:
        assert await CrossrefClient(client).status("10.1234/x") == "unknown"
    async with transport(lambda request: httpx.Response(200, json={"message": 3})) as client:
        assert await CrossrefClient(client).status("10.1234/x") == "unknown"


async def test_full_text_failures_fall_back_to_nothing():
    async with transport(refuse) as client:
        full = FullTextClient(client)
        assert await full.arxiv("2601.00001v2") is None
        assert await full.europepmc("10.1234/x") is None

    def search(status=200, body=None, fetch_status=200, fetch_error=False):
        def handle(request):
            if request.url.path.endswith("/rest/search"):
                return httpx.Response(status, content=body or b"{}")
            if fetch_error:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(fetch_status, content=b"<p>" + b"x" * 100 + b"</p>")

        return handle

    hit = json.dumps(
        {"resultList": {"result": [{"pmcid": "PMC123", "isOpenAccess": "Y"}]}}
    ).encode()
    closed = json.dumps(
        {"resultList": {"result": [{"pmcid": "PMC123", "isOpenAccess": "N"}]}}
    ).encode()
    for handler in (
        search(status=500),
        search(body=b"not json"),
        search(body=b'{"resultList": {"result": []}}'),
        search(body=closed),
        search(body=hit, fetch_status=404),
        search(body=hit, fetch_error=True),
        search(body=b'{"resultList": 1}'),
    ):
        async with transport(handler) as client:
            assert await FullTextClient(client).europepmc("10.1234/x") is None
    async with transport(search(body=hit)) as client:
        found = await FullTextClient(client).europepmc("10.1234/x")
    assert found is not None and found.url.endswith("/PMC123")


async def test_papers_federated_client_error_is_a_provider_error():
    providers = Providers(federated_status=400)
    client, _, papers, _ = clients(providers, federated=True)
    with pytest.raises(ProviderError):
        await papers.search("q", 5)
    await client.aclose()


def paper(**fields):
    base = dict(
        title="T",
        summary="",
        authors=(),
        published=None,
        doi=None,
        arxiv_id=None,
        url=None,
        journal_ref=None,
        comment=None,
        sources=(),
    )
    return Paper(**{**base, **fields})


async def test_retrieval_helpers_cover_every_identity_shape():
    assert retrieval_module._timestamp(None) is None
    assert retrieval_module._timestamp("not a date") is None
    assert retrieval_module._timestamp("2026-01-01T00:00:00").tzinfo is not None
    assert retrieval_module._url(paper(doi="10.48550/arXiv.2601.1")) == (
        "https://doi.org/10.48550/arXiv.2601.1"
    )
    assert retrieval_module._url(paper(url="https://example.org/a")) == "https://example.org/a"
    assert retrieval_module._url(paper(url="ftp://example.org/a")) is None
    assert retrieval_module._url(paper()) is None
    assert retrieval_module._publisher(paper(sources=("openalex",))) == "openalex"
    assert retrieval_module._publisher(paper()) == "Unknown publisher"
    assert retrieval_module._source_type(paper(doi="10.1234/x")) == "other"
    withdrawn = paper(comment="This paper has been withdrawn by the authors.", doi="10.1234/x")
    async with transport(refuse) as client:
        assert await retrieval_module._retraction(CrossrefClient(client), withdrawn) == "withdrawn"
        assert await retrieval_module._retraction(CrossrefClient(client), paper()) == "unknown"


def passage(passage_id, level="abstract_only"):
    from datetime import UTC, datetime

    return Passage(
        passage_id=passage_id,
        evidence_id=f"evd_{passage_id}",
        origin=f"origin:{passage_id}",
        source=SourceRecord(
            id=f"src_{passage_id}", title="T", publisher="P", url=None, published_at=None
        ),
        source_type="preprint",
        inspection_level=level,
        retrieval_relevance="high",
        retraction_status="unknown",
        excerpt="text",
        retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        found_by=["neutral"],
    )


def test_relation_reply_checks_and_summary_wording():
    passages = [passage("p1"), passage("p2")]
    check = check_reply(passages)

    def reply(*ids):
        return RelationReply.model_validate(
            {
                "relations": [
                    {"passage_id": i, "relation": "supports", "rationale": "r"} for i in ids
                ]
            }
        )

    assert check(reply("p1", "p2")) is None
    assert "unknown passage ids" in check(reply("p1", "p9"))
    assert "exactly once" in check(reply("p1", "p1", "p2"))
    assert "missing passages" in check(reply("p1"))
    retrieval = ClaimRetrieval(claim_id="c", status="retrieved")
    text = summary("supported", {"support": 2}, passages, retrieval)
    assert "2 sources support" in text and "Only abstracts or metadata were read" in text
    full = summary("supported", {"support": 1}, [passage("p1", "full_text")], retrieval)
    assert "Only abstracts" not in full and "1 source support" in full
    long = summary(
        "insufficient_evidence", {}, passages, retrieval.model_copy(update={"failed_queries": 1})
    )
    assert len(long) <= 600 and "not evidence of absence" in long


def stages():
    settings = Settings(
        database_url="postgresql+psycopg://synthetic@127.0.0.1/synthetic",
        scholarxiv_api_key=KEY,
        _env_file=None,
    )
    return EvidenceStages(SimpleNamespace(), settings, transport=Providers().transport())


def job(payload):
    return SimpleNamespace(payload=payload, id=uuid.uuid4())


async def test_stage_payloads_are_validated_before_any_database_access():
    evidence = stages()
    with pytest.raises(NonRetriableInput):
        await evidence.retrieval(job({"investigation_id": "x"}), SimpleNamespace())
    base = {
        "investigation_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "base_version": 1,
    }
    with pytest.raises(NonRetriableInput):
        await evidence.assessment(job(base), SimpleNamespace())
    request = {
        "request_id": str(uuid.uuid4()),
        "investigation_id": str(uuid.uuid4()),
        "owner_id": str(uuid.uuid4()),
        "supersedes_version": 1,
        "claim_ids": [],
    }
    for payload in (
        {},
        {**request, "reason": "correction", "supersedes_version": "one"},
        {**request, "reason": "expansion"},
        {**request, "reason": "verdict"},
    ):
        with pytest.raises(NonRetriableInput):
            await evidence.reanalysis(job(payload), SimpleNamespace())
    assert evidence.budget("deeper").max_queries == 6
    assert set(evidence.handlers()) == {"retrieval", "assessment", "reanalysis"}


async def test_rate_limit_from_an_empty_bucket_reaches_the_caller():
    providers = Providers()
    client, _, papers, bucket = clients(providers)
    bucket.tokens = 0
    with pytest.raises(RateLimited):
        await papers.search("q", 5)
    assert providers.calls("/papers/search") == []
    await client.aclose()


async def test_a_429_holds_the_bucket_and_the_next_attempt_succeeds():
    providers = Providers()
    statuses = [429]

    def handle(request):
        if request.url.path.endswith("/papers/search") and statuses:
            return httpx.Response(statuses.pop(), headers={"Retry-After": "7"})
        return providers.handle(request)

    async with transport(handle) as client:
        bucket = FakeBucket()
        papers = PapersClient(client, BASE, KEY, bucket, False)
        result = await papers.search("city buses", 10)
    assert len(result.papers) == 5 and bucket.blocks == [7]


async def test_an_empty_bucket_waits_with_jitter_and_gives_up_past_the_limit(monkeypatch):
    waits = [12.0, 0.0]
    slept = []

    async def take(self):
        return waits.pop(0)

    async def sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(TokenBucket, "_take", take)
    bucket = TokenBucket(SimpleNamespace(), "synthetic", 300, max_wait_seconds=60, sleep=sleep)
    await bucket.acquire()
    assert len(slept) == 1 and 12 <= slept[0] <= 12 + 3.05
    waits.append(61.0)
    with pytest.raises(RateLimited):
        await bucket.acquire()
    assert len(slept) == 1


class Context:
    def __init__(self, cancel_after=None):
        self.lease_seconds = 0.1
        self.beats = 0
        self.cancel_after = cancel_after

    async def heartbeat(self):
        self.beats += 1
        if self.cancel_after is not None and self.beats >= self.cancel_after:
            raise CancellationRequested("synthetic")


async def test_with_heartbeat_extends_the_lease_and_stops_work_on_cancellation():
    async def work(seconds):
        await asyncio.sleep(seconds)
        return "done"

    context = Context()
    assert await with_heartbeat(context, work(1.2)) == "done" and context.beats == 1
    assert await with_heartbeat(Context(), work(0)) == "done"
    stopped = asyncio.Event()

    async def long():
        try:
            await asyncio.sleep(30)
        finally:
            stopped.set()

    with pytest.raises(CancellationRequested):
        await with_heartbeat(Context(cancel_after=1), long())
    assert stopped.is_set()


def test_expansion_backoff_doubles_and_caps():
    assert [expansion_delay(n) for n in (0, 1, 2, 5, 11)] == [30, 60, 120, 600, 600]
