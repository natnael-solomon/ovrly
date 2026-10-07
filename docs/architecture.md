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
| Stop from the compact overlay | Ask "Continue research in queue" or "Keep only available results" (or keep recording), then pass the answer to capture through `StopChoiceHandler`; the overlay never closes a session itself. |
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

`backend` is one Python 3.12/uv project with FastAPI, shared settings and SQLAlchemy asyncio/Psycopg. `services/api` owns the API lifespan; `services/worker` runs standalone or as an optional lifespan task (`OVRLY_EMBED_WORKER=1`); `services/jobs` holds the durable queue. The [Linux/WSL helper](../backend/README.md#local-setup-linux--wsl) starts PostgreSQL 16 in Compose, applies Alembic migrations and runs the API and embedded worker natively.

`/healthz` checks the database, that the schema is at the Alembic head the build ships, that the upload directory is writable and, when enabled, that the embedded worker is running and has polled or extended a lease within two minutes. It also reports queue depth, the oldest claimable job's age and the worker heartbeat age (RFC section 16 signals) without contacting a provider. Failures return a safe 503 with one reason; failed embedded-worker startup prevents API startup. API-only readiness does not monitor a separate worker. Shutdown stops owned tasks, finishes or releases the worker's in-flight lease and closes database connections. Deployment, smoke, keep-alive and backup are in the [deployment runbook](operations/deployment.md).

Jobs live in PostgreSQL with an idempotent stage key `(version, stage, input hash)`, are claimed with `FOR UPDATE SKIP LOCKED` and carry a lease with a fencing token plus a cancellation/deletion generation. Publishing a stage result is compare-and-set against both, in the same transaction as its result and deferred successors, which inherit the parent job's stored owner. A stale or late worker cannot publish or advance the pipeline. The state machine and typed retries are property-tested; infrastructure failures re-lease work rather than pretending it failed safely. Unknown provider outcomes require verified reconciliation before retry: hosted Groq speech never resends that model and tries the RFC-D27 fallback model once instead. Recovery cases run in CI as **Backend recovery**, including owner isolation and API-driven cancel/delete; see the [backend README](../backend/README.md#durable-jobs-and-recovery). Intake hands uploads to media validation; capture validation still verifies stored chunks and uses its fenced fan-out/fan-in publisher. Upload speech uses internal stage `upload_asr` (public `asr`) so capture's unimplemented `asr` jobs are not claimed by it. Existing evidence and labelled-stub registration remain independent. Nothing here establishes hosting entitlement.

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

`services/api/auth` owns guest principals, digest-only opaque credentials, Google account linking per [BC-D07](decisions/BC-D07-account-link.md), and the owner-scoped loader; another owner's object is a 404. `google.py` verifies ID tokens behind `IdTokenVerifier`; `linking.py` upgrades or merges the calling principal transactionally. Existing principal, upload, investigation, job-action, report/export/reanalysis, save and voice routes are preserved. Errors use the shared `{code, message, retryable, action, request_id}` shape. Upload bytes go through `UploadStore` (`services/storage.py`), currently local filesystem; object storage remains pending BC-D03.

Investigation creation writes its record, idempotency key and one `intake` job in the same transaction before answering 202. `QueueDispatcher` (`services/api/intake.py`) derives its stage key from the investigation ID. The intake handler confirms ownership and defers an upload's media successor until fenced publication; URL references stay intake-only. `default_handlers(store, settings=settings)` dispatches capture and upload validation without replacing the evidence/privacy registry. Investigation reads preserve the full contract read model (`state`, `processing_status`, latest live job and latest report), adding published media coverage and optional timed speech without exposing leases, failure types or local artifact paths.

Pydantic request/read models in `services/api/schemas.py` mirror `packages/contracts` schemas and generate result fixtures. Report routes still emit immutable `ReportVersion` values published by `services/reports.py`. Tables remain in `services/models.py`; migration `0012_asr_requests` follows main's `0011_quotas` without replacing ownership, capture, report or reanalysis migrations.
`services/pipeline/media_validation.py` snapshots and verifies completed upload bytes, probes media, checks decoded video duration, and prepares complete 16 kHz mono PCM16 WAV when audio exists. `services/media/runner.py` bounds command CPU, wall time, output and file size and reaps process groups on cancellation. Private artifacts are linked atomically under the active job lock; only fenced job-result publication makes references usable. Coverage stays `not_started`, with explicit absent audio and pending text for silent video. Opt-in retention removes job artifacts at guest/legacy expiry or tombstone purge; crash-left preparation scratch directories remain operator-cleaned. Device text and multimodal aggregation are later BE-07 slices.

The additional `upload_asr` handler (`services/pipeline/speech.py`, contract stage `asr`) is wired as a fenced
successor only when hosted speech is explicitly enabled and audio exists.
`services/asr/groq.py` implements bounded Groq HTTP. `services/asr/fallback.py`
applies RFC-D27 (decision 0004) to uploads and capture chunks alike: the primary
`whisper-large-v3-turbo` request, then one `whisper-large-v3` request after any
primary failure, then explicit `ASR_UNAVAILABLE` with the last attempt's reason.
There is no other provider and no local model. `services/asr/reservations.py` and
migration `0012_asr_requests` store per-model account quota reservations and
outcomes before publication. An uncertain interrupted request is never resent to
the same model (no Groq reconciliation mechanism is verified); the run moves on to
the fallback instead of using the generic queue's reconciliation retries.
Known outcomes survive restarts, and completed responses are reused within the
same job/source/model/settings. Quota exhaustion never auto-resumes at reset.
Owner-scoped `speech` reads retain timed segments and provenance separately from
the unchanged preparation coverage. Successful transcription is not completed
analysis. Live use remains disabled pending verified account/model settings;
see [hosted speech](../backend/README.md#hosted-uploaded-speech-disabled-by-default).

Media preparation and optional speech do not claim completed investigation analysis; claim extraction remains separate work. The evidence stages (#27, `services/evidence`, `services/providers`) turn published claims into evidence and assessments through Scholarxiv retrieval, open-access passages, a router relation step and citation validation. Until #25 publishes real claims, development-only `OVRLY_STUB_REPORTS` publishes labelled fixture reports; its upload intake path does not launch hosted speech. Saved reports move to the account on a second-device merge. Every outbound evidence and hosted speech request goes through the SSRF guard in `services/providers/egress.py` (one checked resolution per connection, checked redirects, size cap); the REPO-06 (#28) security tests are listed in the [backend README](../backend/README.md#security-tests-repo-06-28).

`services/api/routes/device_text.py` accepts a separate backend-only v1 handoff
for owned, validated uploaded video. Bounded immutable batches preserve
original-media timestamps, upright normalized boxes, recognizer/sampling facts
and explicit recognized-empty/failed frames. Explicit completion also supports
zero batches. `upload_text` is migration `0013`, after `0012_asr_requests`;
principal/investigation/job locks serialize admission with privacy and job actions.
Its unclaimed `upload_device_text` job is a cancellation/deletion fence, not
published research. Intake, media, uploaded-speech and text job deletion erases
content and prevents replay from recreating it, even after job tombstone purge.
Prerequisite deletion persists a content-free `upload_text` fence with no valid
media reference, including before first admission. Its investigation FK survives
account job purges and cascades on workspace expiry. Admission uses a non-key
investigation row lock so deletion can insert that FK-backed fence without a
job/investigation lock inversion. Text remains separate
from speech/captions. No server OCR, timeout or aggregation is added. Shared
synthetic fixtures exercise the approved backend protocol; Android upload sending
and physical-device end-to-end verification remain separate.

BE-08 (#25) supplies the internal `enqueue_extraction` handoff and typed
`LlmAdapter` boundary. One versioned timed observation window yields
source-grounded provisional occurrences, with optional shared-contract
interpretation metadata and no verdicts. Production activation is off until
route eligibility and input authorization are verified. `jobs.stage_data`
persists attempt allowance and validated artifacts; an unresolved provider
attempt fails explicitly instead of repeating inference. `StageResult` adds
an optional transactional publication callback to the existing dictionary
handler contract, so extraction report and job-result publication share the
queue fence and transaction. The observation-envelope timing, strict grounding,
single repair and whole-input reconciliation boundaries are described
in the [backend README](../backend/README.md#provisional-claim-extraction).
Eligible provider failures now use durable, bounded Scholarxiv recovery and
independently authorized, extraction-only Groq fallback. Restrictions and
unknown outcomes fail closed. Shared pre-send accounting records every
completion/decision/feedback request. The internal `submit_observations` producer
adds bounded adaptive overlapping batches and a durable per-investigation ledger
in `extraction_runs`. Pre-send reservations share the job-checkpoint transaction;
concurrent windows cannot spend the reconciliation reserve. Cumulative provisional
publication deduplicates exact source identities without collapsing later
repetitions. Owner-scoped investigation/capture reads expose pending, processed,
skipped and failed source intervals separately from findings. Capture Stop fences
this fixture-backed path too. Reports expose optional diagnostic processing
provenance. Numeric production budgets and real ingestion remain
gated; the legacy single-window handoff does not automatically gain run budgeting.

`services/pipeline/reconciliation.py` registers accepted upstream work and schedules
one quality-only job after sealed observations and terminal upstream/extraction work.
Captures additionally require actual close and settled chunk work. The shared
pre-send ledger protects total capacity; lease-safe provider waits and invalid-output
feedback are shared with extraction. No available context is silently truncated.
Durable artifacts, capture Stop and report-snapshot fencing prevent resends and stale
publication. Corrections retain linked appearances and immutable history, remove stale
evidence/assessments, and hand changed claim IDs to later reassessment. Report
interpretation finality and live reconciliation failure are additive contract metadata,
not completed assessment or full modality coverage.

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
publication of both queues one `claim_extraction` job for that chunk, which
admits the chunk's committed speech and device text to the extraction ledger when
extraction is enabled. Stage keys preserve
per-capture/sequence idempotency, publication shares
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

## Opt-in backend admission budgets

`services/quotas.py` applies the BC-D06 (#22) daily checks/upload reservations
and active-check admission limits in the API's existing transactions, serialized
by principal before owned-object locks. The principal admission lock is
`FOR NO KEY UPDATE`, so it never blocks a worker's foreign-key insert of a
principal-owned job; the lock order is principal, owned objects, reanalysis
request, jobs. `quota_usage` retains one UTC-day row
per principal; deletion of content does not refund it. Existing replays and
accepted chunk retries do not spend quota again. The same gate is the global stop:
`services/provider_budgets.py` pauses new intake while any configured provider
is near exhaustion (the Scholarxiv bucket, the Groq speech `asr_requests`
windows or the Groq fallback `groq_llm:*` buckets) and derives `Retry-After`
from refill, reservation age-out or a provider hold.
`services/providers/budget.py` reuses the shared bucket, adds a per-bucket
period, all-or-nothing `SharedBudget` charges and expiring, lease-fenced
`provider_slots` for opt-in outbound concurrency; a busy slot or an
empty bucket is waited for with jittered backoff up to the bucket's maximum wait
before the stage is rescheduled, and no connection is
held while waiting or over the network. Papers, Router and feedback all use that path;
with quotas on, extraction and reconciliation take their provider units before
recording each request. `services/quota_summary.py` is an operator-only read
command, not an API endpoint or provider dashboard; upstream balances are reported
as unknown with a local estimate. See the [backend quota setup](../backend/README.md#opt-in-admission-quotas-22)
and the [BC-D06 policy](decisions/BC-D06-retention.md#admission-and-provider-budgets-22).

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
