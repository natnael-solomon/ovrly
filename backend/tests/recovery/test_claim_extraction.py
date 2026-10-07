import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from recovery.harness import ScriptedFaults
from services.api.main import create_app
from services.jobs.faults import Checkpoint, SimulatedCrash
from services.jobs.handlers import default_handlers
from services.pipeline.extraction import ObservationWindow, enqueue_extraction
from services.pipeline.llm import ScholarxivAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "packages" / "contracts"))
import validate  # noqa: E402


def window():
    return ObservationWindow(
        version=1,
        window_id="window-1",
        hosted_processing_approved=True,
        observations=[
            {
                "id": "speech-1",
                "role": "target",
                "text": "The rate did not increase by 20%.",
                "modality": "speech",
                "timebase": "media",
                "start_ms": 1000,
                "end_ms": 4000,
                "speaker_id": None,
            }
        ],
    )


def extraction():
    return {
        "occurrences": [
            {
                "proposition": "The rate did not increase by 20%.",
                "taxonomy": "empirical",
                "source_refs": [{"observation_id": "speech-1", "start_char": 0, "end_char": 33}],
                "context_refs": [],
                "assertion_mode": "asserted",
                "speaker_commitment": "endorsed",
                "attributed_to": None,
                "eligibility_reason": "factual-claim",
                "uncertainty_flags": [],
            }
        ]
    }


def completion(output):
    return {
        "model": "fixture-free-model",
        "decision_id": "synthetic-decision-1",
        "usage": {"total_tokens": 100},
        "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
    }


async def create_investigation(client, *, capture=False):
    response = await client.post(
        "/v1/captures" if capture else "/v1/investigations",
        json={"chunk_duration_ms": 10000}
        if capture
        else {"source": {"kind": "url", "url": "https://example.org/synthetic"}},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert response.status_code == (201 if capture else 202)
    return uuid.UUID(response.json()["id"])


async def test_one_window_publishes_source_grounded_provisional_claim(harness):
    h = harness
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        app = h.app(None, stages=default_handlers(llm=adapter))
        async with app.router.lifespan_context(app), h.client(app) as client:
            investigation_id = await create_investigation(client)
            async with h.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, h.queue, investigation_id, h.owner.id, window()
                )
            await h.wait_for_state(queued.job_id, "published")
            response = await client.get(f"/v1/investigations/{investigation_id}")
            assert response.status_code == 200
            body = response.json()
            validate.Validator().validate(body, "investigation.schema.json")
            assert body["processing_status"] == "partial"
            assert body["stage"] == body["job"]["stage"] == "claim_extraction"
            report = body["report"]
            attempt = report["processing_attempts"][0]
            assert attempt["provider"] == "scholarxiv"
            assert attempt["model"] == "fixture-free-model"
            assert attempt["decision_id"] == "synthetic-decision-1"
            assert attempt["task"] == "claim_extraction"
            assert attempt["outcome"] == "valid"
            assert attempt["total_tokens"] == 100
            assert report["provisional"] is True
            assert report["assessments"] == report["evidence"] == []
            (claim,) = report["claims"]
            assert (
                claim["original_text"]
                == claim["proposition"]
                == ("The rate did not increase by 20%.")
            )
            assert claim["interval"] == {"start_ms": 1000, "end_ms": 4000, "timebase": "media"}
            assert claim["interpretation"]["taxonomy"] == "empirical"
            assert claim["interpretation"]["eligibility_reason"] == "factual-claim"
            async with h.client(app, outsider=True) as outsider:
                assert (
                    await outsider.get(f"/v1/investigations/{investigation_id}")
                ).status_code == 404
            assert len(requests) == 1
            assert set(requests[0]) == {"model", "models", "messages", "temperature", "max_tokens"}
            assert requests[0]["model"] == "auto:cheap"
            assert requests[0]["temperature"] == 0
            assert requests[0]["models"] == ["fixture-free-model"]


async def test_rate_limited_extraction_resumes_without_spending_its_repair(harness):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=completion(extraction()))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(
            http, allowed_models=["fixture-free-model"], max_tokens=2048, recovery_attempts=1
        )
        app = harness.app(
            None,
            stages=default_handlers(llm=adapter),
            job_retry_rate_limited_attempts=1,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            terminal = await harness.wait_for_state(queued.job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert terminal.status.state.value == "published"
            assert body["report"]["claims"][0]["original_text"] == window().observations[0].text
            assert len(requests) == 2
            assert "Regenerate once" not in requests[1]["messages"][0]["content"]


async def test_provider_cooldown_is_not_shortened_and_can_be_cancelled(harness):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=2048,
                    recovery_attempts=1,
                )
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            async with asyncio.timeout(5):
                while True:
                    record = await harness.queue.get(queued.job_id)
                    if record.retry_counts:
                        break
                    await asyncio.sleep(0.02)
            assert (record.available_at - datetime.now(UTC)).total_seconds() > 590
            assert (await client.post(f"/v1/jobs/{queued.job_id}/cancel")).status_code == 200
            await harness.wait_for_state(queued.job_id, "cancelled")
            assert len(calls) == 1


async def test_gateway_failure_uses_only_verified_decision_fallback(harness):
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append((request.url.path, payload))
        if request.url.path == "/api/v1/router":
            assert set(payload) == {"messages", "preset", "models"}
            assert payload["preset"] == "cheap"
            return httpx.Response(
                200,
                json={
                    "decision_id": "synthetic-routing-decision",
                    "model": "fixture-free-model",
                    "fallbacks": ["fixture-backup"],
                    "preset": "cheap",
                    "degraded": False,
                },
            )
        if payload["model"] == "auto:cheap":
            return httpx.Response(502)
        assert payload["model"] == "fixture-backup"
        return httpx.Response(200, json={**completion(extraction()), "model": "fixture-backup"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(
            http,
            allowed_models=["fixture-free-model", "fixture-backup"],
            max_tokens=2048,
            recovery_attempts=1,
        )
        app = harness.app(
            None,
            stages=default_handlers(llm=adapter),
            job_retry_backoff_seconds=0.01,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            terminal = await harness.wait_for_state(queued.job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert terminal.status.state.value == "published"
            assert body["report"]["claims"][0]["original_text"] == window().observations[0].text
            assert len(requests) == 3
            record = await harness.queue.get(queued.job_id)
            (published,) = await harness.queue.published(record.key)
            assert [call["operation"] for call in published.result["requests"]] == [
                "completion",
                "decision",
                "completion",
            ]


async def test_crash_after_routing_response_does_not_repeat_or_infer(harness):
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/router":
            return httpx.Response(
                200,
                json={
                    "model": "fixture-free-model",
                    "fallbacks": ["fixture-free-model"],
                    "preset": "cheap",
                    "degraded": False,
                },
            )
        return (
            httpx.Response(502)
            if len(calls) == 1
            else httpx.Response(200, json=completion(extraction()))
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(
                http, allowed_models=["fixture-free-model"], max_tokens=2048, recovery_attempts=1
            )
        )
        app = create_app(harness.settings())
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            first = harness.worker(
                None,
                stages=handlers,
                faults=ScriptedFaults(crash_at=Checkpoint.AFTER_PROVIDER_CALL, attempts=(2,)),
            )
            await first.start()
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(first.wait(), 5)
            second = harness.worker(None, stages=handlers)
            await second.start()
            await harness.wait_for_state(queued.job_id, "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert body["error"]["code"] == "EXTRACTION_OUTCOME_UNKNOWN"
            assert calls == ["/api/v1/router/chat/completions", "/api/v1/router"]


@pytest.mark.parametrize("status", [200, 429, 502])
async def test_unavailable_decision_fallback_fails_explicitly(harness, status):
    calls = []

    def provider(request):
        calls.append(request.url.path)
        if request.url.path == "/api/v1/router":
            return httpx.Response(
                status,
                json={
                    "model": "fixture-free-model",
                    "fallbacks": [],
                    "preset": "cheap",
                    "degraded": False,
                },
            )
        return httpx.Response(502)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=2048,
                    recovery_attempts=1,
                )
            ),
            job_retry_backoff_seconds=0.01,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            await harness.wait_for_state(queued.job_id, "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert body["error"]["code"] == "EXTRACTION_UNAVAILABLE"
            assert body["report"] is None
            assert len(calls) == 2


async def test_crash_during_feedback_does_not_replay_feedback_or_reset_repair(harness):
    inference = []
    feedback = []

    def provider(request):
        if request.url.path == "/api/v1/router/feedback":
            feedback.append(request)
            if len(feedback) == 1:
                raise SimulatedCrash("synthetic feedback outcome unknown")
            return httpx.Response(204)
        inference.append(request)
        return httpx.Response(200, json=completion({"invalid": True}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        )
        app = create_app(harness.settings())
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            first = harness.worker(None, stages=handlers)
            await first.start()
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(first.wait(), 5)
            second = harness.worker(None, stages=handlers)
            await second.start()
            await harness.wait_for_state(queued.job_id, "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert body["error"]["code"] == "EXTRACTION_INVALID"
            assert len(inference) == len(feedback) == 2
            async with harness.control.engine.begin() as connection:
                replay = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            assert replay.job_id == queued.job_id
            assert not replay.created


async def test_later_failure_preserves_published_version(harness):
    async with pipeline(harness, [completion(extraction()), completion({"invalid": True})]) as (
        client,
        investigation_id,
        first,
        requests,
        _,
    ):
        await harness.wait_for_state(first.job_id, "published")
        original = (await client.get(f"/v1/investigations/{investigation_id}/reports/1")).json()
        async with harness.control.engine.begin() as connection:
            second = await enqueue_extraction(
                connection,
                harness.queue,
                investigation_id,
                harness.owner.id,
                window().model_copy(update={"window_id": "later-window"}),
            )
        await harness.wait_for_state(second.job_id, "failed")
        failed = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert failed["error"]["code"] == "EXTRACTION_INVALID"
        preserved = await client.get(f"/v1/investigations/{investigation_id}/reports/1")
        assert preserved.status_code == 200
        assert preserved.json() == original
        assert len(requests) == 3


@pytest.mark.parametrize("fallback_outcome", ["success", "quota", "repair"])
async def test_exhausted_quota_uses_separately_authorized_groq_extraction(
    harness, fallback_outcome
):
    from services.pipeline.llm import GroqAdapter

    def scholarxiv(request):
        return httpx.Response(429, headers={"Retry-After": "0"})

    groq_requests = []

    def groq(request):
        payload = json.loads(request.content)
        groq_requests.append(payload)
        assert request.url.path == "/openai/v1/chat/completions"
        assert payload["model"] == "openai/gpt-oss-20b"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert "models" not in payload
        if fallback_outcome == "quota":
            return httpx.Response(429, headers={"Retry-After": "0"})
        output = (
            {"invalid": True}
            if fallback_outcome == "repair" and len(groq_requests) == 1
            else extraction()
        )
        return httpx.Response(200, json={**completion(output), "model": "openai/gpt-oss-20b"})

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(scholarxiv), base_url="https://router.example"
        ) as primary,
        httpx.AsyncClient(
            transport=httpx.MockTransport(groq), base_url="https://groq.example"
        ) as fallback,
    ):
        adapter = ScholarxivAdapter(primary, allowed_models=["fixture-free-model"], max_tokens=2048)
        app = harness.app(
            None,
            stages=default_handlers(
                llm=adapter, fallback_llm=GroqAdapter(fallback, max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            source = window().model_copy(update={"groq_processing_approved": True})
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, source
                )
            terminal = await harness.wait_for_state(queued.job_id, "published", "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            if fallback_outcome == "quota":
                assert terminal.status.state == "failed"
                assert body["error"]["code"] == "EXTRACTION_UNAVAILABLE"
                assert len(groq_requests) == 1
                return
            assert terminal.status.state.value == "published"
            assert body["report"]["claims"][0]["original_text"] == source.observations[0].text
            assert len(groq_requests) == (2 if fallback_outcome == "repair" else 1)
            if fallback_outcome == "repair":
                assert body["report"]["processing_attempts"][1]["feedback"] == "not_applicable"


async def test_quota_admission_charges_shared_buckets_and_provider_cooldown_pauses_intake(
    harness, database_url
):
    from sqlalchemy import delete

    from services.models import provider_buckets
    from services.pipeline.extraction import ExtractionStage
    from services.pipeline.llm import GroqAdapter
    from services.provider_budgets import LlmAdmission, provider_statuses
    from services.providers.budget import TokenBucket
    from services.settings import Settings

    def scholarxiv(request):
        return httpx.Response(429, headers={"Retry-After": "120"})

    groq_requests = []

    def groq(request):
        groq_requests.append(request)
        return httpx.Response(200, json={**completion(extraction()), "model": "openai/gpt-oss-20b"})

    config = Settings(
        database_url=database_url,
        quotas_enabled=True,
        scholarxiv_api_key="synthetic-key",
        groq_extraction_enabled=False,
        _env_file=None,
    )
    names = [
        "scholarxiv",
        "groq_llm:requests_minute",
        "groq_llm:requests_day",
        "groq_llm:tokens_minute",
        "groq_llm:tokens_day",
    ]

    async def reset():
        async with harness.control.engine.begin() as connection:
            await connection.execute(
                delete(provider_buckets).where(provider_buckets.c.name.in_(names))
            )

    await reset()
    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(scholarxiv), base_url="https://router.example"
            ) as primary,
            httpx.AsyncClient(
                transport=httpx.MockTransport(groq), base_url="https://groq.example"
            ) as fallback,
        ):
            adapter = ScholarxivAdapter(
                primary, allowed_models=["fixture-free-model"], max_tokens=2048
            )
            groq_adapter = GroqAdapter(fallback, max_tokens=2048)
            handlers = dict(default_handlers(llm=adapter, fallback_llm=groq_adapter))
            handlers["claim_extraction"] = ExtractionStage(
                adapter, groq_adapter, LlmAdmission(config)
            ).run
            app = harness.app(None, stages=handlers)
            async with app.router.lifespan_context(app), harness.client(app) as client:
                investigation_id = await create_investigation(client)
                source = window().model_copy(update={"groq_processing_approved": True})
                async with harness.control.engine.begin() as connection:
                    queued = await enqueue_extraction(
                        connection, harness.queue, investigation_id, harness.owner.id, source
                    )
                terminal = await harness.wait_for_state(queued.job_id, "published", "failed")
                assert terminal.status.state.value == "published"
        assert len(groq_requests) == 1
        async with harness.control.engine.connect() as connection:
            # The Scholarxiv 429 held the account-wide bucket for its Retry-After and kept
            # the balance (999 after the one refused-by-provider request).
            assert 998.9 <= await TokenBucket.balance(connection, "scholarxiv", 1000) <= 1000
            assert 100 < await TokenBucket.held_seconds(connection, "scholarxiv") <= 120
            # The Groq fallback took one request and its token estimate from shared buckets.
            day = await TokenBucket.balance(connection, "groq_llm:requests_day", 900, 86400)
            assert 899 <= day < 899.5
            tokens = await TokenBucket.balance(connection, "groq_llm:tokens_day", 180000, 86400)
            assert tokens <= 180000 - 2048 - 256
            statuses = {s.provider: s for s in await provider_statuses(connection, config)}
        scholarxiv_status = statuses["scholarxiv"]
        assert scholarxiv_status.pauses_intake
        # Intake waits for the provider hold only, not for a drained budget.
        assert 100 < scholarxiv_status.retry_after_seconds <= 120
        assert not statuses["groq_llm"].pauses_intake
    finally:
        await reset()


async def test_units_return_when_the_request_record_loses_its_lease(
    harness, database_url, monkeypatch
):
    from sqlalchemy import delete

    from services.jobs.queue import JobQueue, LeaseLost
    from services.models import provider_buckets
    from services.pipeline.extraction import ExtractionStage
    from services.provider_budgets import LlmAdmission
    from services.providers.budget import TokenBucket
    from services.settings import Settings

    lost = []
    original = JobQueue.save_stage_data

    async def lose_request_record(self, lease, data, *, connection=None):
        # Fence the in-flight record written in the same transaction as the reservation.
        if connection is not None and data.get("in_flight") and not lost:
            lost.append(lease.job_id)
            raise LeaseLost("Injected lease fence while recording the request")
        await original(self, lease, data, connection=connection)

    monkeypatch.setattr(JobQueue, "save_stage_data", lose_request_record)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=completion(extraction()))

    # Two units per hour: a leaked unit could not refill before the bucket wait limit.
    config = Settings(
        database_url=database_url,
        quotas_enabled=True,
        scholarxiv_requests_per_hour=2,
        quota_provider_reserve=1,
        _env_file=None,
    )

    async def reset():
        async with harness.control.engine.begin() as connection:
            await connection.execute(
                delete(provider_buckets).where(provider_buckets.c.name == "scholarxiv")
            )

    await reset()
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(provider), base_url="https://router.example"
        ) as http:
            adapter = ScholarxivAdapter(
                http, allowed_models=["fixture-free-model"], max_tokens=2048
            )
            handlers = dict(default_handlers(llm=adapter))
            handlers["claim_extraction"] = ExtractionStage(adapter, None, LlmAdmission(config)).run
            app = harness.app(None, stages=handlers)
            async with app.router.lifespan_context(app), harness.client(app) as client:
                investigation_id = await create_investigation(client)
                async with harness.control.engine.begin() as connection:
                    queued = await enqueue_extraction(
                        connection, harness.queue, investigation_id, harness.owner.id, window()
                    )
                terminal = await harness.wait_for_state(queued.job_id, "published", "failed")
                assert terminal.status.state.value == "published"
        assert len(lost) == 1
        assert len(calls) == 1
        async with harness.control.engine.connect() as connection:
            # Fenced attempt refunded; only the request actually sent was spent.
            assert 0.99 <= await TokenBucket.balance(connection, "scholarxiv", 2) < 1.1
    finally:
        await reset()


async def test_feedback_failure_is_observable_without_hiding_the_repaired_result(harness, caplog):
    inference = []
    feedback = []

    def provider(request):
        payload = json.loads(request.content)
        if request.url.path == "/api/v1/router/feedback":
            feedback.append(payload)
            return httpx.Response(502)
        inference.append(payload)
        output = {"invalid": True} if len(inference) == 1 else extraction()
        return httpx.Response(200, json=completion(output))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        app = harness.app(None, stages=default_handlers(llm=adapter))
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            await harness.wait_for_state(queued.job_id, "published")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert len(body["report"]["claims"]) == 1
            assert feedback == [{"decision_id": "synthetic-decision-1", "feedback": "regenerated"}]
            assert "feedback_failed" in caplog.text
            assert len(inference) == 2
            repair = inference[1]["messages"][0]["content"]
            assert "Earlier response invalid: occurrences: missing; ?: extra_forbidden." in repair


@pytest.mark.parametrize("failure", ["timeout", "401", "403", "cooldown", "invalid-cooldown"])
async def test_feedback_outcomes_control_further_requests_honestly(harness, failure):
    inference = []

    def provider(request):
        if request.url.path == "/api/v1/router/feedback":
            if failure == "timeout":
                raise httpx.ReadTimeout("synthetic", request=request)
            if "cooldown" in failure:
                return httpx.Response(
                    429, headers={"Retry-After": "600" if failure == "cooldown" else "NaN"}
                )
            return httpx.Response(int(failure))
        inference.append(request)
        return httpx.Response(
            200, json=completion({"invalid": True} if len(inference) == 1 else extraction())
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
            # This test checks feedback outcomes, not lease timing. Under coverage on slow
            # runners the 0.5 s harness lease raced the strict renewal fence and was lost
            # mid-stage (unknown_outcome), flaking twice on #125; 2 s removes that race.
            job_lease_seconds=2,
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            if failure == "cooldown":
                async with asyncio.timeout(5):
                    while True:
                        record = await harness.queue.get(queued.job_id)
                        if record.retry_counts or record.status.state == "published":
                            break
                        await asyncio.sleep(0.02)
                assert (record.available_at - datetime.now(UTC)).total_seconds() > 590
                await client.post(f"/v1/jobs/{queued.job_id}/cancel")
                await harness.wait_for_state(queued.job_id, "cancelled")
                assert len(inference) == 1
            else:
                terminal = await harness.wait_for_state(queued.job_id, "failed", "published")
                body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
                if failure == "timeout":
                    assert terminal.status.state == "published"
                    assert (
                        body["report"]["processing_attempts"][0]["feedback"] == "feedback_unknown"
                    )
                    assert len(inference) == 2
                else:
                    assert terminal.status.state == "failed"
                    assert body["error"]["code"] == (
                        "EXTRACTION_UNAVAILABLE"
                        if failure == "invalid-cooldown"
                        else "EXTRACTION_DENIED"
                    )
                    assert len(inference) == 1


async def test_invalid_envelope_keeps_usable_feedback_identity(harness):
    inference = []
    feedback = []

    def provider(request):
        if request.url.path == "/api/v1/router/feedback":
            feedback.append(json.loads(request.content))
            return httpx.Response(204)
        inference.append(request)
        body = completion(extraction())
        if len(inference) == 1:
            body["choices"] = []
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            await harness.wait_for_state(queued.job_id, "published")
            report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
            assert feedback == [{"decision_id": "synthetic-decision-1", "feedback": "regenerated"}]
            assert report["processing_attempts"][0]["feedback"] == "feedback_sent"
            assert report["processing_attempts"][1]["repair"] is True


@pytest.mark.parametrize(
    "failure", ["permission", "authentication", "timeout", "invalid", "no-consent"]
)
async def test_restricted_or_invalid_extraction_never_escapes_to_groq(harness, failure):
    from services.pipeline.llm import GroqAdapter

    calls = []

    def provider(request):
        calls.append(request.url)
        if request.url.host == "groq.example":
            pytest.fail("Restricted, ambiguous or invalid work must not switch provider")
        if request.url.path == "/api/v1/router/feedback":
            return httpx.Response(204)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if failure == "invalid":
            return httpx.Response(200, json=completion({"invalid": True}))
        return httpx.Response(
            {"permission": 403, "authentication": 401, "no-consent": 429}[failure],
            headers={"Retry-After": "0"},
        )

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(provider), base_url="https://router.example"
        ) as primary,
        httpx.AsyncClient(
            transport=httpx.MockTransport(provider), base_url="https://groq.example"
        ) as fallback,
    ):
        app = harness.app(
            None,
            stages=default_handlers(
                llm=ScholarxivAdapter(
                    primary, allowed_models=["fixture-free-model"], max_tokens=2048
                ),
                fallback_llm=GroqAdapter(fallback, max_tokens=2048),
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            source = window().model_copy(
                update={"groq_processing_approved": failure != "no-consent"}
            )
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, source
                )
            await harness.wait_for_state(queued.job_id, "failed")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            expected = {
                "permission": "EXTRACTION_DENIED",
                "authentication": "EXTRACTION_DENIED",
                "timeout": "EXTRACTION_OUTCOME_UNKNOWN",
                "invalid": "EXTRACTION_INVALID",
                "no-consent": "EXTRACTION_UNAVAILABLE",
            }
            assert body["error"]["code"] == expected[failure]
            assert body["report"] is None
            assert all(url.host == "router.example" for url in calls)


@asynccontextmanager
async def pipeline(h, responses, *, faults=None, source=None, capture=False):
    requests = []

    def provider(request):
        if request.url.path == "/api/v1/router/feedback":
            return httpx.Response(200, json={})
        requests.append(json.loads(request.content))
        response = responses[min(len(requests) - 1, len(responses) - 1)]
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        app = h.app(None, stages=default_handlers(llm=adapter), faults=faults)
        async with app.router.lifespan_context(app), h.client(app) as client:
            investigation_id = await create_investigation(client, capture=capture)
            async with h.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, h.queue, investigation_id, h.owner.id, source or window()
                )
            yield client, investigation_id, queued, requests, app


async def test_exhausted_repair_is_a_visible_extraction_error_not_empty_success(harness):
    async with pipeline(harness, [completion({"invalid": True})]) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "failed")
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["state"] == body["processing_status"] == "failed"
        assert body["error"]["code"] == "EXTRACTION_INVALID"
        assert body["report"] is None
        assert len(requests) == 2


async def test_duplicate_json_keys_are_repaired_not_silently_accepted(harness):
    duplicate = completion({})
    duplicate["choices"][0]["message"]["content"] = '{"occurrences":[],"occurrences":[]}'
    async with pipeline(harness, [duplicate, completion(extraction())]) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "published")
        report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
        assert len(report["claims"]) == 1
        assert len(requests) == 2


@pytest.mark.parametrize(
    "case",
    [
        "missing-envelope",
        "truncated",
        "unknown-source",
        "bad-span",
        "unknown-context",
        "duplicate-span",
        "duplicate-occurrence",
        "normative-eligible",
        "question-eligible",
        "trailing-prose",
        "unclosed-thinking",
        "oversized-proposition",
    ],
)
async def test_invalid_output_gets_exactly_one_repair(harness, case):
    output = extraction()
    occurrence = output["occurrences"][0]
    response = completion(output)
    if case == "missing-envelope":
        response = {}
    elif case == "truncated":
        response["choices"][0]["finish_reason"] = "length"
    elif case == "unknown-source":
        occurrence["source_refs"][0]["observation_id"] = "invented"
    elif case == "bad-span":
        occurrence["source_refs"][0]["end_char"] = 999
    elif case == "unknown-context":
        occurrence["context_refs"] = [
            {**occurrence["source_refs"][0], "observation_id": "invented"}
        ]
    elif case == "duplicate-span":
        occurrence["source_refs"] *= 2
    elif case == "duplicate-occurrence":
        output["occurrences"] *= 2
    elif case == "normative-eligible":
        occurrence["taxonomy"] = "normative"
    elif case == "question-eligible":
        occurrence["assertion_mode"] = "questioned"
    elif case == "oversized-proposition":
        occurrence["proposition"] = "x" * 2001
    if case not in {"missing-envelope", "truncated", "trailing-prose", "unclosed-thinking"}:
        response = completion(output)
    if case == "trailing-prose":
        response["choices"][0]["message"]["content"] += " not JSON"
    elif case == "unclosed-thinking":
        response["choices"][0]["message"]["content"] = '<think>{"occurrences":[]}'
    async with pipeline(harness, [response, completion(extraction())]) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "published")
        report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
        assert report["claims"][0]["proposition"] == "The rate did not increase by 20%."
        assert len(requests) == 2
        assert "Regenerate once" in requests[1]["messages"][0]["content"]


@pytest.mark.parametrize(
    "crash_at", [Checkpoint.AFTER_ARTIFACT_STORE, Checkpoint.AFTER_PROVIDER_CALL]
)
async def test_hygiene_and_artifacts_survive_crash_without_repeating_provider_call(
    harness, crash_at
):
    h = harness
    calls = []
    response = completion(extraction())
    response["choices"][0]["message"]["content"] = (
        "<think>synthetic reasoning</think>\n```json\nPreamble\n"
        + json.dumps(extraction())
        + "\n```"
    )

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=response)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        handlers = default_handlers(
            llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        )
        app = create_app(h.settings())
        async with app.router.lifespan_context(app), h.client(app) as client:
            investigation_id = await create_investigation(client)
            async with h.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, h.queue, investigation_id, h.owner.id, window()
                )
            first = h.worker(
                None,
                stages=handlers,
                faults=ScriptedFaults(crash_at=crash_at),
            )
            await first.start()
            with pytest.raises(SimulatedCrash):
                await asyncio.wait_for(first.wait(), 5)
            before = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert before["report"] is None
            second = h.worker(None, stages=handlers)
            await second.start()
            if crash_at == Checkpoint.AFTER_PROVIDER_CALL:
                await h.wait_for_state(queued.job_id, "failed")
                body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
                assert body["report"] is None
                assert body["error"]["code"] == "EXTRACTION_OUTCOME_UNKNOWN"
                assert len(calls) == 1
                return
            await h.wait_for_state(queued.job_id, "published")
            report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
            assert report["version"] == 1
            assert len(report["claims"]) == len(calls) == 1
            record = await h.queue.get(queued.job_id)
            (published,) = await h.queue.published(record.key)
            assert published.result["attempts"][0]["thinking_leaked"] is True
            assert published.result["attempts"][0]["fenced"] is True
            assert "synthetic reasoning" not in json.dumps(published.result)
            async with h.control.engine.begin() as connection:
                replay = await enqueue_extraction(
                    connection, h.queue, investigation_id, h.owner.id, window()
                )
            assert replay.job_id == queued.job_id and not replay.created
            versions = (await client.get(f"/v1/investigations/{investigation_id}/reports")).json()
            assert len(versions["items"]) == 1


@pytest.mark.parametrize("action", ["cancel", "delete"])
async def test_late_extraction_cannot_publish_after_cancel_or_delete(harness, action):
    async def cancel_before_publish(name, job):
        if name == Checkpoint.BEFORE_PUBLISH:
            if action == "cancel":
                await harness.queue.request_cancel(job.id)
            else:
                await harness.queue.delete(job.id)

    faults = ScriptedFaults(on_checkpoint=cancel_before_publish)
    async with pipeline(harness, [completion(extraction())], faults=faults) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "cancelled", "deleted")
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["report"] is None
        assert len(requests) == 1
        record = await harness.queue.get(queued.job_id)
        assert await harness.queue.published(record.key) == []


@pytest.mark.parametrize(
    "kind", ["empty", "normative", "rejected-quote", "endorsed-quote", "quantity"]
)
async def test_empty_and_ineligible_results_are_provisional_not_verdicts(harness, kind):
    output = extraction()
    source = window()
    if kind == "empty":
        output["occurrences"] = []
    elif kind == "normative":
        source.observations[0].text = "The city should ban all cars."
        output["occurrences"][0].update(taxonomy="normative", eligibility_reason="opinion")
    elif kind in {"rejected-quote", "endorsed-quote"}:
        endorsement = "but I reject that claim." if kind == "rejected-quote" else "and I agree."
        source.observations[0].text = f'Alex said "The rate increased by 20%," {endorsement}'
        output["occurrences"][0].update(
            assertion_mode="reported",
            speaker_commitment="rejected" if kind == "rejected-quote" else "endorsed",
            eligibility_reason="quoted-not-endorsed"
            if kind == "rejected-quote"
            else "factual-claim",
            attributed_to="Alex",
        )
    elif kind == "quantity":
        source.observations[
            0
        ].text = "As of 2026-01-01, only 20 of 100 buses weigh more than 12 tonnes."
    if output["occurrences"]:
        output["occurrences"][0]["proposition"] = source.observations[0].text
        output["occurrences"][0]["source_refs"][0]["end_char"] = len(source.observations[0].text)
    async with pipeline(harness, [completion(output)], source=source) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "published")
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["processing_status"] == "partial"
        assert body["error"] is None
        assert body["report"]["provisional"] is True
        assert body["report"]["evidence"] == body["report"]["assessments"] == []
        assert len(body["report"]["claims"]) == (0 if kind == "empty" else 1)
        if kind != "empty":
            claim = body["report"]["claims"][0]
            expected = output["occurrences"][0]
            assert claim["original_text"] == claim["proposition"] == source.observations[0].text
            assert claim["interpretation"] == {
                key: value for key, value in expected.items() if key != "proposition"
            }
        assert len(requests) == 1


async def test_expired_publication_is_replayed_from_artifact_under_a_new_lease(harness):
    pauses = 0

    async def pause_before_publish(name, job):
        nonlocal pauses
        if name == Checkpoint.BEFORE_PUBLISH and job.key.stage == "claim_extraction":
            pauses += 1
            if pauses == 1:
                await asyncio.sleep(0.8)

    async with pipeline(
        harness,
        [completion(extraction())],
        faults=ScriptedFaults(on_checkpoint=pause_before_publish),
    ) as (client, investigation_id, queued, requests, _):
        await harness.wait_for_state(queued.job_id, "published")
        record = await harness.queue.get(queued.job_id)
        assert record.attempts == 2
        assert len(requests) == 1
        report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
        assert report["version"] == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_inference_keeps_lease_alive_and_observes_cancellation(harness, cancel):
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def respond(request):
        entered.set()
        try:
            await asyncio.sleep(1.2)
            return httpx.Response(200, json=completion(extraction()))
        finally:
            stopped.set()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url="https://router.example"
    ) as http:
        adapter = ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048)
        app = harness.app(None, stages=default_handlers(llm=adapter))
        async with app.router.lifespan_context(app), harness.client(app) as client:
            investigation_id = await create_investigation(client)
            async with harness.control.engine.begin() as connection:
                queued = await enqueue_extraction(
                    connection, harness.queue, investigation_id, harness.owner.id, window()
                )
            await asyncio.wait_for(entered.wait(), 5)
            if cancel:
                await harness.queue.request_cancel(queued.job_id)
                await asyncio.wait_for(stopped.wait(), 0.8)
            await harness.wait_for_state(queued.job_id, "cancelled" if cancel else "published")
            body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
            assert (body["report"] is None) is cancel


async def test_window_without_hosted_authorization_never_calls_provider(harness):
    source = window().model_copy(update={"hosted_processing_approved": False})
    async with pipeline(harness, [completion(extraction())], source=source) as (
        client,
        investigation_id,
        queued,
        requests,
        _,
    ):
        await harness.wait_for_state(queued.job_id, "failed")
        assert requests == []
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["processing_status"] == "failed"
        assert body["report"] is None


async def test_default_worker_fails_explicitly_when_extraction_is_not_enabled(harness):
    app = create_app(harness.settings(embed_worker=True))
    async with app.router.lifespan_context(app), harness.client(app) as client:
        investigation_id = await create_investigation(client)
        async with harness.control.engine.begin() as connection:
            queued = await enqueue_extraction(
                connection, harness.queue, investigation_id, harness.owner.id, window()
            )
        await harness.wait_for_state(queued.job_id, "failed")
        body = (await client.get(f"/v1/investigations/{investigation_id}")).json()
        assert body["error"]["code"] == "EXTRACTION_UNAVAILABLE"
        assert body["report"] is None


async def test_capture_polling_reads_real_provisional_claims_from_the_report(harness):
    source = window()
    source.observations[0].timebase = "capture"
    async with pipeline(harness, [completion(extraction())], source=source, capture=True) as (
        client,
        investigation_id,
        queued,
        requests,
        app,
    ):
        await harness.wait_for_state(queued.job_id, "published")
        report = (await client.get(f"/v1/investigations/{investigation_id}")).json()["report"]
        body = (await client.get(f"/v1/captures/{investigation_id}")).json()
        validate.Validator().validate(body, "capture-status.schema.json")
        assert body["claim_extraction_status"] == "partial"
        assert body["claims"] == [
            {
                "claim_id": report["claims"][0]["id"],
                "processing_status": "checking",
                "error": None,
            }
        ]
        assert report["fixture"] is False
        assert len(requests) == 1
        async with harness.client(app, outsider=True) as outsider:
            assert (await outsider.get(f"/v1/captures/{investigation_id}")).status_code == 404
