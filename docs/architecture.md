# Repository architecture

## Implemented boundary

`android` is a standalone Gradle root with one `:app` module.

| Package | Responsibility |
| --- | --- |
| `capture` | Projection/playback capture, bounded temporary output and lifecycle |
| `contract` | Typed models and the production parser for the shared `packages/contracts` schemas (voice-actions slice today) |
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

Jobs live in PostgreSQL with an idempotent stage key `(version, stage, input hash)`, are claimed with `FOR UPDATE SKIP LOCKED` and carry a lease with a fencing token plus a cancellation/deletion generation. Publishing a stage result is compare-and-set against both, in the same transaction as the result row, so a stale or late worker can never publish. The state machine (queued, leased, running, published, cancelled, deleted, failed, with requested versus effective cancellation) is property-tested. Stage handlers report failures as typed retry classes (transient, rate limited, non-retriable input, invalid model schema, unknown outcome); the worker schedules bounded, jittered retries through the job's availability time and records the class on exhaustion. An unknown outcome is retried only when the handler recorded the provider request id before the call, so the next attempt reconciles by id instead of calling again. Database or network errors during a stage leave the outcome unknown: the lease is released or expires and the job is re-leased, never failed. Recovery cases run in CI as **Backend recovery**; see the [backend README](../backend/README.md#durable-jobs-and-recovery). Cancel/delete endpoints and the cross-owner-read invariant are #75; pipeline stages are later tasks. Nothing here establishes hosting entitlement.

`services/api/auth` owns guest principals, opaque bearer credentials (stored as digests) and the owner-scoped loader every object route uses; another owner's object is a 404. `services/api/routes` exposes `/v1/principals/guest`, `/v1/uploads` and `/v1/investigations`; `services/api/errors.py` gives every failure the shared contract error shape `{code, message, retryable, action, request_id}` from [`packages/contracts`](../packages/contracts/README.md). Upload bytes go through the `UploadStore` interface in `services/storage.py` (local filesystem now, object storage pending BC-D03). Investigation creation writes the record and its idempotency key in one transaction before answering 202; `services/api/intake.py` is the hook where the job engine will enqueue inside that transaction. The intake request and response models in `services/api/schemas.py` remain backend-owned Pydantic until #15 exports them to `packages/contracts`. Tables are defined in `services/models.py` and created by migration `0003_identity_intake`.

Recorded investigations are not processed yet: hand-off into the job queue, account linking (BC-D07), quotas and deletion are the second BE-05 PR.

Backend CI checks the frozen environment, lint/types, PostgreSQL/migrations and service coverage. Known documentation-only changes skip execution but report the final check. Coverage is compared with remeasured `main`; a missing pre-bootstrap baseline is disclosed. Android coverage, future-module evidence and required-check activation remain #13.

## Future integration boundary

Before connecting Android, agree a versioned API contract with validated schemas and compatibility tests: captured intervals, timestamped segments, ordered claims, evidence citations, job states, cancellation and explicit errors. FastAPI exposes bootstrap OpenAPI, health and the `/v1` identity and intake endpoints. The first slice of that contract, the draft voice-actions request/response schemas, shared error shape and synthetic fixtures, lives in [`packages/contracts`](../packages/contracts/README.md); the intake models are backend-owned Pydantic until #15 exports them, and the remaining schemas and the Contract checks gate are #15.

`packages/contracts` is the single source for that contract. Its first slice (BE-13, #67) is the voice-actions request/response schema with synthetic fixtures. The Android `contract` package parses that slice with the production parser and reads the committed fixtures directly through Gradle test resources; see the [Android README](../android/README.md#contract-models-and-fixtures). The investigation, job, report, claim and evidence schemas remain #15 work, and no endpoint is called.

Hosted model weights stay with the provider. Credentials stay on the server; prompts and adapters belong in the backend. Evaluation fixtures live in root `evaluation/`, independently of backend implementation. Voxide remains a separate companion-navigation path. The directory layout enables no capture upload.

## Evaluation-data boundary

[`evaluation/`](../evaluation/README.md) defines version-2 JSON Schema/JSONL contracts for provenance, one whole-clip occurrence pass and a final adjudication per clip. A second reviewer or model-blind human annotation is not required; actual authorship and review limitations remain explicit. The standard-library validator checks snapshot integrity, references, intervals and declared creator/topic/repost isolation across dev/test.

The eleven-clip RES-06 `draft/` snapshot has explicit draft validation, per-review status, source/timing provenance and separate main-argument assessments. Pending rights, uncertain modality/language and incomplete coverage do not pass frozen validation. Synthetic examples only test the contract; RES-01 still requires the reviewed 10-20-clip corpus.

Metadata-only CI never fetches media or calls providers. Local media verification additionally checks byte hashes. Rights, declared review provenance, scenario coverage and undeclared leakage require human sign-off. No validation mode scores a pipeline; normalized claims are not ASR transcripts or OCR-box ground truth.

## Development ownership

Android builds independently of backend dependencies. Backend platform and research work share one Python project; client/server contract changes need both sides' review. Product scope, task tracking, hackathon dates and the Voxide route are recorded in [docs/decisions](decisions/README.md).

Generated builds, caches, APKs and machine configuration stay out of Git. Use ignored root `.local` for personal tooling/media and `.scratch` for disposable experiments. Version shared configuration and approved test fixtures; keep evaluation media outside version control by default.
