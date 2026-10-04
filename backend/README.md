# Backend

Python 3.11 / uv / FastAPI foundation with PostgreSQL, Alembic, a shared worker
lifecycle and a durable PostgreSQL job queue. The API mints guest principals,
accepts uploads and records investigations behind bearer authentication (see
[Identity and intake API](#identity-and-intake-api)). No research processing or
account linking is implemented, recorded investigations are not yet handed to
the queue, and no pipeline stage handlers exist, so the worker claims nothing in
production; it proves startup, supervision, lease draining and shutdown. See
[Durable jobs and recovery](#durable-jobs-and-recovery).

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
| `OVRLY_WORKER_SHUTDOWN_SECONDS` | 5; positive, at most 30. Time a stopping worker may spend finishing its in-flight job before the lease is released |
| `OVRLY_JOB_LEASE_SECONDS` | 30; positive, at most 600. Lease granted per claim; handlers extend it with heartbeats |
| `OVRLY_JOB_POLL_SECONDS` | 1; positive, at most 60. Idle wait between claim attempts |
| `OVRLY_UPLOAD_MAX_BYTES` | 268435456 (256 MiB); positive. Placeholder until BC-D06 fixes the budget |
| `OVRLY_UPLOAD_TARGET_SECONDS` | 900; how long an upload target accepts bytes and completion, at most 86400 |
| `OVRLY_MAX_SHARED_DURATION_SECONDS` | 600; declared shared-media duration limit from BC-D01 |
| `OVRLY_STORAGE_DIR` | `.data/uploads`, relative to `backend/` and Git-ignored; local filesystem upload store |

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
owned database resources and drain their lease on shutdown. Worker failures are
visible and are not automatically restarted. Migration `0001` only establishes
Alembic history; `0002_jobs` creates the `jobs` and `job_results` tables and
`0003_identity_intake` creates the principal, credential, upload, investigation
and idempotency tables defined in `services/models.py`. Future schema changes
require a reviewed migration and upgrade/downgrade coverage, not `create_all()`
during API startup.

### Durable jobs and recovery

`services/jobs` implements the BE-04 engine core. `queue.py` is the PostgreSQL
queue, `states.py` the pure state machine, `handlers.py` the stage-handler
protocol and `faults.py` the fault-injection hooks used only by tests.

| Mechanism | Behaviour |
| --- | --- |
| Stage key | `(version, stage, input_hash)` is unique on `jobs` and `job_results`; re-enqueueing returns the existing job. `JobQueue.enqueue` takes the caller's connection so the business record and the queue row commit in one transaction. |
| Claim | `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`; each claim bumps the fencing token and attempt count, grants a lease and first returns expired leases to the queue (or makes a pending cancellation effective). |
| Lease | Owner, expiry and a monotonically increasing fencing token. `heartbeat` extends it and reports a cancellation request; a lost lease raises `LeaseLost`. |
| Publish | Compare-and-set on owner, fencing token, generation, `running` state and no pending cancellation; the result row is written in the same transaction. Any mismatch raises `PublishRejected`. |
| Cancellation | `request_cancel` cancels a queued job immediately (effective) or sets `cancel_requested` for a leased one (requested); the worker observes it at start, through heartbeats or when the lease expires, and makes it effective. Both bump the generation. |
| Deletion | `delete` tombstones the job, clears its payload, bumps the generation and removes the published result. |
| States | queued, leased, running, published, cancelled, deleted, failed. Terminal states only move to deleted; deleted is absorbing. `tests/test_job_states.py` checks random legal and illegal sequences with Hypothesis. |
| Shutdown | Stop requests end claiming; the in-flight job finishes and publishes within `OVRLY_WORKER_SHUTDOWN_SECONDS`, otherwise the task is cancelled and its lease is released, so a job is neither lost nor run twice by the same worker. |

Workers execute only the stages they have handlers for; `default_handlers()` is
empty until pipeline tasks register stages. Handlers receive a `JobContext`
whose `heartbeat()` must be called by long stages. Handler exceptions mark the
job `failed` with the exception type only; messages are never persisted or
logged. Retry classes, backoff, `POST .../cancel` and `DELETE` endpoints and
the remaining fault cases are the next BE-04 change.

`tests/recovery/` holds the in-process API + worker harness with
`ScriptedFaults` checkpoints (`claimed`, `before_publish`, `after_publish`).
Covered now: worker killed before the state commit (re-leased, completes exactly
once), lease expiry while the original worker is alive (stale publish rejected
by the fencing token), graceful drain in embedded and standalone (SIGTERM)
modes, forced drain releasing the lease, cancellation before and during a stage,
lost leases, and the negative invariants (duplicate publish, publish after
cancel, publish after delete). After each case the harness asserts exactly one
publication, no lost job, matching stage key, fencing token and generation, and
no held leases. CI runs this as the separate **Backend recovery** job:

```sh
OVRLY_TEST_DATABASE_URL='postgresql+psycopg://ovrly:local-development-only@127.0.0.1:55432/ovrly' \
  uv run --frozen pytest -q tests/test_job_states.py tests/test_job_queue.py tests/recovery
```

Not covered yet: kill after provider call or artifact store, cancel during
retrieval, delete with a delayed callback, database connection drop, API
restart with in-flight requests, cross-owner reads and the idempotency tests for
chunks, investigation POSTs and provider callbacks.

## Identity and intake API

All product routes live under `/v1`, return JSON and use the shared contract
error shape from
[`packages/contracts/schemas/error.schema.json`](../packages/contracts/schemas/error.schema.json):
`{code, message, retryable, action, request_id}` with `SCREAMING_SNAKE_CASE`
codes. Validation failures (422 `VALIDATION_FAILED`), unknown routes
(`NOT_FOUND`), database outages (503 `DATABASE_UNAVAILABLE`) and unexpected
failures (500 `INTERNAL_ERROR`) use the same shape; messages never include
internals, inputs or secrets. Every response carries `X-Request-Id`; a
well-formed client value (1 to 128 characters, starting with a letter or digit,
then letters, digits, `_` or `-`) is echoed, otherwise one is generated.
`action` is a client hint: `none`, `retry`, `authenticate`, `fix_request` or
`upload_again`. The test suite validates every error response against the
schema with the contracts validator.

| Route | Behavior |
| --- | --- |
| `POST /v1/principals/guest` | Mints a guest principal and an opaque bearer token (`ovk_` prefix, 256 random bits). Only a SHA-256 digest is stored; the token is returned once and never logged. 201. |
| `POST /v1/uploads` | Declares `size_bytes`, `sha256` and optional `content_type`; returns a scoped `target`, `max_bytes` and `expires_at`. Over the byte limit: 413 `UPLOAD_TOO_LARGE`. |
| `PUT /v1/uploads/{id}/content` | Streams raw bytes to the target while holding the upload row lock, so a concurrent completion waits for the whole body. Exceeding the smaller of the limit and the declared size discards the partial content with 413. Expired: 410 `UPLOAD_EXPIRED`. |
| `POST /v1/uploads/{id}/complete` | Re-reads the stored bytes and compares size and SHA-256 with the declaration. Mismatch deletes the bytes and returns 409 `UPLOAD_MISMATCH`; missing bytes return 409 `UPLOAD_CONTENT_MISSING`. Completing twice returns the same 200. |
| `POST /v1/investigations` | Requires `Idempotency-Key` (400 if missing or longer than 200 characters). Body: `{"source": {"kind": "url", "url": ...}}` or `{"source": {"kind": "upload", "upload_id": ...}}`, each with optional `duration_ms`. Writes the investigation and the key in one transaction before answering 202. Same key and body replays the original 202 body; same key with a different body is 409 `IDEMPOTENCY_KEY_REUSED`. Keys are scoped per owner. |
| `GET /v1/investigations` | Newest-first list of the caller's investigations (at most 100). |
| `GET /v1/investigations/{id}` | `state`, `stage`, a coverage placeholder, `version` and a safe `error` object or `null`. |

Every route except guest minting requires `Authorization: Bearer <token>`.
Missing credentials return 401 `AUTHENTICATION_REQUIRED`; malformed, unknown or
revoked ones return 401 `INVALID_CREDENTIAL`, both with `WWW-Authenticate`.
Identity comes only from the credential: `user_id`, `owner_id` or `principal_id`
in a body, query string or `X-User-Id`-style header is rejected with
`CLIENT_IDENTITY_REJECTED`. Object routes load rows through the owner-scoped
helper in `services/api/auth/ownership.py`; another owner's object is a plain
404, so existence is not revealed.

Limits come from settings. A declared `duration_ms` above
`OVRLY_MAX_SHARED_DURATION_SECONDS` is 422 `DURATION_LIMIT_EXCEEDED`; the byte
limit is checked at declaration and while streaming. Upload bytes are written
to `OVRLY_STORAGE_DIR` through the `UploadStore` interface in
`services/storage.py`; object storage can replace the local store once BC-D03 is
decided. Uploaded media is development data on the local disk, not a retention
policy or consent record.

Recorded investigations stay `queued` at stage `intake`. Handing them to the
job engine (#16), account linking (BC-D07), quotas beyond the two limits and
deletion are the second BE-05 PR. `services/api/intake.py` defines the
`InvestigationDispatcher` hook that runs inside the creating transaction; the
default records nothing beyond the investigation row. `packages/contracts`
holds the shared error shape and the draft voice-actions schemas; the intake
request and response models in `services/api/schemas.py` remain backend-owned
Pydantic until #15 exports them there.

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
resource cleanup, the job queue invariants, the recovery harness,
startup-helper negative paths, and the intake API: guest credentials, rejected
client identity, cross-owner 404s, idempotent replay and 409, upload limits,
expiry and hash mismatch, and the shared error shape validated against
`packages/contracts/schemas/error.schema.json`. The test database is migrated
to `head` once per session. No provider keys or personal media are needed;
upload tests use synthetic bytes in a temporary directory.

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
strict MyPy with the Pydantic plugin, the
[contracts package](../packages/contracts/README.md#validation) validator and
tests, PostgreSQL 16, migration upgrade/drift
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
the standalone entry point. `services/api/auth` now exists and is held to the
same 90% floor; `services/contracts` keeps a future 90% floor and is shown as
**not implemented / not evaluated**, not 100%, until #15 lands. Android coverage
is not part of this denominator and must not be inferred from backend results.

Remaining [REPO-04 / #13](https://github.com/natnael-solomon/ovrly/issues/13) work:
Android unit/instrumented reports and capture/share floors depend on the emulator
work in #37; auth/contracts/job-engine coverage must be verified on their real
implementations; a maintainer must add **Backend checks** to the main ruleset
after the workflow lands and reports successfully. This PR does not change
protection settings or complete the whole issue.

Durable job retry classes, cancel/delete endpoints and the remaining
fault-injection cases remain
[BE-04 / #16](https://github.com/natnael-solomon/ovrly/issues/16); the engine
core and the first recovery cases are described under
[Durable jobs and recovery](#durable-jobs-and-recovery).

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
