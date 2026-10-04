# BC-D03: Claim-extraction provider, hosting and the BE-08 development go

Also records the BE-01 reading of RFC-D31 (structural-validity gate for the
extraction route). One record, two identifiers.

**Question:** Which hosted model route extracts claims, which fallback follows
it, where does the backend run, and is the measured structural validity enough
to start BE-08 development against that route?

**Status:** Proposed. Authored from the BE-01 experiment by Neb-iyu on
4 October 2026; awaits confirmation by the product owner natnael-solomon.
Issue ownership does not grant authority over cross-cutting provider and
hosting choices, so nothing here is Accepted until that confirmation is
recorded with its date.

**Owner and participants:** Neb-iyu (BE-01 owner, research and pipeline)
proposed every choice below and ran the experiment. natnael-solomon (product
owner) is to confirm or reject. No sponsor or provider statement is on file.

## Options considered

1. Claim extraction on Scholarxiv `auto:cheap`, with Groq `openai/gpt-oss-20b`
   (`strict: true` JSON Schema) as the only fallback; Gemini and OpenRouter not
   selected.
2. The same cheap route with Gemini as a second fallback behind Groq.
3. Scholarxiv `auto:quality` or a pinned Scholarxiv candidate as the primary
   route, decided by the full 525-case comparison.
4. Hosting on EthioDeploy Free (one web service with the embedded worker and
   the Postgres add-on) versus deferring the hosting choice until a paid or
   sponsored tier is confirmed.
5. For BE-08: a limited development go on the measured route now, or holding
   BE-08 until the comparison completes and semantic accuracy is measured.

## Evidence and uncertainties

### Measured structural validity (uncommitted local run)

The fixed `be01-experimental-v2` schema and `be01-window-only-v2` prompt were
run on 50 authorized real development windows from seven sources, temperature
0, `max_tokens: 8192`. Windows overlap and source/topic families are
correlated; these are not 50 independent clips or a holdout evaluation.
Headline figures: `auto:cheap` 44/50 first-pass and 48/50 (96 percent)
post-single-repair structural validity across the 50 baseline windows; the
`/no_think` suffix gave no improvement (21/25 against 25/25 on the matched
subset); `auto:quality` returned HTTP 200 for only 9 of 50 baseline cases;
pinned routes recorded no HTTP responses in that cohort; a separate recovery
cohort measured GPT-OSS via Scholarxiv at 16/21 post-repair. The complete cheap
baseline used 56 requests and 123,122 reported tokens with case p50/p95 latency
of 2.35/10.90 s including repair. The full table is in the
[backend README](../../backend/README.md#measured-result-local-run).

Every figure above comes from an uncommitted local run whose recordings live
under the ignored `.scratch/router/<run-id>/` directory (manifest,
`attempts.jsonl`, `results.json`, `summary.json`). Nothing in the repository
lets a reviewer verify them. Before this record moves from Proposed to
Accepted, either a redacted metrics-only `summary.json` (no transcripts,
requests or responses) or the run id together with the manifest SHA-256 and
summary SHA-256 must be committed and linked here.

Semantic review of structurally valid output found missed claims, incorrect
rejection and negation handling, and hypothetical context promoted to fact.
Quote-reference and prompt-revision pilots did not show a consistent held-aside
improvement and were not adopted; their provisional reference labels are
agent-authored, not independent human gold. The 435 transport failures in the
original plus first continuation, and the 399 cases left unattempted by the
final recovery, are deferred, not passed. Earlier synthetic runs used a
2048-token ceiling and cannot be pooled with these measurements.

### Direct Groq precheck

A current-contract synthetic precheck made five HTTP requests to Groq: four
200s and one 429. Negation passed; a rejected-quotation response failed span
bounds and its repair was rate-limited (an 8,000 TPM rejection was observed).
After the cooldown a separately recorded continuation repaired the bounds and
tested hypothetical context. Both passed structural checks but misrepresented
source meaning: the rejection was lost, its citation narrowed to an
insufficient fragment, and explicit hypothetical context was ignored. Access
and schema compatibility are verified; semantic readiness is not.
[Groq's structured-output support](https://console.groq.com/docs/structured-outputs)
is not a semantic-quality guarantee.

### Account and entitlement evidence, checked 3 and 4 October 2026

| Item | Observation | Remaining limitation |
| --- | --- | --- |
| Scholarxiv keys and profile | Neb-iyu reports separate backend and experiment keys and no Free dashboard model-selection controls | Account-wide cost ceiling not established; self-reported configuration, not a dashboard audit |
| Router plan boundary | Authenticated cheap completion succeeded; paid-model probe returned 403 | Historical access does not guarantee future availability or remaining quota |
| Undocumented JSON mode | Compared with and without `response_format: {"type":"json_object"}`; fenced output still occurred | Unsupported behaviour, not a correctness guarantee or production dependency |
| Scholarxiv Papers | Authenticated title and advanced search succeeded; federated search returned 403 on Free | Live federated partial failure cannot be reproduced with this account |
| Groq strict output | Current v2 schema accepted by GPT-OSS 20B; local validation caught a span error | Strict JSON does not guarantee grounding, rejection stance or hypothetical framing |
| Groq Whisper | `whisper-large-v3` and `whisper-large-v3-turbo` each returned HTTP 200 with a nonempty transcript and seven timestamped segments for one 31.819 s authorized non-social development excerpt (about 1.02 MB, 16 kHz mono WAV, CC BY 4.0 attribution retained locally); both matched the 90-word subtitle reference after case and punctuation normalization except `favor` versus `favour`; request times 11.2 s and 14.8 s | English sample only; subtitle agreement, not certified WER; no model speed ranking; the [documented](https://console.groq.com/docs/speech-to-text) 25 MB Free upload cap, dated Limits dashboard, no-card status and audio quotas not verified |
| Sponsor offers | Public STARK sponsor and prize pages advertise participant Voxide sessions; Scholarxiv and EthioDeploy paid perks are winner offers | Credit allocation, expiry and restrictions not established; do not budget winner perks as current Free entitlement |

The [Papers federated-search documentation](https://www.scholarxiv.com/developers/docs/papers-api/federated-search.md)
states that a failed source reports `{count: 0, hasMore: false}` instead of
failing the request, which is indistinguishable from a genuine empty result.
Do not infer "no evidence exists" from it. A provider-confirmed replay or an
authorized Go+ account is needed for live reproduction; a mocked failure is
client-test evidence only.

Open-access retrieval routes have positive access evidence with per-article
licensing: [arXiv 2501.10868](https://arxiv.org/abs/2501.10868) abstract and
PDF (HTTP 200, bounded complete download with signature and EOF checks;
article is CC BY 4.0, not a universal arXiv license);
[Europe PMC PMC3258128 full text](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC3258128/fullTextXML)
(HTTP 200, article body with 40 paragraph tags; CC BY-NC 3.0, attribution and
noncommercial restrictions apply); Unpaywall lookup for DOI
`10.1093/nar/gkr715` (HTTP 200, OA location and publisher PDF link, using an
authorized contact email; `cc-by-nc`). Metadata discovery is not proof that
every linked PDF is accessible or reusable.

### Hosting evidence

EthioDeploy's background-work and quota answers were relayed by Neb-iyu, not
independently authenticated support correspondence:

- One Free project; web container 256 MB RAM / 0.5 CPU; 50 GB-hours and
  20 CPU-hours per month.
- The web service sleeps after 30 minutes without incoming HTTP. Outbound
  requests and CPU work do not reset the timer; sleep kills in-progress jobs.
  Polling resets the timer but is not a durability guarantee.
- Postgres is separate compute: 256 MB RAM / 0.25 CPU and 512 MB total
  storage, not charged against the web compute quota.
- Public [billing docs](https://ethiodeploy.com/docs/billing) state that Free
  quota exhaustion stops a project until the next month.

The HTTP body limit, HTTP timeout, region and web disk allowance are
unanswered; no default is assumed. Project and add-on provisioning,
deployment and hosted recovery of the durable job engine are not verified.

## Chosen option and rationale

Proposed by Neb-iyu, pending confirmation:

| Stage | Selected order | Boundary |
| --- | --- | --- |
| Claim extraction | Scholarxiv `auto:cheap`, then Groq `openai/gpt-oss-20b` with `strict: true` JSON Schema | Groq is the only selected fallback. It accepts the experimental schema but still makes semantic and source-reference errors. Gemini was removed from scope by Neb-iyu on 4 October 2026; there is no second fallback. |
| Speech-to-text | Groq Whisper candidates, separately from the claim fallback chain | Both candidates transcribed the authorized excerpt. This does not select an ASR fallback chain. |
| Hosting | EthioDeploy Free web service with embedded background work and the Postgres add-on | Selected by Neb-iyu on 4 October 2026 on the relayed quota answers; provisioning and deployment are not verified. |

BE-08 development go (RFC-D31 reading): Neb-iyu accepted a limited go for
BE-08 development on 4 October 2026, using the proposed gate of at least
90 percent post-single-repair structural validity on 50 real baseline windows
per route, the fallback order above, and explicit deferral of the unfinished
model comparison. The cheap route met the gate (48/50). This does not approve
production accuracy or declare every BE-01 check complete. Historical run
summaries keep `decision: pending_team_approval`; this record supplements them
rather than rewriting them.

Rationale: the cheap route is the only route with a complete 50-window
measurement that clears the structural gate on the shared Free account; Groq
is the only other provider whose strict JSON mode accepted the schema with a
working key; one Free hosting project with embedded work matches the durable
job engine already built and avoids a paid commitment before the deadline.

## User-visible consequences

None until BE-08 ships. When it does, a route that is exhausted or
unavailable must surface as an error, never as an empty successful
extraction, and the report must say which provider and model answered. The
web service sleeping after 30 idle minutes means a job that is still running
when the service sleeps is killed and must be recovered on the next start;
the person checking sees a delayed or failed check, not a silent loss.

## Technical, privacy, cost and evaluation consequences

- Provider selection alone does not authorize sending real transcripts to
  another provider; hosted processing still needs the rights and consent
  steps in the evaluation contract.
- No provider switching has been added to the API, worker or the fixed
  Scholarxiv comparison runner; adapter orchestration belongs to BE-08, which
  must validate JSON, required fields, source ids, roles and spans, finish
  reason and semantic fidelity independently of provider guarantees, bound
  repairs and retries, respect rate-limit cooldowns and retain failure
  diagnostics.
- Groq serving both ASR and claim extraction leaves both stages dependent on
  one provider.
- Request and token counts are quota proxies only; billed cost and remaining
  shared quota are not established. A singleton `models` request is a
  per-request selection, not an account-wide billing ceiling.
- The web container must use external inference, not local Whisper or LLM
  weights; job state and checkpoints must be persisted, processing must be
  idempotent, and unfinished jobs must be recovered on startup. A job under
  30 minutes is not protected from crashes, OOM, restarts or quota exhaustion.
- Structural validity does not measure claim correctness, semantic fidelity,
  extraction recall, timestamps or evidence quality; those remain RES-03
  measurements against the frozen corpus.

## Dependencies / capability gates

- Confirmation by natnael-solomon, recorded here with the date.
- Committed verification evidence for the measured figures (see Evidence and
  uncertainties) before the status moves to Accepted.
- Issue #11 (BE-01) was closed on 4 October 2026 ahead of this PR's merge,
  at Neb-iyu's direction; the owner decides whether it stays closed.
- BE-08 (#21) builds the adapters; BE-03 (#15) owns the production contract,
  which this experiment schema does not change; RES-03 measures semantic
  accuracy; hosting entitlement and deployment are #21 and the pending
  BC-D03 confirmation, not this record.

## Rejected alternatives and why

Gemini as a second fallback: removed from scope by Neb-iyu on 4 October 2026
without account, model or schema checks, to limit the provider surface before
the deadline. OpenRouter: not evaluated. `auto:quality` or a pinned route as
primary: only nine HTTP 200 responses and no pinned-route responses in the
measured cohorts, so no quality ranking exists; the comparison is deferred,
not decided against them. Holding BE-08 until the comparison completes: the
cheap route already clears the structural gate and the deadline leaves no
room for a second full matrix before adapter work starts. Deferring hosting:
the durable job engine needs a target with known sleep and quota behaviour to
be designed against.

## What evidence would reverse this decision

A committed metrics summary that does not reproduce the 48/50 result; a
Scholarxiv plan or quota change that removes Free access to `auto:cheap`; a
Groq limit that blocks strict-mode extraction at the needed rate; an
EthioDeploy answer on body limit, timeout or disk that the pipeline cannot fit;
a completed comparison showing another route clearly better on the same
windows; or a semantic-accuracy measurement on the frozen corpus below what
the product promises. Any of these reopens the provider or hosting row rather
than the whole record.

## Links

Build contract section 5 (BC-D03) and section 4; RFC-D31, RFC-D41 to RFC-D43;
[0001](0001-confirmed-product-scope.md); [BC-D04](BC-D04-voxide-route.md) for
the Voxide route, which the #11 checklist also cites but which this record does
not redefine; [backend README, BE-01 router experiment](../../backend/README.md#be-01-router-experiment);
`backend/services/experiments/`; `backend/tests/test_router_experiment.py`;
`backend/tests/test_extraction_contract.py`; issues #11, #15, #21; PR #84.
