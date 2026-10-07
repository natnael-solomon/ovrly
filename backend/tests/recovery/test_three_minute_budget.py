"""Offline accounting proof for a controlled three-minute input (BE-08, #25).

``CANDIDATE`` is a measured candidate envelope, not verified free-plan capacity:
provider quota windows remain unverified, so no live route is activated.
Reserved units are serialized input bytes + output cap + 256 per request (feedback is
counted too). This deliberately overestimates provider tokens; it is not a tokenizer.
"""

import asyncio
import json

import httpx
import pytest

from recovery.test_claim_extraction import completion, create_investigation, extraction, window
from recovery.test_reconciliation import unchanged
from services.jobs.handlers import default_handlers
from services.pipeline.incremental import ExtractionPolicy, submit_observations
from services.pipeline.llm import ScholarxivAdapter

# Reference density: 36 contiguous 5 s speech observations (180 s), 14 words each
# (168 words per minute). Duration alone does not bound density; see the dense case.
SENTENCE = "Officials said the regional rate rose by two percent over the last year here."
CANDIDATE = ExtractionPolicy(
    max_requests=24,
    max_tokens=196500,
    reconciliation_requests=3,
    reconciliation_tokens=102500,
    batch_observations=6,
    overlap_observations=1,
    max_observations=64,
)


def observations(text):
    return [
        window()
        .observations[0]
        .model_copy(
            update={
                "id": f"speech-{index + 1:02d}",
                "text": text,
                "start_ms": index * 5000,
                "end_ms": (index + 1) * 5000,
            }
        )
        for index in range(36)
    ]


def respond(repairs, request):
    body = json.loads(request.content)
    key = body["messages"][1]["content"]
    source = json.loads(key)
    if repairs is not None and key not in repairs:
        repairs.add(key)
        return httpx.Response(200, json=completion({"invalid": True}))
    if body["model"] == "auto:quality":
        return httpx.Response(200, json=completion(unchanged(source)))
    template = extraction()["occurrences"][0]
    return httpx.Response(
        200,
        json=completion(
            {
                "occurrences": [
                    {
                        **template,
                        "proposition": item["text"][:200],
                        "source_refs": [
                            {
                                "observation_id": item["id"],
                                "start_char": 0,
                                "end_char": len(item["text"]),
                            }
                        ],
                    }
                    for item in source["observations"]
                ]
            }
        ),
    )


async def run(harness, text, *, repair):
    from services.pipeline.reconciliation import request_reconciliation

    repairs = set() if repair else None
    calls = []

    def provider(request):
        if request.url.path == "/api/v1/router/feedback":
            calls.append("feedback")
            return httpx.Response(204)
        calls.append(json.loads(request.content)["model"])
        return respond(repairs, request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), base_url="https://router.example"
    ) as http:
        app = harness.app(
            None,
            job_lease_seconds=5,
            stages=default_handlers(
                llm=ScholarxivAdapter(http, allowed_models=["fixture-free-model"], max_tokens=2048),
                reconciliation_llm=ScholarxivAdapter(
                    http,
                    allowed_models=["fixture-free-model"],
                    max_tokens=8192,
                    task="reconciliation",
                ),
            ),
        )
        async with app.router.lifespan_context(app), harness.client(app) as client:
            identifier = await create_investigation(client)
            source = observations(text)
            queued = []
            # Producer cadence: six observations (30 s) per admission, then close.
            for start in range(0, 36, 6):
                async with harness.control.engine.begin() as connection:
                    queued += await submit_observations(
                        connection,
                        harness.queue,
                        identifier,
                        harness.owner.id,
                        policy=CANDIDATE,
                        observations=source[start : start + 6],
                        closed=start == 30,
                        hosted_processing_approved=True,
                    )
            for item in queued:
                await harness.wait_for_state(item.job_id, "published", "failed")
            extracted = (await client.get(f"/v1/investigations/{identifier}")).json()
            async with harness.control.engine.begin() as connection:
                await request_reconciliation(
                    connection, identifier, harness.owner.id, accepted_jobs=[]
                )
            async with asyncio.timeout(30):
                while True:
                    body = (await client.get(f"/v1/investigations/{identifier}")).json()
                    if body.get("reconciliation_progress", {}).get("status") in {
                        "complete",
                        "failed",
                    }:
                        return calls, extracted["extraction_progress"], body
                    await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    "repair,extraction_use,total_use,calls_expected",
    [
        # Six windows, then one whole-input quality request.
        (False, (6, 44862), (7, 94553), 7),
        # Bounded worst case: every completion is invalid once, so each window and the
        # reconciliation spends completion + regenerated feedback + one repair.
        (True, (18, 92262), (21, 192085), 21),
    ],
)
async def test_three_minute_input_fits_candidate_envelope_with_headroom(
    harness, repair, extraction_use, total_use, calls_expected
):
    calls, extracted, body = await run(harness, SENTENCE, repair=repair)
    progress = body["extraction_progress"]
    assert len(calls) == calls_expected
    assert (extracted["requests_used"], extracted["tokens_reserved"]) == extraction_use
    assert (progress["requests_used"], progress["tokens_reserved"]) == total_use
    extraction_allowance = (
        CANDIDATE.max_requests - CANDIDATE.reconciliation_requests,
        CANDIDATE.max_tokens - CANDIDATE.reconciliation_tokens,
    )
    reconciliation_use = (total_use[0] - extraction_use[0], total_use[1] - extraction_use[1])
    # Unrounded headroom; the worst case leaves 3 requests / 1738 units for extraction,
    # 0 requests / 2677 units inside the reserve and 3 requests / 4415 units overall.
    assert extraction_allowance[0] - extraction_use[0] >= 3
    assert extraction_allowance[1] - extraction_use[1] >= 1738
    assert CANDIDATE.reconciliation_requests - reconciliation_use[0] >= 0
    assert CANDIDATE.reconciliation_tokens - reconciliation_use[1] >= 2677
    assert body["reconciliation_progress"]["status"] == "complete"
    assert body["report"]["reconciliation"]["coverage_limited"] is False
    assert all(item["status"] == "processed" for item in progress["observations"])
    assert len(body["report"]["claims"]) == 36


async def test_denser_three_minute_input_discloses_unprocessed_intervals(harness):
    _, extracted, body = await run(harness, " ".join([SENTENCE] * 24), repair=False)
    states = [(item["status"], item["reason"]) for item in extracted["observations"]]
    assert set(states) <= {("processed", None), ("skipped", "budget_exhausted")}
    assert ("skipped", "budget_exhausted") in states
    assert extracted["requests_used"] <= CANDIDATE.max_requests - CANDIDATE.reconciliation_requests
    assert extracted["tokens_reserved"] <= CANDIDATE.max_tokens - CANDIDATE.reconciliation_tokens
    outcome = body["reconciliation_progress"]
    if outcome["status"] == "complete":
        assert body["report"]["reconciliation"]["coverage_limited"] is True
    else:
        assert outcome["error"]["code"]
