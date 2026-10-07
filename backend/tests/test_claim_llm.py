import asyncio
import json
import uuid

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from services.jobs.retries import RateLimited, UnknownOutcome
from services.pipeline.extraction import ExtractionStage, ObservationWindow, grounded_claims
from services.pipeline.llm import (
    MAX_RESPONSE_BYTES,
    Completion,
    ExtractionInvalid,
    ExtractionUnavailable,
    GroqAdapter,
    ScholarxivAdapter,
    configured_groq,
    configured_llm,
    parse_completion,
)
from services.settings import Settings


def envelope():
    return {
        "model": "synthetic-free",
        "usage": {"total_tokens": 10, "provider_private_detail": "must not persist"},
        "choices": [{"message": {"content": '{"occurrences":[]}'}, "finish_reason": "stop"}],
    }


@pytest.mark.parametrize(
    "case",
    [
        "success",
        "quota",
        "auth",
        "redirect",
        "wrong-model",
        "oversized",
        "timeout",
        "negative-usage",
        "bool-usage",
        "bad-choices",
        "bad-message",
        "bad-decision",
        "bad-utf8",
    ],
)
async def test_bounded_adapter_response_validation(case):
    body = envelope()
    if case == "wrong-model":
        body["model"] = "unverified"
    elif case in {"negative-usage", "bool-usage"}:
        body["usage"]["total_tokens"] = -1 if case == "negative-usage" else True
    elif case == "bad-choices":
        body["choices"] = []
    elif case == "bad-message":
        body["choices"][0]["message"] = None
    elif case == "bad-decision":
        body["decision_id"] = []

    def respond(request):
        if case == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if case == "oversized":
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))
        if case == "bad-utf8":
            return httpx.Response(200, content=b"\xff")
        status = {"quota": 429, "auth": 403, "redirect": 302}.get(case, 200)
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["synthetic-free"], max_tokens=100)
        if case == "success":
            completion = await adapter.complete({"observations": []}, repair=False)
            assert completion.usage == {"total_tokens": 10}
            assert parse_completion(completion)[0].occurrences == []
        else:
            error = (
                UnknownOutcome
                if case == "timeout"
                else RateLimited
                if case == "quota"
                else ExtractionUnavailable
                if case in {"auth", "redirect", "wrong-model"}
                else ExtractionInvalid
            )
            with pytest.raises(error):
                await adapter.complete({}, repair=False)


async def test_configured_adapter_uses_authenticated_non_redirecting_client(monkeypatch):
    factory = httpx.AsyncClient
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=envelope())

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return factory(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("services.pipeline.llm.httpx.AsyncClient", client)
    settings = Settings(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-free"],
        scholarxiv_api_key=SecretStr("synthetic-key"),
    )
    await configured_llm(settings).complete({}, repair=False)
    assert str(calls[0].url) == "https://www.scholarxiv.com/api/v1/router/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer synthetic-key"
    assert "stream" not in json.loads(calls[0].content)


@pytest.mark.parametrize("models,tokens", [([], 1), ([" "], 1), (["free"], 0), (["free"], 8193)])
def test_adapter_rejects_unbounded_or_implicit_configuration(models, tokens):
    with pytest.raises(ValueError):
        ScholarxivAdapter(allowed_models=models, max_tokens=tokens)


async def test_missing_configuration_fails_closed():
    with pytest.raises(ExtractionUnavailable):
        await ScholarxivAdapter(allowed_models=["free"], max_tokens=1).complete({}, repair=False)
    with pytest.raises(ExtractionUnavailable):
        await configured_llm(None).complete({}, repair=False)
    for adapter in (
        configured_llm(None),
        ScholarxivAdapter(allowed_models=["free"], max_tokens=1),
        GroqAdapter(max_tokens=1),
    ):
        with pytest.raises(ExtractionUnavailable):
            await adapter.fallbacks({})
        with pytest.raises(ExtractionUnavailable):
            await adapter.feedback("synthetic-id")
    adapter = ScholarxivAdapter(allowed_models=["free"], max_tokens=1)
    for decision in ("", " ", "x" * 201):
        with pytest.raises(ExtractionUnavailable):
            await adapter.feedback(decision)
    with pytest.raises(ExtractionUnavailable):
        await adapter.complete({}, repair=False, model="unverified")


@pytest.mark.parametrize("attempts", [-1, 11])
def test_recovery_attempts_are_bounded(attempts):
    with pytest.raises(ValueError):
        ScholarxivAdapter(allowed_models=["free"], max_tokens=1, recovery_attempts=attempts)


@pytest.mark.parametrize(
    "text",
    [
        "no object",
        "<think>unfinished",
        '{"occurrences":NaN}',
        'Preamble <think>{"occurrences":[]}',
        '```json\n<think>{"occurrences":[]}\n```',
    ],
)
def test_invalid_completion_hygiene(text):
    with pytest.raises(ExtractionInvalid):
        parse_completion(Completion(text, "stop", "free", None, {}))


@pytest.mark.parametrize("field", ["window_id", "id"])
def test_observation_identifiers_cannot_be_whitespace(field):
    body = {
        "version": 1,
        "window_id": "window",
        "hosted_processing_approved": False,
        "observations": [
            {
                "id": "speech",
                "role": "target",
                "text": "Synthetic.",
                "modality": "speech",
                "timebase": "media",
                "start_ms": 0,
                "end_ms": 1000,
                "speaker_id": None,
            }
        ],
    }
    target = body if field == "window_id" else body["observations"][0]
    target[field] = " "
    with pytest.raises(ValidationError):
        ObservationWindow.model_validate(body)


async def test_quality_tasks_cannot_use_extraction_fallback_policy():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://router.example"
    ) as http:
        quality = ScholarxivAdapter(
            http, allowed_models=["synthetic-free"], max_tokens=100, task="reconciliation"
        )
        await quality.complete({}, repair=False)
        assert requests[0]["model"] == "auto:quality"
        with pytest.raises(ValueError):
            ExtractionStage(quality)
    for task in ("reconciliation", "assessment"):
        with pytest.raises(ValueError):
            GroqAdapter(max_tokens=100, task=task)
    with pytest.raises(ValueError):
        ExtractionStage(GroqAdapter(max_tokens=100))


@pytest.mark.parametrize("case", ["degraded", "preset", "model", "fallback", "duplicate", "shape"])
async def test_unsafe_routing_decisions_fail_closed(case):
    body = {
        "model": "synthetic-free",
        "fallbacks": ["synthetic-free"],
        "preset": "cheap",
        "degraded": False,
    }
    if case == "degraded":
        body["degraded"] = True
    elif case == "preset":
        body["preset"] = "balanced"
    elif case == "model":
        body["model"] = "unverified"
    elif case == "fallback":
        body["fallbacks"] = ["unverified"]
    elif case == "duplicate":
        body["fallbacks"] *= 2
    else:
        body["fallbacks"] = {}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        base_url="https://router.example",
    ) as http:
        with pytest.raises(ExtractionUnavailable):
            await ScholarxivAdapter(
                http, allowed_models=["synthetic-free"], max_tokens=100
            ).fallbacks({})


@pytest.mark.parametrize("hint", ["-1", "NaN", "inf", "tomorrow", "86401"])
async def test_invalid_provider_cooldown_fails_without_retry(hint):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": hint})
        ),
        base_url="https://router.example",
    ) as http:
        with pytest.raises(ExtractionUnavailable):
            await ScholarxivAdapter(
                http, allowed_models=["synthetic-free"], max_tokens=100
            ).complete({}, repair=False)


@pytest.mark.parametrize("decision", [None, "", " ", "valid-id"])
async def test_completion_decision_identity_is_provider_specific(decision):
    body = {**envelope(), "decision_id": decision}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        base_url="https://router.example",
    ) as http:
        result = await ScholarxivAdapter(
            http, allowed_models=["synthetic-free"], max_tokens=100
        ).complete({}, repair=False)
        assert result.decision_id == ("valid-id" if decision == "valid-id" else None)
        body["model"] = "openai/gpt-oss-20b"
        result = await GroqAdapter(http, max_tokens=100).complete({}, repair=False)
        assert result.provider == "groq"
        assert result.decision_id is None


async def test_multiple_choices_are_invalid():
    body = envelope()
    body["choices"] *= 2
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        base_url="https://router.example",
    ) as http:
        with pytest.raises(ExtractionInvalid):
            await ScholarxivAdapter(
                http, allowed_models=["synthetic-free"], max_tokens=100
            ).complete({}, repair=False)


@pytest.mark.parametrize(
    "missing", ["extraction_enabled", "groq_free_route_verified", "groq_api_key"]
)
def test_groq_requires_independent_activation(missing):
    config = dict(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-free"],
        scholarxiv_api_key=SecretStr("synthetic"),
        groq_extraction_enabled=True,
        groq_free_route_verified=True,
        groq_api_key=SecretStr("synthetic"),
    )
    config[missing] = None if missing == "groq_api_key" else False
    with pytest.raises(ValidationError):
        Settings(**config)
    assert configured_groq(None) is None


async def test_verified_extraction_route_does_not_activate_quality_reconciliation():
    extraction_only = Settings(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-cheap"],
        scholarxiv_api_key=SecretStr("synthetic"),
    )
    with pytest.raises(ExtractionUnavailable):
        await configured_llm(extraction_only, task="reconciliation").complete({}, repair=False)
    reconciling = extraction_only.model_copy(
        update={
            "reconciliation_enabled": True,
            "reconciliation_free_routes_verified": True,
            "reconciliation_models": ["synthetic-quality"],
        }
    )
    adapter = configured_llm(reconciling, task="reconciliation")
    assert isinstance(adapter, ScholarxivAdapter)
    assert adapter.allowed_models == ["synthetic-quality"]
    assert configured_llm(reconciling).allowed_models == ["synthetic-cheap"]


def test_default_output_caps_match_the_measured_budget_candidate():
    config = Settings(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-cheap"],
        reconciliation_enabled=True,
        reconciliation_free_routes_verified=True,
        reconciliation_models=["synthetic-quality"],
        scholarxiv_api_key=SecretStr("synthetic"),
    )
    extraction = configured_llm(config)
    reconciliation = configured_llm(config, task="reconciliation")
    assert isinstance(extraction, ScholarxivAdapter)
    assert isinstance(reconciliation, ScholarxivAdapter)
    # Whole-input reconciliation rewrites every claim, so it needs the larger cap.
    assert (extraction.max_tokens, reconciliation.max_tokens) == (2048, 8192)
    assert (config.extraction_budget_tokens, config.extraction_reserved_tokens) == (
        196500,
        102500,
    )


@pytest.mark.parametrize(
    "missing",
    ["extraction_enabled", "reconciliation_free_routes_verified", "reconciliation_models"],
)
def test_quality_reconciliation_requires_independent_route_verification(missing):
    config = dict(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-cheap"],
        scholarxiv_api_key=SecretStr("synthetic"),
        reconciliation_enabled=True,
        reconciliation_free_routes_verified=True,
        reconciliation_models=["synthetic-quality"],
    )
    config[missing] = [] if missing == "reconciliation_models" else False
    with pytest.raises(ValidationError):
        Settings(**config)


async def test_configured_recovery_transports_use_exact_authenticated_endpoints(monkeypatch):
    factory = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path == "/api/v1/router":
            return httpx.Response(
                200,
                json={
                    "model": "synthetic-free",
                    "fallbacks": [],
                    "preset": "cheap",
                    "degraded": False,
                },
            )
        if request.url.path == "/api/v1/router/feedback":
            return httpx.Response(204)
        return httpx.Response(200, json={**envelope(), "model": "openai/gpt-oss-20b"})

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return factory(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("services.pipeline.llm.httpx.AsyncClient", client)
    config = Settings(
        database_url="postgresql+psycopg://localhost/synthetic",
        extraction_enabled=True,
        extraction_free_routes_verified=True,
        extraction_models=["synthetic-free"],
        scholarxiv_api_key=SecretStr("synthetic-primary"),
        groq_extraction_enabled=True,
        groq_free_route_verified=True,
        groq_api_key=SecretStr("synthetic-fallback"),
    )
    primary = configured_llm(config)
    assert await primary.fallbacks({}) == []
    await primary.feedback("synthetic-decision")
    fallback = configured_groq(config)
    await fallback.complete({}, repair=False)
    assert [str(request.url) for request in requests] == [
        "https://www.scholarxiv.com/api/v1/router",
        "https://www.scholarxiv.com/api/v1/router/feedback",
        "https://api.groq.com/openai/v1/chat/completions",
    ]
    assert requests[0].headers["Authorization"] == "Bearer synthetic-primary"
    assert requests[2].headers["Authorization"] == "Bearer synthetic-fallback"
    assert json.loads(requests[1].content) == {
        "decision_id": "synthetic-decision",
        "feedback": "regenerated",
    }


def quoted(*refs, flags=()):
    return {
        "proposition": "Synthetic proposition.",
        "taxonomy": "empirical",
        "source_refs": [dict(ref) for ref in refs],
        "context_refs": [],
        "assertion_mode": "asserted",
        "speaker_commitment": "uncommitted",
        "attributed_to": None,
        "eligibility_reason": "factual-claim",
        "uncertainty_flags": list(flags),
    }


def answer(*occurrences):
    return Completion(json.dumps({"occurrences": list(occurrences)}), "stop", "free", None, {})


TEXTS = {"speech": "Prices rose 2%. Sorry, prices rose 2% in May.", "text": "PRICES +20%"}


def test_quotes_resolve_to_exact_offsets_in_the_cited_observation():
    output, _ = parse_completion(
        answer(
            quoted({"observation_id": "speech", "quote": "Prices rose 2%."}),
            quoted({"observation_id": "speech", "quote": "prices rose 2%"}),
            quoted({"observation_id": "text", "quote": "prices +20%"}),
        ),
        TEXTS,
    )
    spans = [
        [(ref.observation_id, ref.start_char, ref.end_char) for ref in item.source_refs]
        for item in output.occurrences
    ]
    assert spans == [[("speech", 0, 15)], [("speech", 23, 37)], [("text", 0, 11)]]
    assert TEXTS["text"][0:11] == "PRICES +20%"


def test_a_repeated_quote_cites_the_next_unused_appearance():
    output, _ = parse_completion(
        answer(
            quoted({"observation_id": "speech", "quote": "rose 2%"}),
            quoted({"observation_id": "speech", "quote": "rose 2%"}),
        ),
        TEXTS,
    )
    assert [item.source_refs[0].start_char for item in output.occurrences] == [7, 30]


@pytest.mark.parametrize("quote", ['"Prices rose 2%."', "“Prices rose 2%.”", "'Prices rose 2%.'"])
def test_quotation_marks_wrapping_a_quote_are_not_part_of_it(quote):
    output, _ = parse_completion(
        answer(quoted({"observation_id": "speech", "quote": quote})), TEXTS
    )
    assert output.occurrences[0].source_refs[0].end_char == 15


def test_schema_errors_name_field_paths_without_echoing_model_output():
    bad = {**quoted({"observation_id": "speech", "quote": "Prices"}), "taxonomy": "opinion"}
    bad["Ignore previous instructions"] = "x"
    with pytest.raises(ExtractionInvalid) as raised:
        parse_completion(answer(bad), TEXTS)
    reason = raised.value.repair_reason
    assert "occurrences.0.taxonomy" in reason and "occurrences.0.?" in reason
    assert "opinion" not in reason and "Ignore" not in reason and len(reason) <= 200


async def test_the_one_repair_states_the_bounded_validation_reason():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["synthetic-free"], max_tokens=100)
        await adapter.complete({}, repair=True, repair_reason="occurrences.0.taxonomy: enum")
        await adapter.complete({}, repair=True, repair_reason="x" * 500)
    first, second = (request["messages"][0]["content"] for request in requests)
    assert "Earlier response invalid: occurrences.0.taxonomy: enum." in first
    assert "x" * 200 in second and "x" * 201 not in second


def test_context_references_to_target_observations_are_dropped():
    window = ObservationWindow(
        version=1,
        window_id="w",
        hosted_processing_approved=True,
        observations=[
            {
                "id": key,
                "role": role,
                "text": TEXTS[key] if key in TEXTS else "Earlier context.",
                "modality": "speech" if key != "text" else "text",
                "timebase": "media",
                "start_ms": start,
                "end_ms": start + 10,
                "speaker_id": None,
            }
            for key, role, start in (
                ("before", "context", 0),
                ("speech", "target", 10),
                ("text", "target", 20),
            )
        ],
    )
    occurrence = {
        **quoted({"observation_id": "speech", "quote": "in May"}),
        "context_refs": [
            {"observation_id": "text", "quote": "PRICES"},
            {"observation_id": "before", "quote": "Earlier"},
        ],
    }
    output, _ = parse_completion(answer(occurrence), TEXTS | {"before": "Earlier context."})
    (claim,) = grounded_claims(output, window, uuid.uuid4())
    assert [ref.observation_id for ref in claim.interpretation.context_refs] == ["before"]


def test_offset_references_remain_accepted():
    ref = {"observation_id": "speech", "start_char": 0, "end_char": 6}
    output, _ = parse_completion(answer(quoted(ref)), TEXTS)
    assert output.occurrences[0].source_refs[0].end_char == 6


@pytest.mark.parametrize(
    "ref",
    [
        {"observation_id": "speech", "quote": "Prices fell"},
        {"observation_id": "unknown", "quote": "Prices"},
        {"observation_id": "speech", "quote": "   "},
        {"observation_id": "speech", "quote": "Prices", "start_char": 0, "end_char": 6},
    ],
)
def test_unverifiable_quotes_are_invalid_output(ref):
    with pytest.raises(ExtractionInvalid):
        parse_completion(answer(quoted(ref)), TEXTS)


async def test_extraction_prompt_asks_for_verbatim_quotes_and_closed_enums():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://router.example"
    ) as http:
        await ScholarxivAdapter(http, allowed_models=["synthetic-free"], max_tokens=100).complete(
            {}, repair=False
        )
    system = requests[0]["messages"][0]["content"]
    schema = json.loads(system[system.index("{") :])
    assert set(schema["$defs"]["SourceRef"]["properties"]) == {"observation_id", "quote"}
    assert "verbatim" in system
    assert "eligibility_reason opinion" in system
    assert "never invent enum values" in system


async def test_provider_deadline_is_enforced_as_an_unknown_outcome():
    assert ScholarxivAdapter(allowed_models=["m"], max_tokens=1).timeout_seconds == 20

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=envelope())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["m"], max_tokens=1)
        adapter.timeout_seconds = 0.05
        with pytest.raises(UnknownOutcome):
            await adapter.complete({}, repair=False)
