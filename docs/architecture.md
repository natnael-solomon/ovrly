# Repository architecture

## Implemented boundary

`android` is a standalone Gradle root with one `:app` module.

| Package | Responsibility |
| --- | --- |
| `capture` | Projection/playback capture, bounded temporary output and lifecycle |
| `contract` | Typed models and the production parsers for the shared `packages/contracts` schemas: voice actions, uploads, investigations with jobs and report versions, claims, evidence, assessments, capture sessions and chunks |
| `overlay` | Floating-window lifecycle, movement and controls |
| `share` | Validation of video URIs and URL references |
| `ui` | Companion screens, production controls and isolated sample/gallery content |
| `voice` | Experimental, explicitly configured Voxide companion navigation |

Android owns permissions and media access. Hiding controls does not stop capture. Stopping capture releases media access; research cancellation is a separate, future action.

`AppShell` provides Your space, Explore and Settings. The activity owns the selected tab; external capture/share intents open Settings. Capture and voice keep their existing state stores. Capture keeps an active-session shortcut across tabs; voice shows its state on the header orb. Sample saves use restored UI state, not capture storage or a provider. Tab and report scroll positions have separate saveable scopes.

See the [Android README](../android/README.md) for builds and [UI maintenance](android-ui.md) for appearance, artwork and splash behavior.

## Overlay material and demo boundaries

Compact controls and the larger demo are mutually exclusive modes of one foreground service. Production controls retain real callbacks. The demo exposes simulated claims/evidence, a real close action and no recording or provider access.

| Transition | Required behavior |
| --- | --- |
| Open a demo from the activity | Confirm stopping any real session, wait for capture cleanup and verify the activity is still foregrounded. |
| Open a demo through the service | Reject entry during capture. |
| Start capture or voice from the companion | Close the demo. Capture starting through another route also closes it. |
| Close a demo | Do not restart capture or voice. |
| Change theme or opacity | Update the active window without restarting capture. |

### Window and layout

`OverlayWindow` uses a service-owned, non-focusable `Dialog` with `TYPE_APPLICATION_OVERLAY`. Its public `Window`/`DecorView` supports Android 12's `setBackgroundBlurRadius` for content behind the panel. Blurring a Compose node cannot provide cross-window blur.

Compact controls remain wrap-content. The demo has 16 dp side margins, stays below half the usable display height and starts at the bottom with a 16 dp margin. System bars and cutouts are excluded. Drag the header vertically; scroll the body separately. Close stays outside the scroll area.

The host does not set `FLAG_BLUR_BEHIND` or dim surrounding content. It observes blur availability and removes the listener on dismissal. Android 10/11, disabled/unsupported blur and higher-opacity mode use opaque surfaces. Gallery materials are synthetic and do not establish device blur behavior.

### Device checks before merging

Use authorized devices and approved content:

- Android 10/11 fallback; supported Android 12+ blur limited to the panel; runtime blur disablement and battery saver.
- Header dragging versus body scrolling, outside-touch pass-through, rotation and 200% text.
- Permission revocation, close/notification/task cleanup and confirmed versus cancelled demo entry during capture.
- Theme persistence and no automatic restart of media access after closing the demo.

Unit tests cover palette contrast, fallback decisions and demo-entry policy. They do not test GPU composition or physical-device behavior.

## Backend foundation

`backend` is one Python 3.11/uv project with FastAPI, shared settings and SQLAlchemy asyncio/Psycopg. `services/api` owns the API lifespan; `services/worker` runs standalone or as an optional lifespan task (`OVRLY_EMBED_WORKER=1`); `services/jobs` holds the durable queue. The [Linux/WSL helper](../backend/README.md#local-setup-linux--wsl) starts PostgreSQL 16 in Compose, applies Alembic migrations and runs the API and embedded worker natively.

`/healthz` checks the database and, when enabled, the embedded worker. Failures return a safe 503; failed embedded-worker startup prevents API startup. API-only readiness does not monitor a separate worker. Shutdown stops owned tasks, finishes or releases the worker's in-flight lease and closes database connections.

Jobs live in PostgreSQL with an idempotent stage key `(version, stage, input hash)`, are claimed with `FOR UPDATE SKIP LOCKED` and carry a lease with a fencing token plus a cancellation/deletion generation. Publishing a stage result is compare-and-set against both, in the same transaction as the result row, so a stale or late worker can never publish. The state machine (queued, leased, running, published, cancelled, deleted, failed, with requested versus effective cancellation) is property-tested. Stage handlers report failures as typed retry classes (transient, rate limited, non-retriable input, invalid model schema, unknown outcome); the worker schedules bounded, jittered retries through the job's availability time and records the class on exhaustion. An unknown outcome is retried only when the handler recorded the provider request id before the call, so the next attempt reconciles by id instead of calling again. Database or network errors during a stage leave the outcome unknown: the lease is released or expires and the job is re-leased, never failed. Recovery cases run in CI as **Backend recovery**, including owner isolation and API-driven cancel/delete; see the [backend README](../backend/README.md#durable-jobs-and-recovery). The `intake` stage is the only production stage and media stages are later tasks. Nothing here establishes hosting entitlement.

`services/api/routes/jobs.py` exposes owner-scoped cancellation and deletion.
Migration `0006_job_ownership` gives jobs nullable principal ownership and a
durable cancellation receipt; the intake dispatcher records the investigation's
owner on every new `intake` job, while jobs enqueued before the migration stay
ownerless and API-inaccessible.
Authorization, mutation and receipt storage share a locked transaction.
Cancellation replays the original requested/effective outcome. Deletion revokes
access, fences workers and clears the database payload/result; provider data,
uploads and backups are outside its stated cleanup scope. Tombstones remain.
Recovery fixtures check cross-owner reads/actions after every case, and API
tests prove cancellation during retrieval and rejection of delayed publication.

`services/api/auth` owns guest principals, opaque bearer credentials (stored as digests), Google account linking per [BC-D07](decisions/BC-D07-account-link.md) (`google.py` verifies the ID token behind an `IdTokenVerifier` protocol; `linking.py` upgrades the caller in place or merges a second device into the existing account in one transaction, every write restricted to the calling principal) and the owner-scoped loader every object route uses; another owner's object is a 404. `services/api/routes` exposes `/v1/principals/guest`, `/v1/principals/link`, `/v1/uploads`, `/v1/investigations` and the `/v1/jobs` actions; `services/api/errors.py` gives every failure the shared contract error shape `{code, message, retryable, action, request_id}` from [`packages/contracts`](../packages/contracts/README.md). Upload bytes go through the `UploadStore` interface in `services/storage.py` (local filesystem now, object storage pending BC-D03). Investigation creation writes the record, its idempotency key and one `intake` job in one transaction before answering 202: `services/api/intake.py` holds the `QueueDispatcher` that calls `JobQueue.enqueue` on the same connection with a stage key derived from the investigation id, so neither a replay nor a dispatcher failure can leave the record and the job out of step. `services/pipeline/intake.py` is the worker-side `intake` stage registered by `default_handlers()`; it confirms the owned record and publishes a placeholder result until BE-07 (#20) adds media stages. Investigation reads derive `state` from the job (`queued`, `running`, `failed`, `cancelled`) without exposing leases, fencing tokens or failure types. The intake and account-link request and response models in `services/api/schemas.py` are mirrored by the JSON Schemas and the OpenAPI document in `packages/contracts` (BE-03, #15); the read models for claims, evidence, assessments, report versions and job summaries live there too and generate the contract's result fixtures, but no route emits them yet. Tables are defined in `services/models.py` and created by migrations `0003_identity_intake` and `0005_account_link`.

Investigations are queued but not analysed yet: media stages are BE-07 (#20), saved reports and their transfer on account merge are BE-10 (#33), quotas and retention are #77.

Backend CI checks the frozen environment, lint/types, PostgreSQL/migrations and service coverage. Known documentation-only changes skip execution but report the final check. Coverage is compared with remeasured `main`; a missing pre-bootstrap baseline is disclosed. Android coverage, future-module evidence and required-check activation remain #13.

## Future integration boundary

Before connecting Android, agree a versioned API contract with validated schemas and compatibility tests: captured intervals, timestamped segments, ordered claims, evidence citations, job states, cancellation and explicit errors. FastAPI exposes bootstrap OpenAPI, health and the `/v1` identity and intake endpoints. That contract lives in [`packages/contracts`](../packages/contracts/README.md): the voice-actions slice, the shared error shape, the upload, investigation, job, capture-session, report-version, claim, evidence and assessment schemas with one `$def` per enum, six result fixtures generated from the backend read models, intake samples, and a hand-maintained OpenAPI 3.1 document whose schemas reference those files. The required **Contract checks** workflow validates all of it (spectral, oasdiff against the base, server round trip, Android contract tests) on every PR.

`packages/contracts` is the single source for that contract. The Android `contract` package parses every schema in it with production parsers (`VoiceActionCodec`, `InvestigationCodec`, `UploadCodec`, `CaptureCodec`), one Kotlin enum per contract enum with an `UNKNOWN` fallback that is never a success state, and constructors that enforce the schema rules, including the investigation `oneOf` branches so a failure is never read as a finding; its unit tests read the committed fixtures and schemas directly through Gradle test resources and cross-check the enums and model members against them, see the [Android README](../android/README.md#contract-models-and-fixtures). This is the #62 deliverable of #15; no endpoint is called, and networking, Room and reconciliation are #18.

Hosted model weights stay with the provider. Credentials stay on the server; prompts and adapters belong in the backend. Evaluation fixtures live in root `evaluation/`, independently of backend implementation. Voxide remains a separate companion-navigation path. The directory layout enables no capture upload.

## Evaluation-data boundary

[`evaluation/`](../evaluation/README.md) defines version-2 JSON Schema/JSONL contracts for provenance, one whole-clip occurrence pass and a final adjudication per clip. A second reviewer or model-blind human annotation is not required; actual authorship and review limitations remain explicit. The standard-library validator checks snapshot integrity, references, intervals and declared creator/topic/repost isolation across dev/test.

The eleven-clip RES-06 `draft/` snapshot has explicit draft validation, per-review status, source/timing provenance and separate main-argument assessments. Pending rights, uncertain modality/language and incomplete coverage do not pass frozen validation. Synthetic examples only test the contract; RES-01 still requires the reviewed 10-20-clip corpus.

`reviewed-draft/` preserves that original snapshot and records later user
confirmations and supplied-card reconciliation. A `user-confirmed-negative`
assessment records claim absence without certifying language, rights or full
review completion; eligible final decisions contradicting it fail validation.

`freeze-candidate/` adds the final supplied-card discoveries and reconciles
social speech intervals to reviewed subtitle cues. Original annotations and
previous snapshots are preserved. Candidate naming does not confer frozen
status: the manifest remains `draft` while rights and acceptance are incomplete.

Metadata-only CI never fetches media or calls providers. Local media verification additionally checks byte hashes. Rights, declared review provenance, scenario coverage and undeclared leakage require human sign-off. No validation mode scores a pipeline; normalized claims are not ASR transcripts or OCR-box ground truth.

`corpus-local/` is the subsequent owner-accepted local snapshot
(`kind: frozen-local`). Its manifest adds the updated rights-checklist hash and
the owner's local-use acceptance while preserving all candidate JSONL bytes.
Validation uses `--frozen-local`, keeps provenance/media/split checks, and
requires known final modalities and confirmed negatives, but applies the draft
rules to the preserved rows: every clip still has pending rights clearance,
unconfirmed coverage and provisional review. The separate full-coverage
`--frozen` mode is not relaxed and rejects this snapshot; local acceptance does
not authorize hosted processing, training or redistribution and does not
complete RES-01.

## Development ownership

Android builds independently of backend dependencies. Backend platform and research work share one Python project; client/server contract changes need both sides' review. Product scope, task tracking, hackathon dates and the Voxide route are recorded in [docs/decisions](decisions/README.md).

Generated builds, caches, APKs and machine configuration stay out of Git. Use ignored root `.local` for personal tooling/media and `.scratch` for disposable experiments. Version shared configuration and approved test fixtures; keep evaluation media outside version control by default.
