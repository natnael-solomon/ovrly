# Backend

Python 3.11 / uv / FastAPI foundation with PostgreSQL, Alembic and a shared worker
lifecycle. No research, intake, authentication, uploads or durable jobs are
implemented yet. The worker is explicitly idle: it proves startup, supervision
and shutdown, not processing or lease draining.

## Local setup (Linux / WSL)

Install [uv](https://docs.astral.sh/uv/) and Docker with Compose, and start Docker.
Use Linux tools and a Linux checkout in WSL. From the repository root:

```sh
sh scripts/backend.sh
```

This command resolves paths from the script, provisions Python 3.11 through uv,
installs `uv.lock` with `--frozen`, starts **only PostgreSQL** in Docker, waits for
it, applies migrations, and runs the API with the embedded worker on
`127.0.0.1:8000`. It does not build an API image, start a reloader, call providers,
download models/media, or deploy anything. In another terminal:

```sh
curl --fail http://127.0.0.1:8000/healthz
```

Ready returns `200 {"status":"ok"}`. An unavailable database or stopped/failed
embedded worker returns a safe `503` with reason `database` or `worker`.
Probes have a bounded database timeout; no provider is contacted. When the
embedded worker is disabled, readiness does not claim to monitor a separate
worker process. Database failure during embedded-worker startup prevents API
startup rather than pretending the worker started.

Ctrl+C stops the API and its embedded worker. PostgreSQL stays running and its
named volume persists. Stop this project's database without deleting data:

```sh
cd backend
docker compose stop db
```

Do not run `down --volumes` unless you intend to delete the development database.
The helper never prunes Docker or deletes database data. The image and Python
caches remain after shutdown. The development database is not a shared/team or
production database.

### Configuration and storage

The helper uses `backend/.env` when present, otherwise the committed
`.env.example` with **local-only** credentials. Environment variables take
precedence. To customize:

```sh
cd backend
cp .env.example .env
```

Keep `.env` ignored. Never use the example password outside loopback development.
If changing database port/password, update both the Compose settings and
`OVRLY_DATABASE_URL` together. PostgreSQL initializes credentials only for a new
volume; editing an environment variable does not change an existing database
password. Do not delete a volume to resolve that without reviewing its data.

| Setting | Default / meaning |
| --- | --- |
| `OVRLY_DATABASE_URL` | Required PostgreSQL `postgresql+psycopg://` URL; local example uses port 55432 |
| `OVRLY_POSTGRES_PORT` | Compose loopback port, 55432 |
| `OVRLY_POSTGRES_PASSWORD` | Compose initialization password; local example only |
| `OVRLY_API_PORT` | Development helper's loopback API port, 8000 |
| `OVRLY_EMBED_WORKER` | Off for ordinary API startup; helper explicitly enables it |
| `OVRLY_DATABASE_TIMEOUT_SECONDS` | 3; positive, at most 30 |
| `OVRLY_WORKER_SHUTDOWN_SECONDS` | 5; positive, at most 30 |

The helper checks for at least 2 GiB free on the checkout filesystem before and
after dependency/image setup and after database startup. It stops further work if
space is low; it does not reserve space, predict every download's expanded size,
roll back downloads, or monitor unrelated filesystems containing custom caches.
Keep additional headroom and inspect `df -h .` and `docker system df` as data grows.
Use of PostgreSQL-only Docker avoids an API image/build cache. No Android SDK is
needed for backend work.

### Separate processes and migrations

The API and worker use the same package, settings and database helper. From
`backend/`, use the local example below (replace `.env.example` with `.env` for
custom settings):

```sh
docker compose --env-file .env.example up -d --wait db
uv sync --frozen
uv run --frozen --env-file .env.example alembic upgrade head
uv run --frozen --env-file .env.example alembic current
uv run --frozen --env-file .env.example alembic check

# API-only terminal; explicit 0 overrides any configured embedded-worker setting.
OVRLY_EMBED_WORKER=0 uv run --frozen --env-file .env.example \
  uvicorn services.api.main:create_app --factory --host 127.0.0.1 --port 8000

# Separate terminal, same working directory/environment:
uv run --frozen --env-file .env.example python -m services.worker
```

Do not start the standalone worker alongside an embedded worker for the same
development session. The standalone entry point handles SIGINT/SIGTERM on
Linux/WSL; native Windows signal handling is not implemented. Both modes close
owned database resources. Worker failures are visible and are not automatically
restarted. The current baseline only establishes Alembic history; it intentionally
does not create product/job tables. Future schema changes require a reviewed
migration and upgrade/downgrade coverage, not `create_all()` during API startup.

## Local checks

From `backend/` with the local PostgreSQL service running:

```sh
uv sync --frozen
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy --strict services/
OVRLY_TEST_DATABASE_URL='postgresql+psycopg://ovrly:local-development-only@127.0.0.1:55432/ovrly' \
  uv run --frozen pytest -q
```

Tests require an explicitly configured **loopback** PostgreSQL role permitted to
create databases. They create a uniquely named `ovrly_test_*` database and drop
only that database afterward, including on failure. They never reset the
configured development database. Missing test configuration/database access is
an error, not a successful skip. Override the test URL if local settings differ.

Put the host and database in the URL authority/path, not query overrides.
`dbname`, `database`, `host`, `hostaddr`, `service` and `servicefile` query
parameters are rejected before connecting. Unset `PGHOSTADDR`, `PGSERVICE`
and `PGSERVICEFILE`; inherited routing must not redirect disposable tests.
Ordinary options such as `sslmode` remain supported.

Coverage includes real PostgreSQL readiness, migration round trips and drift,
safe failure responses, both worker modes, signal shutdown, cancellation and
resource cleanup, and startup-helper negative paths. No provider keys or
personal media are needed.

Ruff includes security rules (`S`) and rejects bare `type: ignore` comments
(`PGH003`); MyPy also enables `ignore-without-code`. Only pytest's `S101`
assertion rule is ignored under `tests/`. Two fixed test subprocess calls have
line-specific, explained `S603` annotations; production code has no security-rule
exemptions.

The optional `quality` group adds locked pre-commit, actionlint, zizmor and
pip-audit tooling without changing runtime dependencies. From the repository root:

```sh
uv sync --project backend --frozen --group quality
uv run --project backend --frozen --group quality pre-commit run --all-files
```

This runs both stacks' quality gates, workflow analysis, dependency audits and
enforcement fixtures without PostgreSQL. Full repository checks require the
Android JDK/SDK too. The same command runs in **Quality checks**, including on
docs-only PRs. It does not replace PostgreSQL/migration/coverage checks.
See [WORKFLOW](../WORKFLOW.md#shared-quality-gates) for setup and tool pins.
The shared hooks also scan reachable Git history and tracked changes for secrets,
using a checksum-pinned native Gitleaks binary; no provider receives source code.

To audit only backend dependencies:

```sh
uv run --project backend --frozen --group quality python .github/scripts/dependency_audit.py backend
```

pip-audit checks a hashed export of the frozen lockfile, including development and
quality groups, without resolving or installing the audited packages. It queries
PyPI with package names/versions. OSV additionally checks the universal lockfile
alongside Android metadata. Findings, incomplete reports and service errors fail
the gate; no advisories are currently ignored.

### CI and coverage

**Backend CI** runs on PRs to any branch (including stacked targets and
retargeting), pushes to `main`, and manual dispatch. A lightweight job always
tests change detection and coverage policy. Only entirely known-documentation
diffs skip backend execution; unknown paths, evaluation, Android and tooling
changes conservatively run it. The stable **Backend checks** result fails if
detection or required validation fails/is cancelled; docs-only skips still
produce that check. PostgreSQL and Python setup are not started for docs-only
changes in this Backend CI workflow; the separate Quality checks job still runs.

The validation job uses Python 3.11, pinned setup-uv, the frozen lockfile, Ruff,
strict MyPy with the Pydantic plugin, PostgreSQL 16, migration upgrade/drift
checks and the real test suite. Only pushes to `main` save uv caches; PRs can read
them. Dependabot checks the `/backend` uv project weekly with grouped minor/patch
updates. There are no production secrets or live providers in this workflow.

Run the same test/coverage pipeline locally after fetching `origin/main`, from
`backend/`:

```sh
git fetch origin main
OVRLY_TEST_DATABASE_URL='postgresql+psycopg://ovrly:local-development-only@127.0.0.1:55432/ovrly' \
  uv run --frozen python ../.github/scripts/backend_checks.py
```

This runs tests under coverage.py, including spawned Python workers, and writes
ignored `reports/junit.xml`, `coverage.xml`, `coverage.json`, `summary.md` and
`comparison.json`. CI uploads these as `backend-reports` for seven days, along
with available baseline reports, even after failure. Raw coverage databases are
not uploaded. Scope is all `services/**/*.py` with no service-file exclusions:
tests and migration scaffolding are outside the service denominator. The runner
rejects reports missing service files or containing inconsistent line counts.

For PRs/manual/local runs the comparison target is the fetched `origin/main`
commit, **not** the parent feature branch. Main pushes compare to the previous
main commit, not themselves. The runner creates a disposable detached worktree,
uses that commit's locked dependencies/tests and the current run's exact coverage
version/configuration, then removes only its own worktree. It does not mutate
your checkout, contact providers or trust a stale/missing artifact as a baseline.
It checks the 2 GiB headroom before measurement and before installing baseline
dependencies; baseline caches can remain in uv's cache.

The overall line-coverage regression limit is a drop of **at most 1 percentage
point**, calculated from exact counts without rounding. If main genuinely has
no backend manifest yet, the report says the baseline is unavailable; it does
not claim the regression check passed. Unknown refs, failing baseline tests,
missing reports or source without a manifest fail instead of taking that path.

The current `services/worker` tree is held to **90% line coverage**, including
the standalone entry point. `services/api/auth` and `services/contracts` have
future 90% floors (module files and package directories supported); absence is
shown as **not implemented / not evaluated**, not 100%. Their eventual locations
must be confirmed when those tasks land. Android coverage is not part of this
denominator and must not be inferred from backend results.

Remaining [REPO-04 / #13](https://github.com/natnael-solomon/ovrly/issues/13) work:
Android unit/instrumented reports and capture/share floors depend on the emulator
work in #37; auth/contracts/job-engine coverage must be verified on their real
implementations; a maintainer must add **Backend checks** to the main ruleset
after the workflow lands and reports successfully. This PR does not change
protection settings or complete the whole issue.

Durable jobs, leases and drain/recovery assertions remain
[BE-04 / #16](https://github.com/natnael-solomon/ovrly/issues/16).

## BE-01 router experiment

`uv run --frozen python -m services.experiments.router` is an isolated research
runner, never started by the API or worker. Its tests use HTTP mocks, not provider
keys. It prepares the experiment in [BE-01 / #11](https://github.com/natnael-solomon/ovrly/issues/11);
it does **not** establish free-tier entitlements or complete that issue.
The existing locked HTTPX dependency is also a runtime dependency for this CLI.

### Inputs and approval

Store inputs under ignored root `.scratch/`, not in evaluation fixtures.
The input is one JSON object:

```json
{
  "schema_version": "be01-experimental-v2",
  "version": "pilot-v2",
  "kind": "synthetic",
  "split": "dev",
  "hosted_processing_approved": false,
  "provenance": "Invented local example; not real transcript evidence",
  "windows": [
    {
      "window_id": "window-one",
      "context_status": "window-only",
      "observations": [
        {
          "id": "segment-one",
          "role": "target",
          "text": "The sample contains ten seeds.",
          "source_type": "supplied-caption",
          "speaker_id": null,
          "envelope": {
            "start_ms": 0,
            "end_ms": 5000,
            "basis": "user-timed-caption-not-media-verified"
          }
        }
      ]
    }
  ]
}
```

Provide 1-50 windows with unique window IDs and distinct target transcript text.
Each window needs at least one target observation and unique observation IDs.
Reused observation IDs across windows must retain identical text/source metadata;
their role may differ. IDs identify supplied observations, not retrieved papers.
`source_type` is `supplied-caption` or `source-subtitle`; these experiment inputs
do not establish that ASR ran or that captions agree with media. The example's
text and timing are invented, not a real recording or timing measurement.

`speaker_id` preserves a supplied speaker label or is explicitly null; do not
infer a person's name. `envelope` has an exclusive end greater than its start.
Timing bases are `user-timed-caption-not-media-verified`,
`coarse-parent-envelope-not-subwindow-timing`, or
`source-subtitle-cue-not-media-verified`. Subdividing a coarse block does not
justify narrowing its time envelope.

Keep the initial 50-window comparison `window-only`: do not silently append
previous passages or guessed visual context. The fixed prompt flags missing
context rather than inventing it. A separately versioned experiment may supply
observations with `role: context` and `context_status: additional-context-supplied`.
Context assists interpretation but is not another extraction target.

`split` must be `dev`: never feed the frozen holdout or its labels to prompt
tuning. The two invented few-shot examples are embedded in the fixed prompt,
separate from evaluation material. Use `kind: real` only for actual transcript
windows, with provenance identifying the approved dataset/version and selection
method. Select short/simple through long/many-claim windows. The repository's
RES-06 draft has neither full transcripts nor hosted-processing clearance;
do not turn its normalized annotations into purported transcript data.

Version 2 replaces the old `transcript`/`evidence_ids` input and `claims` output.
The runner rejects v1 or unversioned inputs; there is no silent conversion.
To migrate locally, preserve the original snapshot, construct observations from
its actual source spans, verify exact text reconstruction, and write a new
dataset version with `schema_version: be01-experimental-v2`. Keep the original
source hashes, parent envelopes, family/overlap records and permission status
in the accompanying private provenance. Do not fabricate fine-grained timing.

`hosted_processing_approved: true` is an operator attestation, not automated
rights verification. Obtain permission for the selected provider's processing
and retention terms before setting it. Plan mode accepts unapproved inputs and
does not read a key, create recordings or contact any provider.

### Plan, then explicitly execute

From `backend/`, after saving the input as root `.scratch/be01-windows.json`:

```sh
uv run --frozen python -m services.experiments.router ../.scratch/be01-windows.json
```

For 50 windows the matrix has **525 cases / at most 1,050 requests**:
350 baseline cases across `auto:cheap`, `auto:quality` and five pinned candidates,
plus 175 paired `/no_think` cases on alternating input windows (25 per route).
Input ordering determines the paired subset. Pinning uses `model: auto:cheap`
with a singleton `models` list; an unexpected executor fails that attempt.
Only documented request fields are used. No `response_format`, tools, feedback,
provider fallback, automatic HTTP retry or production integration is enabled.

Before executing, use a dedicated experiment key and verify the account's plan
and available spending controls. The team reports that its Free account has no
dashboard model-selection controls; do not require or claim a nonexistent
allow-list. A singleton `models` request is a per-request selection, not an
account-wide billing ceiling. Keep subscription-backed Free access; do not
enable self-funded routing or upgrade billing as an experiment workaround.
Inspect remaining account quota: the request bound is not a token, money or
shared-account quota guarantee.
Export `SCHOLARXIV_EXPERIMENT_API_KEY` through a secret manager or non-echoing
shell input; never put its value in a command, document or Git. Obtain the exact
HTTPS completion URL from the team's verified provider setup. The runner checks
URL shape, not domain ownership or account entitlements. Do not use an untrusted
endpoint: it will receive the key and approved transcript text.
The observed canonical endpoint is
`https://www.scholarxiv.com/api/v1/router/chat/completions`; the non-`www`
address redirects, which this runner deliberately does not follow.
If using ignored `backend/.env.experiments`, explicitly load it with
`uv run --frozen --env-file .env.experiments ...`; ordinary `uv run` does not
automatically load that filename.

```sh
uv run --frozen python -m services.experiments.router ../.scratch/be01-windows.json \
  --execute --endpoint "$SCHOLARXIV_ROUTER_COMPLETIONS_URL" \
  --run-id be01-approved-run-01 --max-requests 1050 --max-tokens 2048
```

`--max-requests` must cover the worst-case matrix before any call; smaller pilot
inputs need a smaller bound printed by plan mode. Temperature is always zero;
max tokens defaults to 2048 (accepted range 300-8192), with a 60-second HTTPX
timeout and redirects/environment proxies disabled. Calls are sequential.
Interrupt to stop; partial recordings are retained but never yield a completed
summary. HTTP errors are recorded as failed cases without retries or downgrade.
Use a new run ID for a new run; existing recordings are never overwritten.

### What is measured

The **experimental** `be01-experimental-v2` schema is implemented in
`services/experiments/schema.py`; the fixed prompt is `be01-window-only-v2`.
The output is `{"occurrences":[...]}`, with these required fields per occurrence:

| Field | Meaning |
| --- | --- |
| `proposition` | Claim preserving polarity, quantities, units, conditions and attribution |
| `taxonomy` | `empirical`, `causal`, `documentary`, `predictive`, `normative`, `mixed`, `unclear` |
| `source_refs` | Nonempty list of target observation text spans |
| `context_refs` | Context observation spans, or `[]` when none are used |
| `assertion_mode` | `asserted`, `reported`, `questioned`, `hypothetical`, `counterfactual`, `unclear` |
| `speaker_commitment` | `endorsed`, `rejected`, `uncommitted`, `unclear` |
| `attributed_to` | Person/group explicitly identifiable in supplied text, otherwise null |
| `eligibility_reason` | `factual-claim`, `factual-premise`, `opinion`, `quoted-not-endorsed`, `insufficient-context`, `not-a-claim` |
| `uncertainty_flags` | Any of `unresolved-reference`, `missing-context`, `ambiguous-attribution`, `ambiguous-commitment`, `ambiguous-meaning`, `source-text-conflict`; otherwise `[]` |

Every reference has `observation_id`, `start_char`, and `end_char`. Offsets count
Unicode code points in the exact observation text, zero-based and end-exclusive;
they are not UTF-8 bytes or Kotlin/Java UTF-16 indices. No text normalization is
performed. The validator rejects unknown IDs, role mismatches, empty/inverted or
out-of-bounds spans, whitespace-only selections, duplicate references within an
occurrence, and target spans combining distinct supplied speakers. Repeated
occurrences remain separate; no reconciliation is performed.

Extra fields, missing fields and coercions fail. Pure normative judgments,
questions and invented scenarios cannot use an eligible factual reason.
`quoted-not-endorsed` cannot accompany `endorsed`, and uncertainty flags cannot
repeat. Counterfactual claims may be eligible if their conditions are retained.
Missing evidence alone is not missing context. Relevant exclusions are retained;
this is not an exhaustive annotation of every nonclaim sentence.

The backend can derive quotations, supplied speaker labels and time envelopes
from validated references. The model must not generate those fields, occurrence
IDs, revisions, confidence scores or truth verdicts. JSON Schema enforces shape;
Pydantic and source checks enforce additional consistency, not semantic support.
`{"occurrences":[]}` means no relevant candidates in that window, not a failure
fallback or proof of whole-clip review.

This connects the draft to the isolated experiment, **not** the BE-03/BE-08
production contract. Agree the experiment schema/prompt before the real run;
production promotion still requires the shared contract review. Structural
validity does not measure claim correctness, semantic fidelity, extraction
recall, timestamps or evidence quality.

Recordings retain the request and raw response, executor, decision ID, usage,
response length in characters, latency, raw JSON validity, thinking/fence flags,
truncation, post-hygiene JSON validity, Pydantic validity, enum/schema errors and
source-reference errors. `evidence_id_hallucination` now means an unknown
observation ID in either reference list. Leading closed `<think>` blocks,
surrounding Markdown fences and
preamble before the first object are removed for the post-hygiene measurement.
Duplicate JSON keys, NaN/infinity constants, trailing prose, schema errors,
invented IDs, invalid source grounding and truncation fail closed. One invalid
output gets exactly one repair, with validation diagnostics and the same original
observations; HTTP/protocol failures do not. Unknown completion finish reasons fail.
The expected non-streaming envelope requires `model`, `decision_id`, `usage`
and exactly one `choices` entry with text `message.content` and `finish_reason`;
incompatibility is explicit failure, not a silently accepted response.

`summary.json` reports first-pass (after hygiene) and post-single-repair validity,
fail-closed percentage, per-route/condition metrics, actual-executor attempt
validity, and nearest-rank p50/p95 end-to-end case latency including repair.
Actual-executor counts separate first/repair attempts because routing may change
on repair; they are not falsely attributed to the originally requested model.
Requests and reported tokens are quota proxies only; missing usage is counted.
The proposed 90% threshold uses the 50 baseline cases per eligible route, not a
pool of routes or the `/no_think` condition. Fewer than 50 windows or synthetic
inputs are always `pilot_only`, never a go/no-go observation.
Even a qualifying real run leaves `decision: pending_team_approval`.

Results stay under ignored `.scratch/router/<run-id>/`: a manifest with dataset
and runner/schema-module hashes, schema/prompt versions, a fixed prompt-template
hash, input/output schemas, timestamps and completion state; flushed
`attempts.jsonl` cassettes; `results.json`; and `summary.json`.
Authorization headers are never recorded and the configured key is redacted,
but transcripts and responses remain sensitive. This is **not** a general PII
or secret sanitizer. Review rights, redact other sensitive data and obtain
approval before promoting any cassette into BE-08/RES-03 fixtures or publishing
an evidence table. Keep raw account evidence and private references out of Git.
Exit codes: 0 = complete with every case valid, 1 = complete with failed cases
(inspect the threshold separately), 2 = invalid configuration/input or local
failure. An interrupted run is incomplete, irrespective of partial successes.

The runner executes locally; model inference is hosted. No web-service deployment
is required, but `--execute` sends the approved observation text to the provider.
Plan mode and the focused offline checks send nothing.

Focused offline check:

```sh
uv run --frozen pytest -q tests/test_router_experiment.py tests/test_extraction_contract.py
```

### Fixed experiment contract and offline coverage

Version `be01-experimental-v2` and prompt `be01-window-only-v2` are the fixed
contract for the next model experiment. Regression tests pin their canonical
JSON fingerprints. A deliberate schema/prompt change requires a new version,
reviewed fixture expectations and a separate run; do not overwrite historical
inputs, recordings or results. This experiment lock is not a production
BE-03 contract, hosted-processing approval or a provider go/no-go decision.

`tests/test_extraction_contract.py` supplies thirteen invented contract cases:
negation, rejected quotation, hypothetical, counterfactual, normative/factual
premises, missing context, correction/repetition, reported belief, distinct
speakers/quantities, explicitly supplied hypothetical context, no claims,
instruction-like text, and Unicode source text. They are separate from the two
prompt examples and from both real evaluation splits. These are authored
expected structures, not recorded model outputs or independently reviewed
evaluation labels.

Offline checks establish that the contract can represent these cases, rejects
missing required fields, round-trips its values and preserves input text.
They do not establish that a model will extract them correctly or resist
instruction-like source text. A regression test explicitly demonstrates that
an incorrect strengthened proposition can still pass structural validation;
semantic fidelity and extraction recall require a separate model evaluation.
The schema records uncertain attribution/context but does not verify their
truth. Approximate envelopes remain source metadata, not generated word timing.

### BC-D03 / BC-D04: provider and hosting decisions

Decision update: **2026-10-04**, including the user's later removal of Gemini
and instruction to close BE-01 with hosting/account compatibility assumed.
These are BE-01 research decisions, not deployed adapters or amendments to the
shared production contract. Raw account evidence, requests, transcripts,
permission records and recordings remain local and ignored.

| Stage | Selected order | Verification and boundary |
| --- | --- | --- |
| Claim extraction | Scholarxiv `auto:cheap`, then Groq `openai/gpt-oss-20b` with `strict: true` JSON Schema | Groq is the only selected fallback. It accepts the experimental schema but still makes semantic/source-reference errors. Gemini was removed by the user; there is no selected second fallback. |
| Speech-to-text | Groq Whisper candidates, separately from the claim fallback chain | `whisper-large-v3` and `whisper-large-v3-turbo` both transcribed the authorized non-social development excerpt. This does not select an ASR fallback chain. |
| Hosting | EthioDeploy Free Web Service with embedded background work and Postgres | Selected by the user; project/addon provisioning and deployment are not verified. Durable jobs and recovery remain #16. |

Neither Gemini nor OpenRouter is part of the selected chain. No provider switching
has been added to the API, worker or fixed Scholarxiv comparison runner; actual
adapter orchestration belongs to BE-08. Production integration must preserve the
same validation contract on every route, report which provider/model answered,
and surface exhausted/unavailable routes as errors, never as an empty successful
extraction. Provider selection alone does not authorize sending real data to
another provider. [Groq's structured-output support](https://console.groq.com/docs/structured-outputs)
is not a semantic-quality guarantee.

For BE-08, validate JSON, required fields, source IDs/roles/spans, finish reason
and semantic fidelity independently of provider guarantees. Bound repairs and
retries, respect rate-limit cooldowns, and retain failure diagnostics. A fallback
does not automatically detect plausible but unsupported claims; the present
structural validator is not a semantic detector. Groq serving both ASR and claim
extraction also leaves both stages dependent on one provider.

Account and entitlement evidence, checked 2026-10-03/04:

| Item | Observation | Remaining limitation |
| --- | --- | --- |
| Scholarxiv keys/profile | User confirms separate backend/experiment keys and no Free dashboard model-selection controls | Account-wide cost ceiling is not established; this is user-reported configuration, not a dashboard audit |
| Router plan boundary | Authenticated cheap completion succeeded; paid-model probe returned 403 | Historical access does not guarantee future availability or remaining quota |
| Undocumented JSON mode | Compared with/without `response_format: {"type":"json_object"}`; fenced output still occurred | Unsupported behavior, not a correctness guarantee or production dependency |
| Papers | Authenticated title and advanced search succeeded; federated search returned 403 on Free | Live federated partial failure cannot be reproduced with this account |
| Groq strict output | Current v2 schema accepted by GPT-OSS 20B; local validation caught a span error | Strict JSON does not guarantee grounding, rejection stance, or hypothetical framing |
| Whisper | Two HTTP 200 responses with nonempty transcripts and seven timestamped segments each on one 31.819 s excerpt | English sample only; account dashboard, no-card status and upload-cap boundary not tested |
| Sponsor offers | Public STARK sponsors/prizes pages advertise participant Voxide sessions; Scholarxiv/EthioDeploy paid perks are winner offers | Credit allocation, expiry and restrictions are not established by the offer; do not budget winner perks as current Free entitlement |

The [Papers federated-search documentation](https://www.scholarxiv.com/developers/docs/papers-api/federated-search.md)
states that a failed source reports `{count: 0, hasMore: false}` instead of
failing the entire request. That can be indistinguishable from genuine empty
results. Do not infer "no evidence exists" from it. A provider-confirmed replay
or an authorized Go+ account is needed for live reproduction; a mocked failure
would be client-test evidence only. No upgrade or induced upstream outage is
part of this work.

Open-access routes have positive access evidence, with per-article licensing:

| Route/sample | Result | License and scope |
| --- | --- | --- |
| [arXiv 2501.10868](https://arxiv.org/abs/2501.10868), abstract and PDF | HTTP 200; bounded complete PDF download with signature/EOF checks | Article links CC BY 4.0; not a universal arXiv license or passage-extraction test |
| [Europe PMC PMC3258128 full text](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC3258128/fullTextXML) | HTTP 200; article body and 40 paragraph tags present | Article permissions specify CC BY-NC 3.0; attribution and noncommercial restrictions apply |
| Unpaywall lookup for DOI `10.1093/nar/gkr715` | HTTP 200; OA location and publisher PDF link returned using an authorized contact email | `cc-by-nc`; metadata discovery is not proof that every linked PDF is accessible or reusable |

The Whisper input was a roughly 1.02 MB, 16 kHz mono WAV derived from the
non-social development clip, with source hashes and CC BY 4.0 attribution
retained locally. Both responses matched the 90-word subtitle reference after
case/punctuation normalization except `favor` versus `favour`. This is subtitle
agreement, not independently certified WER. Single request times were 11.2 s
and 14.8 s respectively; they do not establish a model speed ranking.
[Groq's speech-to-text documentation](https://console.groq.com/docs/speech-to-text)
lists a 25 MB Free upload cap; the small test does not verify that boundary or
the account's audio quotas.

EthioDeploy's background-work and quota answers were supplied by the user,
not independently authenticated support correspondence:

- One Free project; web container 256 MB RAM / 0.5 CPU; 50 GB-hours and
  20 CPU-hours monthly.
- Web sleep occurs after 30 minutes without incoming HTTP. Outbound requests
  and CPU work do not reset it; sleep kills in-progress jobs. Polling resets
  the timer but is not a durability guarantee.
- Postgres is separate compute: 256 MB RAM / 0.25 CPU and 512 MB total storage,
  not charged against the web compute quota.

Use external inference rather than local Whisper/LLM weights in this web
container. Persist job state/checkpoints, make processing idempotent, and recover
unfinished jobs on startup. A sub-30-minute job is not protected from crashes,
OOM, restarts or quota exhaustion. Public
[billing docs](https://ethiodeploy.com/docs/billing) also state that Free quota
exhaustion stops a project until the next month. The HTTP body limit, HTTP
timeout, region and web disk allowance remain unanswered; no default is assumed.

### RFC-D31: measured result and limited go

On 2026-10-04 the user accepted **a limited go for BE-08 development**, using
the proposed **at least 90% post-single-repair structural-validity gate**, the
selected fallback order, and explicit deferral of the unfinished model
comparison. This does not approve production accuracy or declare every BE-01
check complete. Historical run summaries retain `pending_team_approval`;
this later decision supplements them rather than rewriting them.

The fixed v2 prompt/schema were measured on 50 authorized real development
windows from seven sources, with temperature 0 and `max_tokens: 8192`.
Windows overlap and source/topic families are correlated; these are not 50
independent clips or a holdout evaluation.

| Cohort | First-pass structural validity | Post-repair structural validity | Interpretation |
| --- | --- | --- | --- |
| `auto:cheap`, all 50 baseline windows | 44/50 (88%) | 48/50 (96%) | Meets the selected structural gate; two cases fail closed |
| Matched cheap subset, no suffix | 21/25 | 25/25 | Same 25 windows as the next row |
| Matched cheap subset, `/no_think` | 17/25 | 21/25 | No improvement; do not adopt the suffix |
| `auto:quality`, original baseline | 9/50 | 9/50 | Only nine baseline cases returned HTTP 200; remaining failures do not establish model quality |
| Pinned routes, original/resumed matrix | Not meaningfully measured | Not meaningfully measured | No HTTP responses for pinned routes in that cohort; do not rank their quality as zero |
| GPT-OSS via Scholarxiv, first recovery baseline subset | 13/21 | 16/21 | Partial, separate recovery cohort, not a completed 50-window route or a direct Groq measurement |

The complete cheap baseline used 56 requests and 123,122 reported tokens;
case p50/p95 latency including repair was 2.35/10.90 s. Counts do not establish
billed cost or remaining shared quota. The original plus first continuation
recorded 525 cases / 539 requests, including 435 transport failures; later
recoveries and timeout trials are separate cohorts, not overwritten results.
The final recovery left 399 cases unattempted in that recovery sequence.
They are explicitly deferred, not passed. Earlier synthetic runs used a
different 2048-token ceiling and cannot be pooled with these measurements.

Semantic review found missed claims, incorrect rejection/negation, and
hypothetical context promoted to fact, including in structurally valid output.
Quote-reference and prompt-revision pilots did not establish a consistent
held-aside improvement and were not adopted. Their provisional reference labels
are agent-authored, not independent human gold.

A separate current-contract **direct Groq** synthetic precheck made five HTTP
requests: four 200s and one 429. Negation passed; a rejected-quotation response
failed span bounds, then its repair was rate-limited. After the cooldown, a
separately recorded continuation repaired the bounds and tested hypothetical
context. Both passed structural checks but still misrepresented source meaning:
the rejection was lost, its citation narrowed to an insufficient fragment,
and explicit hypothetical context was ignored. Thus access/schema compatibility
is verified, not semantic readiness. The original 429 and failures are retained.

### User-directed closure and retained limitations

On 2026-10-04 the user instructed closure of #11, removed Gemini, and accepted
hosting and account compatibility **as assumptions without further evidence**.
This supersedes the earlier requirement to keep BE-01 open for those checks.
It does not establish missing facts, erase failed experiments, or approve
production reliability. The limited development go and previously approved
comparison deferral remain unchanged.

The following remain unverified or undelivered despite the closure instruction:

- Groq's dated live Limits dashboard and no-card account status are unverified.
  The probe observed an 8,000 TPM rejection; response headers are not a substitute
  for all request/token/audio limits on the account.
- EthioDeploy Free project/addon confirmation and the unanswered hosting limits
  above are assumed suitable for planning, not measured or provider-confirmed.
- Papers partial-failure reproduction remains Free-plan blocked. Do not mark
  documentation or a mocked response as live reproduction.
- Cassettes are saved locally; fixture publication requires rights/privacy
  review and explicit authorization. No real transcripts or account identifiers
  have been promoted into committed fixtures.
- At issue closure, the experiment changes were local and uncommitted.
  Repository delivery is a separate step requiring authorized commit/push/PR,
  required CI and another developer's review. Issue closure alone does not
  authorize these actions or satisfy those requirements.

Gemini account/model/schema checks are no longer in the selected scope. No
upgrade, deployment, new transcript upload or public cassette release is implied
by accepting the account/hosting assumptions.

Local validation on 2026-10-04: 267 backend tests passed against the pinned
PostgreSQL 16 container, including migration/lifecycle tests; Ruff lint/format,
strict MyPy and source/wheel builds passed. WSL could not reach Docker's
published loopback port, so the full suite ran in a temporary Python 3.11
container sharing the database container's network namespace. These are local
results, not a required-CI result or a newly measured coverage comparison.

## Stack record and boundaries

This implements the infrastructure choice in
[BE-02 / #12](https://github.com/natnael-solomon/ovrly/issues/12):
Python 3.11, uv, FastAPI/Uvicorn, PostgreSQL 16, SQLAlchemy asyncio/Psycopg and
Alembic, with a single Python codebase for API and worker. The issue associates
this stack with `BC-D03` / `RFC-D41-D43`. This records the implemented stack,
not approval of the remaining infrastructure or hosting decisions. The full
decision-log task remains #5; provider/hosting evidence remains #11 and #21.

Keep provider credentials and private media out of Git; future media uploads
require explicit consent and a retention policy. No hosting entitlement or
deployment has been verified by this bootstrap. [Android builds independently](../android/README.md#setup).
