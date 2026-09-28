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

`AppShell` provides Your space, Explore and Settings. The activity owns the selected tab; external capture/share intents open Settings. Capture and voice keep their existing state stores and an active-session shortcut across tabs. Sample saves use restored UI state, not capture storage or a provider. Tab and report scroll positions have separate saveable scopes.

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

## Future boundary

`backend` is reserved, not implemented on main. The proposed shape is one Python codebase for HTTP endpoints, jobs, research and provider adapters, with API and worker processes able to run separately.

Before connecting Android, agree a versioned API contract with validated schemas and compatibility tests: captured intervals, timestamped segments, ordered claims, evidence citations, job states, cancellation and explicit errors. No OpenAPI document or endpoint is currently implemented.

Hosted model weights stay with the provider. Credentials stay on the server; prompts and adapters belong in the backend. Evaluation fixtures live in root `evaluation/`, independently of backend implementation. Voxide remains a separate companion-navigation path. The directory layout enables no capture upload.

## Evaluation-data boundary

[`evaluation/`](../evaluation/README.md) defines JSON Schema/JSONL contracts for provenance, two independent whole-clip annotation passes and adjudicated claim occurrences. The standard-library validator checks snapshot integrity, references, intervals and declared creator/topic/repost isolation across dev/test.

Synthetic examples test the contract; RES-01 still requires the reviewed 10-20-clip corpus. Metadata-only CI never fetches media or calls providers. Local media verification additionally checks byte hashes. Rights, independent review, scenario coverage and undeclared leakage require human sign-off. Neither mode scores a pipeline; normalized claims are not ASR transcripts or OCR-box ground truth.

## Development ownership

Android builds independently of backend dependencies. Future backend platform and research work can share one Python project, but client/server contract changes need both sides' review.

Generated builds, caches, APKs and machine configuration stay out of Git. Use ignored root `.local` for personal tooling/media and `.scratch` for disposable experiments. Version shared configuration and approved test fixtures; keep evaluation media outside version control by default.
