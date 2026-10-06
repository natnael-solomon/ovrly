# Repository architecture

## Implemented boundary

`android` is a standalone Gradle root with one `:app` module.

| Package | Responsibility |
| --- | --- |
| `capture` | Projection/playback capture, 10-second chunks on the capture timeline, on-device screen-text recognition with local-only frames, bounded local storage, chunk upload scheduling and lifecycle |
| `contract` | Typed models and the production parsers for the shared `packages/contracts` schemas: voice actions, uploads, investigations with jobs and report versions, claims, evidence, assessments, capture sessions and chunks |
| `overlay` | Floating-window lifecycle, movement and controls |
| `share` | Share intake: on-device checks, private staging, upload and investigation creation, duplicate detection |
| `data` | API client on OkHttp (guest credential, request ids, cold start, typed errors), the Room store of shares, local job states and cached report versions, the investigation repository and start/foreground reconciliation |
| `ui` | Companion screens, production controls and isolated sample/gallery content |
| `voice` | Experimental, explicitly configured Voxide companion: tab navigation and the BC-D04 voice actions through `data/VoiceApi.kt` |

Android owns permissions and media access. Hiding controls does not stop capture. Stopping capture releases media access; research cancellation is a separate, future action.

`AppShell` provides Your space, Explore and Settings ([RFC-D22](decisions/RFC-D22-companion-navigation.md)). Your space shows the Inbox and Library of real checks from the Room store, then labeled saved samples; Explore is the sample gallery. The activity owns the selected tab; external capture intents open Settings; shares open the separate ShareIntakeActivity sheet over the source app. Capture and voice keep their existing state stores. Capture keeps an active-session shortcut across tabs; voice shows its state on the header orb. Sample saves use restored UI state, not capture storage or a provider. Tab and report scroll positions have separate saveable scopes.

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
| Stop from the overlay | Ask "Continue research in queue" or "Keep only available results" (or keep examining), then pass the answer to capture through `StopChoiceHandler`; the overlay never closes a session itself. |
| Hide the live results panel | Keep capture running; Stop stays on the pill and in the capture notification. |

The compact overlay's live results panel reads `OverlayStore.liveSource`, which is "not connected" until a capture session starts the polling adapter (`LiveResultsConnection.start`); fixture results are labelled and reachable only from previews, tests and a debug-only launcher. See [Live overlay results](../android/README.md#live-overlay-results).

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

Unit tests cover palette contrast, fallback decisions, demo-entry policy, live claim-state mapping (including `UNKNOWN` never reading as assessed), spoken order, update notices, poll backoff, demo isolation and the Stop choice. They do not test GPU composition or physical-device behavior.

## Backend foundation

`backend` is one Python 3.11/uv project with FastAPI, shared settings and SQLAlchemy asyncio/Psycopg. `services/api` owns the API lifespan; `services/worker` runs standalone or as an optional lifespan task (`OVRLY_EMBED_WORKER=1`); `services/jobs` holds the durable queue. The [Linux/WSL helper](../backend/README.md#local-setup-linux--wsl) starts PostgreSQL 16 in Compose, applies Alembic migrations and runs the API and embedded worker natively.

`/healthz` checks the database and, when enabled, the embedded worker. Failures return a safe 503; failed embedded-worker startup prevents API startup. API-only readiness does not monitor a separate worker. Shutdown stops owned tasks, finishes or releases the worker's in-flight lease and closes database connections.

Jobs live in PostgreSQL with an idempotent stage key `(version, stage, input hash)`, are claimed with `FOR UPDATE SKIP LOCKED` and carry a lease with a fencing token plus a cancellation/deletion generation. Publishing a stage result is compare-and-set against both, in the same transaction as the result row, so a stale or late worker can never publish. The state machine (queued, leased, running, published, cancelled, deleted, failed, with requested versus effective cancellation) is property-tested. Stage handlers report failures as typed retry classes (transient, rate limited, non-retriable input, invalid model schema, unknown outcome); the worker schedules bounded, jittered retries through the job's availability time and records the class on exhaustion. An unknown outcome is retried only when the handler recorded the provider request id before the call, so the next attempt reconciles by id instead of calling again. Database or network errors during a stage leave the outcome unknown: the lease is released or expires and the job is re-leased, never failed. Recovery cases run in CI as **Backend recovery**, including owner isolation and API-driven cancel/delete; see the [backend README](../backend/README.md#durable-jobs-and-recovery). The `intake` stage confirms shared-media intake; capture `media_validation` verifies stored chunk bytes. Codec/media analysis and research stages are later tasks. Nothing here establishes hosting entitlement.

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

`services/api/auth` owns guest principals, opaque bearer credentials (stored as digests), Google account linking per [BC-D07](decisions/BC-D07-account-link.md) (`google.py` verifies the ID token behind an `IdTokenVerifier` protocol; `linking.py` upgrades the caller in place or merges a second device into the existing account in one transaction, every write restricted to the calling principal) and the owner-scoped loader every object route uses; another owner's object is a 404. `services/api/routes` exposes `/v1/principals/guest`, `/v1/principals/link`, `/v1/uploads`, `/v1/investigations`, the `/v1/jobs` actions, the report versions, export and reanalysis under `/v1/investigations/{id}/reports` and `/v1/investigations/{id}/reanalyze`, saves under `/v1/reports`, and `/v1/voice/actions` (BE-10, #33); `services/api/errors.py` gives every failure the shared contract error shape `{code, message, retryable, action, request_id}` from [`packages/contracts`](../packages/contracts/README.md). Upload bytes go through the `UploadStore` interface in `services/storage.py` (local filesystem now, object storage pending BC-D03). Investigation creation writes the record, its idempotency key and one `intake` job in one transaction before answering 202: `services/api/intake.py` holds the `QueueDispatcher` that calls `JobQueue.enqueue` on the same connection with a stage key derived from the investigation id, so neither a replay nor a dispatcher failure can leave the record and the job out of step. `services/pipeline/intake.py` is the worker-side `intake` stage registered by `default_handlers()`; it confirms the owned record and publishes a placeholder result until BE-07 (#20) adds media stages. Investigation reads return the contract read model: `state` derived from the job (`queued`, `running`, `failed`, `cancelled`), `processing_status`, the latest job summary and the latest report version, without exposing leases, fencing tokens or failure types. The intake and account-link request and response models in `services/api/schemas.py` are mirrored by the JSON Schemas and the OpenAPI document in `packages/contracts` (BE-03, #15); the read models for claims, evidence, assessments, report versions and job summaries live there too and generate the contract's result fixtures; the report routes emit `ReportVersion`, written once by `services/reports.py` and never updated. Tables are defined in `services/models.py` and created by migrations `0003_identity_intake`, `0005_account_link` and `0008_reports`.

Investigations are queued but not analysed yet: media stages are BE-07 (#20) and claim extraction is #25. The evidence stages (#27, `services/evidence`, `services/providers`) turn published claims into evidence and assessments through Scholarxiv retrieval, open-access passages, a router relation step and citation validation; until #25 publishes real claims, the development-only `OVRLY_STUB_REPORTS` publishes a labelled fixture report); saved reports move to the account on a second-device merge; quotas and retention are #77.

`services/privacy.py` schedules opt-in retention work on the existing durable
job engine in both worker entry points. It deletes an expired principal's
workspace and stored upload bytes, cascades replay/credential rows, and uses
the same `JobQueue.delete` as the API for fenced result removal. Upload locks
serialize cleanup with streaming; failures leave retryable metadata rather
than reporting complete cleanup. Detached tombstones expire later, without
allowing an old lease to publish. The fixed-lifetime demo policy is
[Proposed BC-D06](decisions/BC-D06-retention.md), disabled by default, not a
permanent-account policy. See the [data map](operations/data-map.md) for every
current storage location and unresolved backup/provider erasure boundaries.

`services/logging.py` supplies content-free logging for API and worker:
allowlisted events, fixed codes, UUID job IDs, hashed request IDs and counts,
with raw access messages, dynamic strings, extras and traceback content
removed from configured sinks. No production log retention provider is
configured. The [PDP checklist](operations/pdp-checklist.md) keeps transfer
and sovereignty obligations separate from successful retention tests.

Backend CI checks the frozen environment, lint/types, PostgreSQL/migrations and service coverage. Known documentation-only changes skip execution but report the final check. The worker, job engine and auth modules each have a 90% line floor, and overall coverage may drop by at most one percentage point against remeasured `main`; a missing pre-bootstrap baseline is disclosed. Android coverage is tracked separately in #76. See the [coverage policy](../backend/README.md#ci-and-coverage) for exact aggregation and absent-module reporting.

## Future integration boundary

BE-06 live capture uses `capture_sessions` and `capture_chunks` under one
investigation/owner. The API reserves a persistent byte key before writing,
then commits each verified receipt and `media_validation` job together.
Capture polling reports missing intervals and client-declared speech/text
coverage, separately from processing. Both worker modes verify each chunk's
stored bytes before close and atomically enqueue `asr` and `device_text`;
publication of both queues one `claim_extraction` job for that chunk.
The handlers for those stages remain #20/#25, so unimplemented stages stay
queued. Stage keys preserve per-capture/sequence idempotency, publication shares
the queue's lease fence, and Stop cancels downstream work under the session lock.
Capture polling derives per-claim states from the latest report, including
explicitly labelled development fixtures; validation alone is not analysis.
Session row locks serialize byte commits, Stop and retention, while queue
fencing prevents cancelled/deleted work from publishing. See the
[capture API](../backend/README.md#incremental-capture-api) for transport,
deadlines and replay semantics. Android packages chunks and uploads them while recording through `CaptureSessionApi` over the AN-03 client (`data/CaptureApi.kt`), and the live overlay polls the capture status and investigation (#26, #31).
`CaptureApiCodec` handles the create/close bodies, multipart metadata and polling
envelope; `CaptureCodec` retains the existing session/chunk parsers.

Before connecting Android, agree a versioned API contract with validated schemas and compatibility tests: captured intervals, timestamped segments, ordered claims, evidence citations, job states, cancellation and explicit errors. FastAPI exposes bootstrap OpenAPI, health and the `/v1` identity and intake endpoints. That contract lives in [`packages/contracts`](../packages/contracts/README.md): the voice-actions slice, the shared error shape, the upload, investigation, job, capture-session, report-version, claim, evidence and assessment schemas with one `$def` per enum, six result fixtures generated from the backend read models, intake samples, and a hand-maintained OpenAPI 3.1 document whose schemas reference those files. The required **Contract checks** workflow validates all of it (spectral, oasdiff against the base, server round trip, Android contract tests) on every PR.

`packages/contracts` is the single source for that contract. The Android `contract` package parses every schema in it with production parsers (`VoiceActionCodec`, `InvestigationCodec`, `UploadCodec`, `CaptureCodec`), one Kotlin enum per contract enum with an `UNKNOWN` fallback that is never a success state, and constructors that enforce the schema rules, including the investigation `oneOf` branches so a failure is never read as a finding; its unit tests read the committed fixtures and schemas directly through Gradle test resources and cross-check the enums and model members against them, see the [Android README](../android/README.md#contract-models-and-fixtures). This is the #62 deliverable of #15. The data package (AN-03 part 1, #18) calls the intake endpoints through these codecs, and keeps shares, local job states and report versions in Room with reconciliation on start and foreground, see [Data layer and share intake](../android/README.md#data-layer-and-share-intake).

Hosted model weights stay with the provider. Credentials stay on the server; prompts and adapters belong in the backend. Evaluation fixtures live in root `evaluation/`, independently of backend implementation. Voxide is a separate companion path; its voice actions go through the backend's `POST /v1/voice/actions`, which owns validation and ownership. The capture transport is implemented on the backend; Android capture-to-network wiring remains separate work.

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
