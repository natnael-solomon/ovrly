# Repository architecture

## Implemented boundary

`android` is a standalone Gradle root with one `:app` module.

| Package | Responsibility |
| --- | --- |
| `capture` | Projection/playback capture, bounded temporary output and lifecycle |
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

`backend` is one Python 3.11/uv project with FastAPI, shared settings and SQLAlchemy asyncio/Psycopg. `services/api` owns the API lifespan; `services/worker` runs standalone or as an optional lifespan task (`OVRLY_EMBED_WORKER=1`). The [Linux/WSL helper](../backend/README.md#local-setup-linux--wsl) starts PostgreSQL 16 in Compose, applies Alembic migrations and runs the API and embedded worker natively.

`/healthz` checks the database and, when enabled, the embedded worker. Failures return a safe 503; failed embedded-worker startup prevents API startup. API-only readiness does not monitor a separate worker. Shutdown stops owned tasks and closes database connections. The migration baseline creates no product tables.

This is lifecycle scaffolding. Jobs, leases, recovery and lease draining remain #16. It does not establish hosting entitlement or durable processing.

Backend CI checks the frozen environment, lint/types, PostgreSQL/migrations and service coverage. Known documentation-only changes skip execution but report the final check. Coverage is compared with remeasured `main`; a missing pre-bootstrap baseline is disclosed. Android coverage, future-module evidence and required-check activation remain #13.

## Future integration boundary

Before connecting Android, agree a versioned API contract with validated schemas and compatibility tests: captured intervals, timestamped segments, ordered claims, evidence citations, job states, cancellation and explicit errors. FastAPI exposes bootstrap OpenAPI and health endpoints, but no product contract or product endpoints exist.

Hosted model weights stay with the provider. Credentials stay on the server; prompts and adapters belong in the backend. Evaluation fixtures live in root `evaluation/`, independently of backend implementation. Voxide remains a separate companion-navigation path. The directory layout enables no capture upload.

## Evaluation-data boundary

[`evaluation/`](../evaluation/README.md) defines version-2 JSON Schema/JSONL contracts for provenance, one whole-clip occurrence pass and a final adjudication per clip. A second reviewer or model-blind human annotation is not required; actual authorship and review limitations remain explicit. The standard-library validator checks snapshot integrity, references, intervals and declared creator/topic/repost isolation across dev/test.

The eleven-clip RES-06 `draft/` snapshot has explicit draft validation, per-review status, source/timing provenance and separate main-argument assessments. Pending rights, uncertain modality/language and incomplete coverage do not pass frozen validation. Synthetic examples only test the contract; RES-01 still requires the reviewed 10-20-clip corpus.

Metadata-only CI never fetches media or calls providers. Local media verification additionally checks byte hashes. Rights, declared review provenance, scenario coverage and undeclared leakage require human sign-off. No validation mode scores a pipeline; normalized claims are not ASR transcripts or OCR-box ground truth.

## Development ownership

Android builds independently of backend dependencies. Backend platform and research work share one Python project; client/server contract changes need both sides' review. Product scope, task tracking, hackathon dates and the Voxide route are recorded in [docs/decisions](decisions/README.md).

Generated builds, caches, APKs and machine configuration stay out of Git. Use ignored root `.local` for personal tooling/media and `.scratch` for disposable experiments. Version shared configuration and approved test fixtures; keep evaluation media outside version control by default.
