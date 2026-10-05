# Backend

Python 3.11 / uv / FastAPI foundation with PostgreSQL, Alembic, a shared worker
lifecycle and a durable PostgreSQL job queue with typed retry classes. The API
mints guest principals, links them to a Google account (BC-D07), accepts
uploads and records investigations behind bearer authentication (see
[Identity and intake API](#identity-and-intake-api)). Each recorded
investigation is handed to the queue as an `intake` job in the same
transaction; the worker's `intake` stage only confirms the record today, so no
research processing happens yet (BE-07, #20). The worker can also run opt-in
privacy-retention jobs (see [Privacy operations](#privacy-operations)). See
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
| `OVRLY_JOB_RETRY_TRANSIENT_ATTEMPTS` | 5; 0 to 20. Scheduled retries for the `transient` class before the job fails |
| `OVRLY_JOB_RETRY_RATE_LIMITED_ATTEMPTS` | 5; 0 to 20. Retries for the `rate_limited` class |
| `OVRLY_JOB_RETRY_SCHEMA_REPAIR_ATTEMPTS` | 2; 0 to 10. Repair attempts for the `invalid_model_schema` class |
| `OVRLY_JOB_RETRY_UNKNOWN_OUTCOME_ATTEMPTS` | 3; 0 to 10. Reconciliation attempts for the `unknown_outcome` class |
| `OVRLY_JOB_RETRY_BACKOFF_SECONDS` | 1; positive, at most 60. Base of the exponential backoff |
| `OVRLY_JOB_RETRY_MAX_BACKOFF_SECONDS` | 60; positive, at most 3600 and at least the base. Caps backoff and provider retry-after hints |
| `OVRLY_GOOGLE_CLIENT_ID` | Empty; Google Web client ID that linked ID tokens must be issued for (BC-D07). Configuration, not a secret. Empty leaves `POST /v1/principals/link` unavailable with 503 `ACCOUNT_LINK_UNAVAILABLE` |

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
Alembic history; `0002_jobs` creates the `jobs` and `job_results` tables,
`0003_identity_intake` creates the principal, credential, upload, investigation
and idempotency tables defined in `services/models.py`, `0004_job_retries`
adds the retry class, per-class retry counters and the provider request id to
`jobs`, `0005_account_link` adds `google_sub` (unique), `merged_into` and
`merged_at` to `principals`, and `0006_job_ownership` adds the nullable
`owner_id` and `cancel_outcome` columns to `jobs`. `0007_captures` adds capture
sessions and chunk reservations/receipts. Future schema changes require a reviewed migration
and upgrade/downgrade coverage, not `create_all()` during API startup.

### Durable jobs and recovery

`services/jobs` implements the BE-04 engine. `queue.py` is the PostgreSQL
queue, `states.py` the pure state machine, `handlers.py` the stage-handler
protocol, `retries.py` the retry classes and policy, and `faults.py` the
fault-injection hooks used only by tests.

| Mechanism | Behaviour |
| --- | --- |
| Stage key | `(version, stage, input_hash)` is unique on `jobs` and `job_results`; re-enqueueing returns the existing job. `JobQueue.enqueue` takes the caller's connection so the business record and the queue row commit in one transaction. |
| Claim | `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`; each claim bumps the fencing token and attempt count, grants a lease and first returns expired leases to the queue (or makes a pending cancellation effective). Only jobs whose `available_at` has passed are claimable. |
| Lease | Owner, expiry and a monotonically increasing fencing token. `heartbeat` extends it and reports a cancellation request; a lost lease raises `LeaseLost`. |
| Publish | Compare-and-set on owner, fencing token, generation, `running` state and no pending cancellation; the result row is written in the same transaction. Any mismatch raises `PublishRejected`. |
| Retry | A handler raises a typed outcome (table below). The policy either schedules the job through `available_at`, recording the class and a per-class counter in `retry_counts`, or declares the class exhausted so the job fails with `retry_class` set. A pending cancellation wins over a retry. |
| Request id | `JobContext.record_request_id` persists the provider request id before the call (fenced). A re-leased attempt sees it on `ClaimedJob.provider_request_id` and reconciles instead of calling again; `find_by_request_id` routes callbacks. The policy refuses to retry an `UnknownOutcome` when no id was recorded. |
| Infrastructure errors | Database, socket and timeout errors raised during a stage leave the outcome unknown: the worker never marks the job failed, hands the lease back if it can and otherwise lets it expire, so the job is re-leased. The worker loop survives. |
| Cancellation | `request_cancel` cancels a queued job immediately (effective) or sets `cancel_requested` for a leased one (requested); the worker observes it at start, through heartbeats or when the lease expires, and makes it effective. Both bump the generation. |
| Deletion | `delete` tombstones the job, clears its payload, bumps the generation and removes the published result. The tombstone keeps `provider_request_id` so a late callback is routed to nothing. |
| States | queued, leased, running, published, cancelled, deleted, failed. Terminal states only move to deleted; deleted is absorbing. `tests/test_job_states.py` checks random legal and illegal sequences with Hypothesis. |
| Shutdown | Stop requests end claiming; the in-flight job finishes and publishes within `OVRLY_WORKER_SHUTDOWN_SECONDS`, otherwise the task is cancelled and its lease is released, so a job is neither lost nor run twice by the same worker. |

| Retry class | Raised as | Schedule |
| --- | --- | --- |
| `transient` | `Transient` (provider 5xx, resets) | Exponential backoff with full jitter from `OVRLY_JOB_RETRY_BACKOFF_SECONDS`, capped by the maximum; `OVRLY_JOB_RETRY_TRANSIENT_ATTEMPTS` retries |
| `rate_limited` | `RateLimited(retry_after_seconds)` (provider 429) | The provider's hint clamped to the maximum backoff, otherwise backoff; `OVRLY_JOB_RETRY_RATE_LIMITED_ATTEMPTS` retries |
| `non_retriable_input` | `NonRetriableInput` | Never; the job fails on the first outcome |
| `invalid_model_schema` | `InvalidModelSchema` | Backoff; `OVRLY_JOB_RETRY_SCHEMA_REPAIR_ATTEMPTS` repair attempts, visible to the handler through `retry_counts` |
| `unknown_outcome` | `UnknownOutcome` (provider timeout) | Backoff only when a request id was recorded; `OVRLY_JOB_RETRY_UNKNOWN_OUTCOME_ATTEMPTS` reconciliation attempts; never a silent re-call |

Workers execute only the stages they have handlers for. `default_handlers()`
registers the `intake` stage from `services/pipeline/intake.py`: it checks that
the investigation still exists and belongs to the owner in the job payload,
confirms `queued` at stage `intake` with the coverage placeholder and publishes
a result row; a missing or foreign investigation is `NonRetriableInput`. Media
stages arrive with BE-07 (#20). Handlers receive a `JobContext`
whose `heartbeat()` must be called by long stages. Other handler exceptions
mark the job `failed` with the exception type only; messages are never
persisted or logged. The owner-scoped cancel/delete API uses the same
`request_cancel` and `delete` primitives inside the authorization transaction;
see [Job actions](#job-actions).

`services/api/intake.py` hands each new investigation to the queue.
`QueueDispatcher` runs inside the transaction that writes the investigation and
idempotency rows and calls `JobQueue.enqueue` with stage key
`(1, "intake", sha256("investigation:<id>"))` and payload
`{investigation_id, owner_id}`. A dispatcher failure rolls back the
investigation, the key and the job together; a replayed `Idempotency-Key`
never reaches the dispatcher, and the stage-key uniqueness would reject a
second job anyway. `RecordOnlyDispatcher` remains for tests that isolate the
API from the queue (`create_app(dispatcher=...)`).

`tests/recovery/` holds the in-process API + worker harness with
`ScriptedFaults` checkpoints (`claimed`, `before_publish`, `after_publish`,
plus the stage-level `after_provider_call` and `after_artifact_store` that stub
stages hit through `JobContext.checkpoint`). No provider or artifact store is
integrated yet, so those stages are stubs registered only in tests. Covered:

- Worker killed before the state commit, after the provider call (request id
  recorded, reconciled on re-lease, provider called once) and after the
  artifact store (idempotent write under the stage key); each re-leased and
  completed exactly once.
- Lease expiry while the original worker is alive (stale publish rejected by
  the fencing token); lost leases detected by heartbeats and at start.
- Graceful drain in embedded and standalone (SIGTERM) modes, forced drain
  releasing the lease, and an API lifespan restart with an in-flight request
  and a running stage (released, re-leased by the new lifespan, completed once).
- Cancellation before a stage, during a stage and mid-retrieval (no further
  chunk fetched); deletion with a delayed provider callback (late publish
  rejected, no resurrected content, tombstone kept), including authenticated
  API-driven variants.
- Database connection dropped mid-stage with `pg_terminate_backend`, and the
  variant where the release fails too (lease expires); the worker survives and
  the job is never marked failed.
- Every retry class through the worker (`test_retries.py`): success after
  backoff, exhaustion with the class recorded, the clamped rate-limit hint,
  immediate failure for non-retriable input, bounded schema repair, unknown
  outcome reconciled by request id, exhausted, or refused without an id.
- Duplicate provider callback with the same request id (`test_idempotency.py`):
  concurrent and late duplicates publish once; a callback for a deleted job is
  dropped.
- Intake hand-off (`test_intake_handoff.py`): a dispatcher failure rolls back
  the investigation and the job together; sequential and concurrent replays of
  one `Idempotency-Key` yield exactly one job; the embedded worker killed
  before publishing the `intake` result is re-leased by a standalone worker
  with the production stage table and the investigation is published once,
  reporting `running` meanwhile; a job for a missing or foreign investigation
  fails as `non_retriable_input`.
- Negative invariants: duplicate publish, publish after cancel, publish after
  delete, stale fencing token, stale lease retry or request-id write.

After each case the harness asserts exactly one publication (or none for
cancelled, deleted and failed jobs), no lost job, matching stage key, fencing
token and generation, no held leases and, for deletions, an empty payload and
no result. All harness cases also check that another principal cannot read the
job through the shared ownership loader or obtain data/mutate it through the
action endpoints: other-owner and missing jobs return identical errors.
CI runs this as the separate **Backend recovery** job:

```sh
OVRLY_TEST_DATABASE_URL='postgresql+psycopg://ovrly:local-development-only@127.0.0.1:55432/ovrly' \
  uv run --frozen pytest -q tests/test_job_states.py tests/test_job_retries.py \
  tests/test_job_queue.py tests/recovery
```

Privacy recovery tests cover current workspace/upload retention and late-result
rejection. External-provider erasure, future artifact stores and physical
backup expiry are not implemented; the chunk `(session, seq)` idempotency test
belongs to the capture intake endpoint that introduces that resource.
Investigation POST idempotency is covered by `test_intake_handoff.py` and
`tests/test_intake_api.py`.

### Job actions

Both routes require the BE-05 bearer principal and accept **no request body**.
They load and lock the job through `load_owned`; authorization, queue mutation
and the receipt commit atomically. Missing, unowned legacy and other-owner
jobs all return 404 `NOT_FOUND`, with no owner information.

| Route | Result |
| --- | --- |
| `POST /v1/jobs/{id}/cancel` | 200 `{"job_id":"...","cancellation":"effective"}` for queued/already cancelled work; 202 with `cancellation: requested` for a leased/running job. Published/failed jobs return 409 `JOB_NOT_CANCELLABLE`. |
| `DELETE /v1/jobs/{id}` | 200 with `job_id`, `state: deleted`, `access_revoked: true`, `cleanup_status: complete`, `cleanup_scope: job_payload_and_result` after tombstoning, clearing the lease/payload and removing the result in one transaction. |

Cancellation stores its first outcome durably. Repeats return that original
status/body even after acknowledgement or an API restart; the receipt is not
a current-progress response. A deleted job cannot replay a cancellation
receipt (404); its owner can repeat DELETE with the same deletion receipt.
Repeats do not bump generations, timestamps or perform cleanup again.
Cancellation cannot promise provider interruption or refunded billing.

Cleanup means **only the database job payload/result**. No provider, upload,
external artifact or backup deletion is claimed. Tombstone identity and fencing
metadata remain so late callbacks cannot resurrect content. There is no
general job-read endpoint in this slice.

Migration `0006_job_ownership` adds nullable `owner_id` and `cancel_outcome`.
The `QueueDispatcher` in `services/api/intake.py` passes the investigation's
owner to `JobQueue.enqueue(..., owner_id=...)`, so every `intake` job created
by `POST /v1/investigations` can be cancelled or deleted by that owner; the
recovery suite proves it and that another principal gets 404. Jobs enqueued
before the migration remain ownerless and API-inaccessible; ownership is never
inferred from payload data. Other enqueue callers pass the authenticated
principal the same way in their business transaction. The existing global
stage key must not be reused across principals: a collision fails explicitly
instead of returning another owner's job, while a replay by the same owner
still returns the existing job.

Shared request-path/response schemas and synthetic receipts live in
`packages/contracts`; all failures reuse its unchanged error schema, and
`openapi.json` publishes both operations. Backend
recovery tests compare actual API output with these fixtures and test replay,
concurrent requests, rollback, invalid input and delayed publication. Android
parsing and both-side contract review remain coordinated through #15/#62.

## Privacy operations

The [data map and runbook](../docs/operations/data-map.md) enumerate current
tables, columns, upload bytes, logs and infrastructure copies. Automatic
retention is **disabled by default**: [BC-D06](../docs/decisions/BC-D06-retention.md)
is Proposed until the product owner accepts it.

| Setting | Proposed default and enforced bounds |
| --- | --- |
| `OVRLY_RETENTION_ENABLED` | `0`; explicit opt-in for approved demo data only |
| `OVRLY_RETENTION_DATA_SECONDS` | 86400; 60..2592000. Guest workspace lifetime from principal creation; also legacy ownerless job content age. Linked accounts are never expired |
| `OVRLY_RETENTION_TOMBSTONE_SECONDS` | 604800; 60..7776000. Detached tombstone/terminal retention-receipt age |
| `OVRLY_RETENTION_POLL_SECONDS` | 60; 1..3600. Scheduling interval |
| `OVRLY_RETENTION_BATCH_SIZE` | 100; 1..1000 expired subjects per category per sweep |

When enabled, both embedded and standalone workers register and schedule the
`privacy_retention` stage using the existing durable queue. Slot keys prevent
duplicate schedules across processes; failures use existing lease recovery.
API-only mode schedules nothing. The API and worker must share the same
database, settings and **upload directory/mount**.

The proposed policy expires credentials and every upload/investigation/job
belonging to a **guest** principal 24 hours after that principal was created,
including newer content. Principals linked to a Google account
([BC-D07](../docs/decisions/BC-D07-account-link.md), `kind = account`) are
excluded from the workspace sweep: the account row, its credentials and its
objects survive however old the original guest row is, because they carry the
saved-report recovery promise. A guest that was merged into an account on a
second device keeps `kind = guest`, so its revoked credential and leftover
workspace still expire. The sweep is deliberately **not a permanent
account/saved-report policy** or inactivity timer; a retention policy for
accounts needs a revised BC-D06. Pending upload targets expire separately.
Cleanup uses row locks and the same `JobQueue.delete` as user-requested
deletion; counts indicate completed operations, not external-provider erasure.
Old detached tombstones are eventually removed; late publication still fails.
API replay receipts are therefore bounded by retention.

Both entry points install `services/logging.py`: fixed event messages, UUID
job IDs, counts and finite codes only. Unknown messages, dynamic stages/worker
names, exception messages/tracebacks, request URLs and extra fields are
redacted. Client-chosen request IDs are correlated by
`request_log_id(X-Request-Id)` (SHA-256's first 16 bytes formatted as a UUID);
the raw header is not logged. Do not install new unfiltered handlers after
startup. Uvicorn/HTTP/SQL access messages become `UNSTRUCTURED_LOG_REDACTED`;
API completion events retain status and the hashed request ID instead.
Host log retention is operator-owned; no local log file is created.

Run targeted checks from `backend/` with the documented test database:

```sh
uv run --frozen pytest -q tests/test_privacy_logging.py tests/recovery/test_privacy.py
```

These run in Backend checks; deletion/late-callback cases also run in Backend
recovery. The [PDP Articles 18-22 checklist](../docs/operations/pdp-checklist.md)
records unresolved transfer/sovereignty gates and is not legal advice.
No provider erasure, backup cleanup, deployment or policy approval is claimed.

## Incremental capture API

BE-06 (#24), AC01/AC03/AC04, contract `0.2.0-draft`. These are draft contract choices, not a
claim of product-owner acceptance; Android and backend review is required.
The existing `CaptureSession` and `CaptureChunk` wire shapes are unchanged.

| Route | Behavior |
| --- | --- |
| `POST /v1/captures` | Bearer + `Idempotency-Key`, JSON `{}` or `{"chunk_duration_ms":10000}` (1000..30000). Creates a session and a capture-source investigation with the same UUID. Returns 201. Same owner/key/duration replays the original empty/open response; a changed duration conflicts. |
| `PUT /v1/captures/{id}/chunks/{seq}` | Multipart with exactly a `metadata` text part containing UTF-8 JSON `{"chunk":<CaptureChunkRequest>,"modality":"speech|text|both"}` and a `content` file part. Verifies actual size/hash, persists the receipt and enqueues one owner-scoped `media_validation` job atomically. Returns 200 with stored/out_of_order/duplicate. |
| `POST /v1/captures/{id}/close` | JSON `{"continue_research":true}` with optional `duration_ms`. False cooperatively cancels unfinished chunk jobs; true keeps them. Published results are retained. Replays return the same closed session; a different effective duration/choice returns 409. |
| `GET /v1/captures/{id}` | Session, continuation choice, expiry, manifest, per-chunk work and typed per-claim progress envelope. Missing/foreign resources return the same 404. |

The server enforces **180000ms on the capture-relative media timeline**, not a
180-second HTTP transfer deadline. Chunk n starts at n * chunk_duration_ms.
Only the final chunk may be shorter; no later sequence can be reserved.
Out-of-order chunks are accepted; only verified receipts count in `received_ms`
and sequence gaps below `highest_seq`. Duplicates must match metadata and
actual bytes; they preserve `received_at`, return **current** gaps and never
write/enqueue twice. Different content returns `CAPTURE_CHUNK_CONFLICT`.

New bytes must finish within 180 seconds plus `OVRLY_UPLOAD_TARGET_SECONDS`
after session creation (default total 18 minutes), allowing bounded buffered
delivery. Expired open sessions read as `abandoned`, using the durable deadline
as `closed_at`. New chunks after expiry return 410; after explicit close, 409.
Identical stored retries and an explicit close remain available after expiry.
Expiration stops intake, not already queued work.

Close's optional duration defaults to the highest received end, zero if empty.
Supply the actual duration to disclose missing trailing intervals; it cannot
truncate received media or extend a short final chunk. Unknown unsent tails
cannot be inferred. The manifest's `declared_coverage` lists captured intervals
for speech/text as declared by the client, **not** verified extraction or
assessment. `both` describes one media object carrying both modalities; separate
audio/frame streams must be packaged before using this transport.

Each accepted chunk is available to the worker before close. Both worker modes
register `CaptureProcessor`, which re-verifies stored bytes under a session lock
and publishes a durable validation result. **ASR, OCR and claim analysis remain
#20/#25/#27/#29**: a published validation job still reads `waiting`, with empty
`claims` and `claim_extraction_status: not_started`, never complete/no-claims.
The per-claim envelope is typed for the pipeline handoff; no synthetic finding
is emitted by the API. Android codecs mirror the additions, but device upload
and polling integration remain AN-07.

The existing upload byte budget applies to the whole session, including pending
reservations. Multipart metadata is limited to 8192 bytes and total transport
to the byte budget plus 16384 bytes. A persistent reservation commits before
writing to the shared `UploadStore`, then receipt and job commit together.
Storage/queue failure leaves a tracked pending key that can be retried, not a
successful receipt or an orphan. Session locks serialize upload commits, Stop,
worker reads and retention. There is no filesystem/PostgreSQL distributed
transaction. Opt-in retention removes all guest capture bytes with the workspace
and cleans unacknowledged reservations after close/expiry even for accounts.
Accepted account content remains excluded by BC-D06.

Run `uv run --frozen pytest -q tests/recovery/test_captures.py` with the local
test database configured. The tests use synthetic bytes only; no provider calls,
physical capture evidence, deployment or retention-policy approval is implied.

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
| `POST /v1/principals/link` | Body `{"provider": "google", "id_token": ...}`, bearer-authenticated as the calling guest ([BC-D07](../docs/decisions/BC-D07-account-link.md)). The token is verified against `OVRLY_GOOGLE_CLIENT_ID`. Unknown subject: the caller is upgraded in place (`kind` becomes `account`, credential stays valid, every object keeps its owner) and the response is `200 {principal_id, kind, linked: true, merged_saved_reports, credential: null}`; repeating it is the same 200. Subject already owned by another principal A: in one transaction the caller's explicitly saved reports move to A (`transfer_saved_reports`, zero until #33 adds the table), the caller's credentials are revoked, `merged_into` is recorded, and the response carries `principal_id = A` plus a new `credential` for A. Investigations, uploads and idempotency keys stay with the revoked guest. Already linked to a different subject: 409 `ACCOUNT_ALREADY_LINKED`. Bad token: 401 `INVALID_ID_TOKEN`. No client ID configured: 503 `ACCOUNT_LINK_UNAVAILABLE`. |
| `POST /v1/uploads` | Declares `size_bytes`, `sha256` and optional `content_type`; returns a scoped `target`, `max_bytes` and `expires_at`. Over the byte limit: 413 `UPLOAD_TOO_LARGE`. |
| `PUT /v1/uploads/{id}/content` | Streams raw bytes to the target while holding the upload row lock, so a concurrent completion waits for the whole body. Exceeding the smaller of the limit and the declared size discards the partial content with 413. Expired: 410 `UPLOAD_EXPIRED`. |
| `POST /v1/uploads/{id}/complete` | Re-reads the stored bytes and compares size and SHA-256 with the declaration. Mismatch deletes the bytes and returns 409 `UPLOAD_MISMATCH`; missing bytes return 409 `UPLOAD_CONTENT_MISSING`. Completing twice returns the same 200. |
| `POST /v1/investigations` | Requires `Idempotency-Key` (400 if missing or longer than 200 characters). Body: `{"source": {"kind": "url", "url": ...}}` or `{"source": {"kind": "upload", "upload_id": ...}}`, each with optional `duration_ms`. Writes the investigation, the key and the `intake` job in one transaction before answering 202. Same key and body replays the original 202 body; same key with a different body is 409 `IDEMPOTENCY_KEY_REUSED`. Keys are scoped per owner. |
| `GET /v1/investigations` | Newest-first list of the caller's investigations (at most 100). |
| `GET /v1/investigations/{id}` | `state`, `stage`, a coverage placeholder, `version` and a safe `error` object or `null`. `state` reflects the intake job: `queued` or `leased` read as `queued`, `running` as `running`, `failed` as `failed` with a `PROCESSING_FAILED` error unless a specific code was recorded, `cancelled` or `deleted` as `cancelled`; a published job leaves the stored state. Lease owners, fencing tokens, retry classes and failure types are never exposed. |

Every route except guest minting requires `Authorization: Bearer <token>`.
Missing credentials return 401 `AUTHENTICATION_REQUIRED`; malformed, unknown or
revoked ones (including a guest credential revoked by a second-device link)
return 401 `INVALID_CREDENTIAL`, both with `WWW-Authenticate`.
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
decided. Uploaded media is development data on the local disk; consent must be
recorded separately and automatic retention requires the explicit opt-in above.

Recorded investigations are handed to the queue as described under
[Durable jobs and recovery](#durable-jobs-and-recovery); until BE-07 (#20)
adds media stages they stay `queued` at stage `intake` after the intake job
publishes. Account linking follows
[BC-D07](../docs/decisions/BC-D07-account-link.md): identity is verified
server-side from the Google ID token (`services/api/auth/google.py`), and
`services/api/auth/linking.py` performs the upgrade or second-device merge in
one transaction, with every write restricted to the calling principal. Quotas
beyond the two limits and deletion remain open (#77 retention, #75 user
deletion). `packages/contracts` holds the shared error shape, the voice-actions schemas
and, from BE-03 (#15), the upload, investigation, job, capture, report, claim,
evidence and assessment schemas with their enums and the OpenAPI document. The
intake and account-link request and response models in `services/api/schemas.py`
are mirrored there; the new read models (`Claim`, `Evidence`, `Assessment`,
`ReportVersion`, `JobSummary`, `InvestigationReadModel`) generate the six
result fixtures through `packages/contracts/roundtrip.py`, and
`tests/test_contract_roundtrip.py` fails when a model and its fixture drift.
No route emits the read models yet: `GET /v1/investigations/{id}` still returns
`InvestigationResponse`, and adopting `InvestigationReadModel` (adding
`processing_status`, `job` and `report`) is #33 work.

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
expiry and hash mismatch, the job-state view of investigations, account
linking with an injected token verifier (in-place upgrade, idempotent repeat,
second-device merge, the owner invariant and the Google verifier's outcome
mapping with a patched library call), and the shared error shape validated
against `packages/contracts/schemas/error.schema.json`. The test database is
migrated to `head` once per session. No provider keys, Google calls or
personal media are needed; upload tests use synthetic bytes in a temporary
directory.

Ruff includes security rules (`S`) and rejects bare `type: ignore` comments
(`PGH003`); MyPy also enables `ignore-without-code`. Only pytest's `S101`
assertion rule is ignored under `tests/`. Two fixed test subprocess calls have
line-specific, explained `S603` annotations; production code has no security-rule
exemptions.

The optional `quality` group adds locked pre-commit, actionlint, zizmor and
pip-audit tooling without changing runtime dependencies; the optional
`contracts` group adds `openapi-spec-validator` for the
[contracts package](../packages/contracts/README.md#validation) OpenAPI check.
From the repository root:

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
server round-trip check (the full contract gate, including OpenAPI, spectral,
oasdiff and the Android tests, is the separate **Contract checks** workflow),
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

The cancel/delete API and cross-owner recovery cases implement
[BE-04 part 3 / #75](https://github.com/natnael-solomon/ovrly/issues/75) under
[BE-04 / #16](https://github.com/natnael-solomon/ovrly/issues/16); the engine,
retry classes and recovery cases are described under
[Durable jobs and recovery](#durable-jobs-and-recovery).

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

### Provider, hosting and BE-08 decisions

The provider order (Scholarxiv `auto:cheap`, then Groq `openai/gpt-oss-20b`
as the only claim fallback; Gemini not selected), the EthioDeploy Free hosting
choice, the limited BE-08 development go and the account, entitlement and
hosting evidence behind them are recorded in
[BC-D03](../docs/decisions/BC-D03-provider-hosting.md). That record is
Proposed: authored from this experiment by Neb-iyu (BE-01 owner) on
2026-10-04 and awaiting confirmation by the product owner, natnael-solomon.
This README keeps the runner, its inputs and the measurement procedure. The
original #11 checklist also cites BC-D04; that reference does not redefine the
canonical [BC-D04 Voxide decision](../docs/decisions/BC-D04-voxide-route.md).
No provider switching has been added to the API, worker or this runner;
adapter orchestration belongs to BE-08 and must keep the same validation
contract on every route.

### Measured result (local run)

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

The figures in this table come from an uncommitted local run. Its recordings
live under ignored `.scratch/router/<run-id>/` (manifest, `attempts.jsonl`,
`results.json` and `summary.json`) and are not in the repository, so a reviewer
cannot verify them from this checkout. BC-D03 was confirmed by the product
owner on 4 October 2026 and stays Conditional until a redacted metrics-only
`summary.json` (no transcripts, requests or responses) or the run id together
with the manifest SHA-256 and summary SHA-256 is committed and linked from the
record; then it moves to Accepted. Run summaries keep
`decision: pending_team_approval`; the confirmed go in BC-D03 supplements them
rather than rewriting them.

Semantic review found missed claims, incorrect rejection/negation, and
hypothetical context promoted to fact, including in structurally valid output.
Quote-reference and prompt-revision pilots did not establish a consistent
held-aside improvement and were not adopted. Their provisional reference labels
are agent-authored, not independent human gold.

Cassettes stay local; fixture publication requires rights and privacy review
and explicit authorization. No real transcripts or account identifiers have
been promoted into committed fixtures.

Offline validation of this runner is the focused command above, which passed
212 tests on 2026-10-04 from `backend/`:

```sh
uv run --frozen pytest -q tests/test_router_experiment.py tests/test_extraction_contract.py
```

The full PostgreSQL suite, migrations and the coverage comparison against
`main` run in **Backend checks**; see [Local checks](#local-checks).

## Stack record and boundaries

This implements the infrastructure choice in
[BE-02 / #12](https://github.com/natnael-solomon/ovrly/issues/12):
Python 3.11, uv, FastAPI/Uvicorn, PostgreSQL 16, SQLAlchemy asyncio/Psycopg and
Alembic, with a single Python codebase for API and worker. The issue associates
this stack with `BC-D03` / `RFC-D41-D43`. This records the implemented stack,
not approval of the remaining infrastructure or hosting decisions. The full
decision-log task remains #5; provider/hosting evidence is proposed in
[BC-D03](../docs/decisions/BC-D03-provider-hosting.md) and continues in #21.

Keep provider credentials and private media out of Git; future media uploads
require explicit consent and a retention policy. No hosting entitlement or
deployment has been verified by this bootstrap. [Android builds independently](../android/README.md#setup).
