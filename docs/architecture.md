# Repository architecture

## Implemented boundary

`android` is a standalone Gradle root with one `:app` module. The Kotlin packages remain unchanged:

| Package | Responsibility |
| --- | --- |
| `capture` | Projection/playback capture, bounded temporary output and lifecycle |
| `overlay` | Manual floating-window service, movement and control callbacks |
| `share` | Validation of supported shared video URIs and URL references |
| `ui` | Companion controls, production glass shell and isolated design fixtures |
| `voice` | Experimental, explicitly configured Voxide companion navigation |

Android owns permissions and media access. Hiding controls is not stopping capture. Capture stop releases media access; future research cancellation must remain a separate action. Gallery fixtures are not live results.

Windows builds can use `scripts/android.ps1`, which resolves paths from its own location and restores the caller's environment afterwards. Linux and WSL builds use `sh gradlew` inside `android`. Both use the same pinned Gradle wrapper and dependencies.

## Overlay material and demo boundaries

`AppShell` owns the Your space / Explore / Settings navigation and preview-only sample selections. The activity owns the selected tab so external capture/share intents still land in Settings. Real capture and voice state remain in their existing stores; an active-session shortcut stays visible across tabs. Sample report saves use restored UI state only, never capture storage or a provider. Tab and report scroll positions use separate saveable state scopes.

The compact and demo overlays are mutually exclusive modes of the existing foreground service, not two competing windows. Real controls retain their callbacks; the demo composable has only simulated fixture interactions and a real close action. Entry from the activity confirms any required session stop, waits for capture cleanup, and checks that the activity is still foregrounded. The service also rejects demo entry during capture and closes a demo if capture subsequently starts. Starting a real session from the companion closes the demo. No capture or voice session is automatically resumed.

`OverlayWindow` uses a service-owned, non-focusable `Dialog` window with `TYPE_APPLICATION_OVERLAY`. This supplies the public `Window`/`DecorView` needed by Android 12's `setBackgroundBlurRadius`; blurring a Compose node would not blur another app. Compact controls stay wrap-content. The demo uses the available width minus 16 dp side margins and a height below half the usable display, initially bottom-aligned with a 16 dp margin. System-bar/cutout insets are excluded; the header remains draggable vertically while its body scrolls, and close stays outside the scroll area. The host never sets `FLAG_BLUR_BEHIND` or dims surrounding video. Blur support changes are observed at runtime and the listener is removed on dismissal. The higher-opacity preference disables blur and uses an opaque surface. Android 10/11 use the same fallback without calling newer APIs.

The theme and opacity preference update the active window without restarting capture. Gallery fixtures remain synthetic; their material treatments do not prove native cross-window behavior. Font license notices ship in APK assets.

Before merging overlay changes, verify on authorized devices: Android 10/11 fallback; supported Android 12+ blur behind the panel only; runtime blur disablement/battery saver; dragging versus demo-body scrolling; touch pass-through; rotation and 200% text; permission revocation; close/notification/task cleanup; and confirmed versus canceled demo entry during a real capture. Verify theme persistence and that closing a demo never restarts media access. The unit tests cover palette contrast, fallback decisions and demo-entry policy, not GPU composition or physical-device behavior.

## Backend foundation

`backend` is a Python 3.11/uv project. `services/api` owns the FastAPI application
and its lifespan; `services/worker` owns one worker lifecycle used both as a
standalone process and as an optional lifespan task (`OVRLY_EMBED_WORKER=1`).
Settings and SQLAlchemy asyncio/Psycopg database access are shared. PostgreSQL 16
runs locally through Compose; the API and worker run natively, avoiding an
additional container image. `scripts/backend.sh` starts this local stack with
migrations. Alembic establishes versioned migration history without product tables.

`/healthz` reports database readiness and, when enabled, embedded-worker health.
It does not contact providers or claim a separate worker is healthy. Failures
return a safe 503; failed embedded-worker startup prevents serving requests.
Shutdown stops owned worker tasks and disposes connections. There are no jobs,
leases or media processing yet; durable execution and lease draining belong to
BE-04 (#16). Backend CI now runs the frozen environment, lint/types,
PostgreSQL/migration tests and service coverage independently of Android CI.
Only known docs-only changes skip execution; the final Backend checks result
still reports. Main comparison uses a disposable worktree and explicitly
discloses the absence of a pre-bootstrap baseline. REPO-04 (#13) remains open
for Android coverage, future-module evidence and administrator activation of the
required check.

## Future integration boundary

The Android/backend integration will use a single versioned API contract with
validated schemas and compatibility tests. FastAPI exposes its bootstrap OpenAPI
and health route, but no versioned product contract or product endpoints exist.
First agree on captured intervals, timestamped segments, ordered claims, evidence
citations, job states, cancellation and explicit errors.

Hosted model weights stay with the provider. Credentials stay on the server; research prompts and adapters will live in the backend. Versioned research evaluation fixtures live in the root `evaluation/` directory, independently of backend implementation. Voxide remains a separate companion-navigation path. No capture upload is enabled by this directory layout.

## Evaluation-data boundary

[`evaluation/`](../evaluation/README.md) defines JSON Schema/JSONL contracts for
clip provenance, two independent whole-clip annotation passes and adjudicated
claim occurrences. Its standard-library validator checks snapshot integrity,
references, intervals and declared creator/topic/repost isolation across dev/test.
Synthetic examples exercise the contract but are not benchmark data or human
review evidence. RES-01 still requires the real 10-20-clip reviewed corpus.

Media stays outside version control by default. Metadata-only CI validation
never fetches media or calls providers; local media verification additionally
checks byte hashes. Legal rights, independent review, actual scenario coverage
and undeclared leakage need human sign-off. Neither validation mode scores a
pipeline, and normalized claims are not ASR transcripts or OCR-box ground truth.

## Development ownership

Android development can proceed without backend dependencies. Backend platform and research work can share the future Python project with separate internal ownership, but contract changes need both client and server review.

Builds, caches, local machine configuration and packaged APKs are ignored. Keep personal tooling and media in the ignored root `.local` directory, and disposable experiments in `.scratch`. Shared configuration and approved test fixtures stay in version control.
