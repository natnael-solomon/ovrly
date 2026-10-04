import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from services.experiments import router

ENDPOINT = "https://router.example/api/v1/router/chat/completions"
KEY = "sxv_synthetic-test-only"
VALID = (
    '{"occurrences":[{"proposition":"Ten seeds.","taxonomy":"empirical",'
    '"source_refs":[{"observation_id":"segment-0","start_char":0,"end_char":3}],'
    '"context_refs":[],"assertion_mode":"asserted","speaker_commitment":"endorsed",'
    '"attributed_to":null,"eligibility_reason":"factual-claim","uncertainty_flags":[]}]}'
)


def dataset(count=2, kind="synthetic", approved=False):
    return router.Dataset(
        schema_version=router.SCHEMA_VERSION,
        version="test-v2",
        kind=kind,
        split="dev",
        hosted_processing_approved=approved,
        provenance="Invented test data, not measured provider evidence",
        windows=[
            router.Window.model_validate(
                {
                    "window_id": f"window-{index}",
                    "context_status": "window-only",
                    "observations": [
                        {
                            "id": f"segment-{index}",
                            "role": "target",
                            "text": f"The sample contains {index} seeds.",
                            "source_type": "supplied-caption",
                            "speaker_id": "speaker-one",
                            "envelope": {
                                "start_ms": 0,
                                "end_ms": 10000,
                                "basis": "coarse-parent-envelope-not-subwindow-timing",
                            },
                        }
                    ],
                }
            )
            for index in range(count)
        ],
    )


def envelope(content=VALID, model="openai/gpt-oss-20b", finish_reason="stop"):
    return {
        "model": model,
        "decision_id": "synthetic-decision",
        "usage": {"total_tokens": 10},
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
    }


def run(responses, route="auto:cheap", no_think=False):
    requests = []
    recordings = []

    def handler(request):
        requests.append(json.loads(request.content))
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        response = responses[len(requests) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    window = dataset().windows[0]
    case = router.Case(route=route, window_id=window.window_id, no_think=no_think)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = router.run_case(client, ENDPOINT, KEY, case, window, 2048, recordings.append)
    return result, requests, recordings


def test_matrix_and_documented_request_fields():
    data = dataset(50)
    matrix = router.cases(data)
    assert len(matrix) == 525
    assert sum(case.no_think for case in matrix) == 175
    assert len({(c.route, c.window_id, c.no_think) for c in matrix}) == 525
    for case in matrix:
        body = router.request_body(case, data.windows[0], 2048)
        assert set(body) <= {"model", "models", "messages", "temperature", "max_tokens"}
        assert body["temperature"] == 0
        assert body["max_tokens"] == 2048
        if case.route in router.MODELS:
            assert body["models"] == [case.route]
            assert body["model"] == "auto:cheap"
        else:
            assert body["model"] == case.route
            assert "models" not in body
        prompt = body["messages"]
        assert len(prompt) == 6
        assert "untrusted data" in prompt[0]["content"]
        assert (
            json.dumps(router.Extraction.model_json_schema(), sort_keys=True)
            in prompt[0]["content"]
        )
        assert prompt[-1]["content"].endswith("/no_think") == case.no_think
        assert prompt[2]["role"] == prompt[4]["role"] == "assistant"


@pytest.mark.parametrize(
    "content,finish,valid,parse_ok,schema_ok",
    [
        (VALID, "stop", True, True, True),
        ('{"occurrences":[]}', "stop", True, True, True),
        ("<think>reasoning</think>\n```json\n" + VALID + "\n```", "stop", True, True, True),
        ("Here is the JSON: " + VALID, "stop", True, True, True),
        ("<think>not closed " + VALID, "stop", False, False, False),
        (VALID + " trailing prose", "stop", False, False, False),
        ('{"occurrences":', "length", False, False, False),
        (VALID, "length", False, True, True),
        (VALID, "content_filter", False, True, True),
        ('{"occurrences":[],"occurrences":[]}', "stop", False, False, False),
        ('{"occurrences":[],"score":NaN}', "stop", False, False, False),
        ('{"occurrences":[],"score":Infinity}', "stop", False, False, False),
        ('{"occurrences":[],"extra":1}', "stop", False, True, False),
        ('{"occurrences":"none"}', "stop", False, True, False),
        ('{"claims":[]}', "stop", False, True, False),
        (VALID.replace('"Ten seeds."', "12"), "stop", False, True, False),
        (VALID.replace("segment-0", "invented"), "stop", False, True, True),
    ],
)
def test_validation_is_strict(content, finish, valid, parse_ok, schema_ok):
    result = router.validate_output(content, finish, dataset().windows[0])
    assert result.valid == valid
    assert result.json_parse_ok == parse_ok
    assert result.pydantic_valid == schema_ok
    assert result.truncated_at_max_tokens == (finish == "length")


def test_diagnostics_preserve_hygiene_and_enum_failures():
    result = router.validate_output(
        "<think>why</think>\n```json\n" + VALID + "\n```", "stop", dataset().windows[1]
    )
    assert result.thinking_leaked and result.fenced
    assert not result.raw_json_parse_ok
    assert result.json_parse_ok and result.evidence_id_hallucination
    assert not result.valid
    result = router.validate_output(
        VALID.replace("empirical", "incorrect"), "stop", dataset().windows[0]
    )
    assert result.enum_errors == 1
    assert not result.pydantic_valid
    assert result.schema_errors


def test_success_needs_no_repair_and_records_executor_usage():
    result, requests, records = run([httpx.Response(200, json=envelope())])
    assert result.valid and result.repair_outcome == "not_needed"
    assert len(requests) == len(records) == 1
    assert result.attempts[0].model == "openai/gpt-oss-20b"
    assert result.attempts[0].decision_id == "synthetic-decision"
    assert result.attempts[0].usage == {"total_tokens": 10}
    assert result.attempts[0].raw_length == len(VALID)
    assert result.attempts[0].latency_ms >= 0


@pytest.mark.parametrize("repaired", [True, False])
@pytest.mark.parametrize("no_think", [True, False])
def test_only_one_repair_then_fail_closed(repaired, no_think):
    result, requests, records = run(
        [
            httpx.Response(200, json=envelope("not JSON")),
            httpx.Response(200, json=envelope(VALID if repaired else "still invalid")),
        ],
        no_think=no_think,
    )
    assert result.valid == repaired
    assert result.repair_outcome == ("succeeded" if repaired else "failed")
    assert len(requests) == len(records) == 2
    assert len(requests[1]["messages"]) == 8
    assert requests[1]["messages"][-2] == {"role": "assistant", "content": "not JSON"}
    assert requests[1]["messages"][-1]["content"].endswith("/no_think") == no_think


@pytest.mark.parametrize("status", [302, 403, 429, 502])
def test_http_errors_are_recorded_without_retries_or_downgrades(status):
    result, requests, records = run([httpx.Response(status, text="denied " + KEY)])
    assert not result.valid and result.repair_outcome == "not_attempted"
    assert result.attempts[0].error == f"http_{status}"
    assert len(requests) == 1
    assert KEY not in json.dumps(records)
    assert "[REDACTED]" in records[0]["response"]


def test_transport_failure_is_explicit():
    result, requests, records = run([httpx.ReadTimeout("sensitive exception text")])
    assert not result.valid and len(requests) == 1
    assert result.attempts[0].error == "transport_error"
    assert "sensitive" not in json.dumps(records)


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {"choices": []},
        {"choices": ["invalid"]},
        {"choices": [{"message": None}]},
        {**envelope(), "model": None},
        {**envelope(), "decision_id": None},
        {**envelope(), "usage": None},
        {**envelope(), "choices": envelope()["choices"] * 2},
    ],
)
def test_malformed_envelopes_cannot_count_as_success(value):
    result, requests, _ = run([httpx.Response(200, json=value)])
    assert not result.valid and len(requests) == 1
    assert result.attempts[0].error == "invalid_completion_envelope"


def test_invalid_envelope_json_and_pinned_mismatch():
    result, _, _ = run([httpx.Response(200, text="not JSON")])
    assert result.attempts[0].error == "invalid_completion_envelope"
    result, requests, _ = run(
        [httpx.Response(200, json=envelope(model="unexpected"))], route=router.MODELS[0]
    )
    assert not result.valid and len(requests) == 1
    assert result.attempts[0].error == "pinned_model_mismatch"


def all_results(data, valid_count=None):
    results = []
    for case in router.cases(data):
        valid = valid_count is None or int(case.window_id.rsplit("-", 1)[1]) < valid_count
        results.append(
            router.Result(
                case=case,
                attempts=[
                    router.Attempt(
                        model="first-executor",
                        usage={"total_tokens": 10},
                        latency_ms=10,
                        validation=router.Validation(valid=False),
                    ),
                    router.Attempt(
                        model="repair-executor",
                        usage={"total_tokens": 20},
                        latency_ms=30,
                        validation=router.Validation(valid=valid),
                    ),
                ],
                repair_outcome="succeeded" if valid else "failed",
                valid=valid,
            )
        )
    return results


@pytest.mark.parametrize("count,expected", [(44, "no_go_candidate"), (45, "go_candidate")])
def test_exact_threshold_and_executor_attribution(count, expected):
    data = dataset(50, "real", True)
    summary = router.summarize(data, all_results(data, count))
    assert summary["threshold_observation"] == expected
    assert summary["decision"] == "pending_team_approval"
    assert summary["status"] == "measured_candidate"
    assert ("auto:cheap" in summary["qualifying_routes"]) == (count == 45)
    assert "auto:quality" not in summary["qualifying_routes"]
    assert summary["overall"]["reported_total_tokens"] == 15750
    assert summary["overall"]["requests_attempted"] == 1050
    assert summary["overall"]["p50_latency_ms"] == 40
    assert summary["overall"]["p95_latency_ms"] == 40
    by_model = summary["by_actual_executor_attempt"]
    assert by_model["first-executor"]["first_attempts"] == 525
    assert by_model["first-executor"]["repair_attempts"] == 0
    assert by_model["repair-executor"]["repair_attempts"] == 525


@pytest.mark.parametrize("count,kind", [(2, "real"), (50, "synthetic")])
def test_pilots_never_produce_go_decision(count, kind):
    data = dataset(count, kind)
    summary = router.summarize(data, all_results(data))
    assert summary["status"] == "pilot_only"
    assert summary["threshold_observation"] == "not_evaluated"
    assert summary["qualifying_routes"] == []
    with pytest.raises(ValueError, match="Incomplete"):
        router.summarize(data, all_results(data)[:-1])
    with pytest.raises(ValueError, match="Incomplete"):
        router.summarize(data, [])


def test_missing_token_usage_is_not_reported_as_measured_zero():
    results = all_results(dataset())
    for result in results:
        result.attempts[0].usage = None
        result.attempts[1].usage = {"total_tokens": True}
    metrics = router.metrics(results)
    assert metrics["usage_missing_attempts"] == 2 * len(results)
    assert "unverified" in metrics["quota_consumed"]
    assert router.percentile([1, 2, 3, 4, 5], 0.95) == 5


@pytest.mark.parametrize(
    "change",
    [
        {"split": "test"},
        {"windows": []},
        {"hosted_processing_approved": "true"},
        {"schema_version": "be01-experimental-v1"},
        {"windows": [dataset().windows[0].model_dump()] * 2},
        {"windows": [{**dataset().windows[0].model_dump(), "window_id": "bad\n"}]},
    ],
)
def test_input_rejects_holdouts_coercion_and_duplicate_ids(change):
    with pytest.raises(ValidationError):
        router.Dataset.model_validate({**dataset().model_dump(), **change})


def test_duplicate_transcript_is_rejected():
    value = dataset().model_dump()
    value["windows"][1]["observations"][0]["text"] = value["windows"][0]["observations"][0]["text"]
    with pytest.raises(ValidationError):
        router.Dataset.model_validate(value)


@pytest.mark.parametrize(
    "change",
    [
        {"observations": []},
        {"context_status": "additional-context-supplied"},
        {"observations": [dataset().windows[0].observations[0].model_dump()] * 2},
    ],
)
def test_window_requires_unique_observations_and_matching_context(change):
    with pytest.raises(ValidationError):
        router.Window.model_validate({**dataset().windows[0].model_dump(), **change})


@pytest.mark.parametrize(
    "change",
    [
        {"text": "   "},
        {"role": "context"},
        {"speaker_id": 7},
        {
            "envelope": {
                "start_ms": 100,
                "end_ms": 100,
                "basis": "user-timed-caption-not-media-verified",
            }
        },
        {
            "envelope": {
                "start_ms": True,
                "end_ms": 100,
                "basis": "user-timed-caption-not-media-verified",
            }
        },
    ],
)
def test_invalid_observation_is_rejected(change):
    value = dataset().windows[0].model_dump()
    value["observations"][0].update(change)
    with pytest.raises(ValidationError):
        router.Window.model_validate(value)


def test_reused_observation_identity_is_consistent_across_windows():
    value = dataset().model_dump()
    value["windows"][1]["observations"][0]["id"] = "segment-0"
    with pytest.raises(ValidationError, match="Reused observation"):
        router.Dataset.model_validate(value)
    context = deepcopy(value["windows"][0]["observations"][0])
    context["role"] = "context"
    value["windows"][1]["observations"][0]["id"] = "segment-1"
    value["windows"][1]["observations"].append(context)
    value["windows"][1]["context_status"] = "additional-context-supplied"
    assert router.Dataset.model_validate(value)


@pytest.mark.parametrize(
    "ref,expected",
    [
        ({"observation_id": "missing", "start_char": 0, "end_char": 2}, "unknown_observation_id"),
        ({"observation_id": "segment-0", "start_char": 0, "end_char": 999}, "span_out_of_bounds"),
        ({"observation_id": "segment-0", "start_char": 3, "end_char": 4}, "whitespace_only_span"),
    ],
)
def test_source_grounding_errors_are_not_schema_success(ref, expected):
    value = json.loads(VALID)
    value["occurrences"][0]["source_refs"] = [ref]
    result = router.validate_output(json.dumps(value), "stop", dataset().windows[0])
    assert result.pydantic_valid
    assert not result.valid
    assert expected in result.source_reference_errors
    assert result.evidence_id_hallucination == (expected == "unknown_observation_id")


@pytest.mark.parametrize(
    "updates",
    [
        {"start_char": -1},
        {"start_char": True},
        {"start_char": "0"},
        {"end_char": 0},
        {"start_char": 5, "end_char": 4},
    ],
)
def test_invalid_offset_types_and_order_fail_schema(updates):
    value = json.loads(VALID)
    value["occurrences"][0]["source_refs"][0].update(updates)
    result = router.validate_output(json.dumps(value), "stop", dataset().windows[0])
    assert not result.valid and not result.pydantic_valid
    assert result.schema_errors


@pytest.mark.parametrize(
    "updates",
    [
        {"taxonomy": "normative"},
        {"assertion_mode": "hypothetical"},
        {"assertion_mode": "questioned"},
        {"eligibility_reason": "quoted-not-endorsed"},
        {"uncertainty_flags": ["missing-context", "missing-context"]},
        {"attributed_to": ""},
        {"source_refs": []},
        {"verdict": "true"},
    ],
)
def test_semantic_and_required_output_constraints(updates):
    value = json.loads(VALID)
    value["occurrences"][0].update(updates)
    result = router.validate_output(json.dumps(value), "stop", dataset().windows[0])
    assert not result.valid and result.schema_errors


def test_context_roles_and_single_repair_diagnostics():
    value = json.loads(VALID)
    value["occurrences"][0]["context_refs"] = value["occurrences"][0]["source_refs"]
    result, requests, records = run(
        [
            httpx.Response(200, json=envelope(json.dumps(value))),
            httpx.Response(200, json=envelope()),
        ]
    )
    assert result.valid and result.repair_outcome == "succeeded"
    assert result.attempts[0].validation.pydantic_valid
    assert result.attempts[0].validation.source_reference_errors == ["wrong_reference_role"]
    assert "wrong_reference_role" in requests[1]["messages"][-1]["content"]
    assert len(records) == 2
    source = dataset().windows[0].model_dump()
    source["context_status"] = "additional-context-supplied"
    context = deepcopy(source["observations"][0])
    context.update(id="context-one", role="context")
    source["observations"].append(context)
    value["occurrences"][0]["context_refs"] = [
        {"observation_id": "context-one", "start_char": 0, "end_char": 3}
    ]
    window = router.Window.model_validate(source)
    assert router.validate_output(json.dumps(value), "stop", window).valid
    value["occurrences"][0]["source_refs"] = value["occurrences"][0]["context_refs"]
    assert not router.validate_output(json.dumps(value), "stop", window).valid


def test_unicode_codepoints_duplicates_and_speaker_boundaries():
    source = dataset().windows[0].model_dump()
    source["observations"][0]["text"] = "A \U0001f331 costs five coins."
    value = json.loads(VALID)
    value["occurrences"][0]["source_refs"][0].update(start_char=2, end_char=3)
    window = router.Window.model_validate(source)
    assert window.observations[0].text[2:3] == "\U0001f331"
    assert router.validate_output(json.dumps(value), "stop", window).valid
    value["occurrences"][0]["source_refs"] *= 2
    result = router.validate_output(json.dumps(value), "stop", window)
    assert result.source_reference_errors == ["duplicate_source_span"]
    second = deepcopy(source["observations"][0])
    second.update(id="second-speaker", speaker_id="speaker-two")
    source["observations"].append(second)
    value["occurrences"][0]["source_refs"][1] = deepcopy(value["occurrences"][0]["source_refs"][1])
    value["occurrences"][0]["source_refs"][1]["observation_id"] = "second-speaker"
    result = router.validate_output(json.dumps(value), "stop", router.Window.model_validate(source))
    assert result.source_reference_errors == ["mixed_source_speakers"]


def test_prompt_examples_are_separate_and_valid_and_real_text_is_unchanged():
    window = dataset().windows[0]
    prompt = router.messages(window, False)
    for index in (1, 3):
        example = router.Window.model_validate_json(prompt[index]["content"])
        assert example.window_id not in {item.window_id for item in dataset().windows}
        assert router.validate_output(prompt[index + 1]["content"], "stop", example).valid
    assert json.loads(prompt[-1]["content"]) == window.model_dump()
    assert "do not invent depicted identities" in prompt[0]["content"]
    assert "insufficient-context" in prompt[0]["content"]
    assert "Preserve repeated claims and corrections" in prompt[0]["content"]


def test_eligible_counterfactual_and_excluded_quote_are_representable():
    for changes in (
        {"assertion_mode": "counterfactual"},
        {
            "assertion_mode": "reported",
            "speaker_commitment": "rejected",
            "eligibility_reason": "quoted-not-endorsed",
        },
        {"taxonomy": "normative", "eligibility_reason": "opinion"},
        {
            "assertion_mode": "unclear",
            "eligibility_reason": "insufficient-context",
            "uncertainty_flags": ["missing-context", "unresolved-reference"],
        },
    ):
        value = json.loads(VALID)
        value["occurrences"][0].update(changes)
        assert router.validate_output(json.dumps(value), "stop", dataset().windows[0]).valid
    value["occurrences"][0].pop("attributed_to")
    assert not router.validate_output(json.dumps(value), "stop", dataset().windows[0]).valid


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://router.example/api/v1/router/chat/completions",
        "https://user:password@router.example/api/v1/router/chat/completions",
        ENDPOINT + "?key=secret",
        ENDPOINT + "#secret",
        "https://router.example/wrong",
        "https:///api/v1/router/chat/completions",
    ],
)
def test_endpoint_requires_verified_https_shape(endpoint):
    with pytest.raises(ValueError):
        router.endpoint_url(endpoint)
    assert router.endpoint_url(ENDPOINT) == ENDPOINT


@pytest.fixture
def cli(monkeypatch, tmp_path):
    source = tmp_path / "input.json"
    source.write_text(dataset(2, approved=True).model_dump_json(), encoding="utf-8")
    runner = tmp_path / "backend/services/experiments/router.py"
    runner.parent.mkdir(parents=True)
    runner.write_bytes(Path(router.__file__).read_bytes())
    monkeypatch.setattr(router, "__file__", str(runner))
    monkeypatch.setenv("SCHOLARXIV_EXPERIMENT_API_KEY", KEY)

    def invoke(*extra):
        monkeypatch.setattr("sys.argv", ["router", str(source), *extra])
        return router.main()

    return invoke, source, tmp_path / ".scratch/router"


EXECUTE = ["--execute", "--endpoint", ENDPOINT, "--run-id", "test-run", "--max-requests", "42"]


def test_cli_plan_is_offline_and_writes_nothing(cli, monkeypatch, capsys):
    invoke, _, output = cli

    def forbidden(*args, **kwargs):
        pytest.fail("Plan must not construct a network client")

    monkeypatch.setattr(router.httpx, "Client", forbidden)
    assert invoke() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["no_provider_calls"]
    assert plan["cases"] == 21 and plan["maximum_requests"] == 42
    assert not output.exists()


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-tokens", "299"],
        ["--execute"],
        [*EXECUTE, "--max-requests", "41"],
        [*EXECUTE, "--run-id", "../escape"],
        [*EXECUTE, "--endpoint", "http://unsafe"],
    ],
)
def test_cli_preflight_fails_before_recording(cli, extra, capsys):
    invoke, _, output = cli
    assert invoke(*extra) == 2
    assert not output.exists()
    assert "Experiment failed" in capsys.readouterr().err


def test_cli_missing_key_and_approval_are_blockers(cli, monkeypatch):
    invoke, source, output = cli
    monkeypatch.delenv("SCHOLARXIV_EXPERIMENT_API_KEY")
    assert invoke(*EXECUTE) == 2
    source.write_text(dataset().model_dump_json(), encoding="utf-8")
    assert invoke(*EXECUTE) == 2
    assert not output.exists()


def test_cli_bad_dataset_does_not_echo_content(cli, capsys):
    invoke, source, _ = cli
    source.write_text('{"private":"do not print this"}', encoding="utf-8")
    assert invoke() == 2
    assert "do not print this" not in str(capsys.readouterr())
    source.unlink()
    assert invoke() == 2


@pytest.mark.parametrize("valid", [True, False])
def test_cli_persists_complete_run_and_refuses_overwrite(cli, monkeypatch, capsys, valid):
    invoke, _, output = cli
    client_class = httpx.Client

    def response(request):
        body = json.loads(request.content)
        window = json.loads(body["messages"][5]["content"].removesuffix("\n/no_think"))
        content = VALID.replace("segment-0", window["observations"][0]["id"])
        return httpx.Response(
            200,
            json=envelope(
                content if valid else "invalid",
                model=body.get("models", [router.MODELS[0]])[0],
            ),
        )

    def client(**kwargs):
        assert kwargs == {"timeout": 60, "follow_redirects": False, "trust_env": False}
        return client_class(
            **kwargs,
            transport=httpx.MockTransport(response),
        )

    monkeypatch.setattr(router.httpx, "Client", client)
    assert invoke(*EXECUTE) == (0 if valid else 1)
    run_dir = output / "test-run"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["schema_version"] == router.SCHEMA_VERSION == "be01-experimental-v2"
    assert manifest["prompt_version"] == router.PROMPT_VERSION
    assert manifest["schema"] == router.Extraction.model_json_schema()
    assert manifest["input_schema"] == router.Dataset.model_json_schema()
    assert len(manifest["schema_module_sha256"]) == len(manifest["prompt_template_sha256"]) == 64
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["status"] == "pilot_only"
    assert summary["overall"]["requests_attempted"] == (21 if valid else 42)
    assert len(json.loads((run_dir / "results.json").read_text())) == 21
    before = (run_dir / "attempts.jsonl").read_bytes()
    assert KEY.encode() not in before
    assert invoke(*EXECUTE) == 2
    assert (run_dir / "attempts.jsonl").read_bytes() == before
    assert KEY not in str(capsys.readouterr())


def test_cli_interrupted_recording_never_claims_complete(cli, monkeypatch):
    invoke, _, output = cli

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(router, "run_case", interrupted)
    with pytest.raises(KeyboardInterrupt):
        invoke(*EXECUTE)
    run_dir = output / "test-run"
    assert json.loads((run_dir / "manifest.json").read_text())["status"] == "started"
    assert not (run_dir / "summary.json").exists()
