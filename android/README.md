# Android

Kotlin/Jetpack Compose app for Android 10+ (API 29), with floating controls, local capture and share validation. No backend or research processing is connected.

## Setup

Install JDK 21, Android SDK platform 37, Build Tools 36.0.0 and Platform Tools, and accept the SDK licenses. The wrapper uses Gradle 9.8.0 and Android Gradle plugin 9.4.1 with built-in Kotlin. Lint treats warnings as errors.

Open this directory in Android Studio and select JDK 21 for Gradle. For terminal builds, set `JAVA_HOME` and `ANDROID_HOME`. Keep `local.properties` uncommitted and pointed at the same SDK.

From the repository root, build the debug APK and run unit tests, lint and quality gates:

### Windows

```powershell
.\scripts\android.ps1 -JdkPath $env:JAVA_HOME -SdkPath $env:ANDROID_HOME
```

### Linux / WSL

```bash
cd android
sh gradlew --no-daemon --console=plain :app:assembleDebug :app:testDebugUnitTest :app:lintDebug qualityCheck
```

Use a Linux JDK, SDK, checkout and Gradle cache in WSL.

APK: `app/build/outputs/apk/debug/app-debug.apk`, relative to this directory. The build needs no backend or provider credentials.

## Quality and dependencies

`qualityCheck` runs detekt 1.23.8 with Compose rules 0.4.23 and ktlint 1.8.0
through Gradle plugin 14.2.0. It checks application/test Kotlin and Gradle scripts.
Detekt runs source analysis at the root, independent of AGP's variant API; it is
not type-resolved analysis. Existing findings are recorded in `config/*baseline.xml`,
not silently fixed or excluded. New findings fail; baseline changes need review.
`app/lint.xml` documents the existing narrow lint annotations and disables no rules.
`settings.gradle.kts` pins patched transitive build-tool dependencies; those
overrides do not apply to the app's runtime dependencies.

Gradle verifies dependency bytes against `gradle/verification-metadata.xml`.
Never bypass a checksum failure. For an intentional dependency update, resolve
the affected build/check tasks with
`--refresh-dependencies --write-verification-metadata sha256`. Review the new
coordinates and hashes against their publishers, then rerun without those flags.
Include parent POMs/BOMs and Linux CI artifacts; a warm-cache pass alone does not
prove complete metadata coverage. CI never regenerates baselines or verification metadata.

Repository pre-commit checks also run these gates and audit dependencies; they
require JDK 21, the SDK, uv and network access. See [shared checks](../WORKFLOW.md#shared-quality-gates).

## Contract models and fixtures

`app.ovrly.contract` holds the typed models and the production parser for the
shared schemas in [`packages/contracts`](../packages/contracts/README.md),
currently the voice-actions slice (`POST /v1/voice/actions`, BE-13 / #67).
Serialization uses `kotlinx.serialization` through the
`org.jetbrains.kotlin.plugin.serialization` plugin and `kotlinx-serialization-json`.
AN-03 (#18) and AN-09 (#35) reuse these entry points instead of adding a second
parser or DTO set:

| Entry point | Purpose |
| --- | --- |
| `VoiceActionCodec.parseRequest` / `encodeRequest` | Strict request handling: unknown keys, a missing `target`, an action outside the allowlist, a target kind that does not match the action or an invalid identifier all throw `ContractParseException`. |
| `VoiceActionCodec.parseResponse` / `encodeResponse` | Response handling: required fields, types, identifier syntax and the accepted/denied shape are enforced; unknown keys are tolerated as additive fields. |
| `VoiceActionRequest`, `VoiceActionResponse`, `VoiceTarget`, `ContractError` | Models whose constructors enforce the schema, so an instance built in code is also one the contract allows. `ContractError` is the contract-wide BE-05 shape (`code`, `message`, `retryable`, `action`, `request_id`); in a voice response its `request_id` must echo the response `request_id`. |
| `VoiceAction`, `VoiceTargetKind`, `VoiceActionResult`, `VoiceActionErrorCode`, `ContractErrorAction` | Enums with an `UNKNOWN` fallback for values this version does not define. `UNKNOWN` is never success: `VoiceActionResponse.isAccepted` is true only for `accepted`, and `UNKNOWN` cannot be encoded. |
| `VoiceActionCodec.CONTRACT_VERSION` | Must equal `packages/contracts/VERSION`; a version bump fails the Android tests until the models are reviewed. |

Unit tests read the committed fixtures and schemas in place: `app/build.gradle.kts`
adds `../packages/contracts` as a test resources directory (only `VERSION`,
`schemas/**` and `fixtures/**` are copied to the test classpath) and
`ContractFixtures` loads `fixtures/voice-actions/*.json`. Nothing is duplicated
into the module. `VoiceActionFixturesTest` asserts typed values for every
accepted fixture, the error code of every denied fixture, the stated failure of
every invalid request fixture, the `UNKNOWN` mapping of `unknown-enum-response`,
and an encode/parse round trip. `VoiceActionCompatibilityTest` checks the Kotlin
enums against the schema enums and `oneOf` pairs, rejects malformed payloads and
the Android-only incompatible fixtures in
`app/src/test/resources/fixtures/voice-actions-incompatible/` (an extra required
field, renamed enum values, wrong types, missing required fields including the
error shape, a non-boolean `retryable`, a non-echoed `error.request_id` and
accepted/denied shape violations), and proves renamed or future enum values never become success.

Adding a fixture to `packages/contracts/fixtures/voice-actions/` fails the
classification test until it is covered here. The schema `pattern` for
`error.code` is enforced, so a future code must still be `SCREAMING_SNAKE_CASE`;
a response with a JSON `null` target is treated as an absent target. Schema or
fixture problems belong in `packages/contracts` through #67 or #15, not in
Android-side workarounds. Investigations, jobs, report versions, claims and
evidence wait for the #15 handoff.

## Gallery and demo

Open `app/src/main/java/app/ovrly/ui/GalleryPreviews.kt` in Studio's Design or Split view. `GlassOverlay.kt` contains the live-control previews. Gallery selection does not change the live overlay.

Your space, Explore and the larger overlay demo use labeled sample reports. Sample saves survive navigation and restored activity state, not a fresh session. Settings contains the actual capture, permissions, storage and share controls, plus voice status and Stop.

Enable the larger demo from Settings or the gallery with display-over-other-apps permission. If capture or voice is active, confirm stopping it first. The demo never records or contacts a provider; closing it never restarts a session.

## Appearance

Liquid Chrome is the default. Saved Light/Dark choices are preserved independently of Android's theme. Lexend is used for interface text; Instrument Serif is reserved for editorial styles. Both retain their [SIL Open Font licenses](app/src/main/assets/licenses/).

Native overlay blur requires a supported Android 12+ device. Unsupported or disabled blur and higher-opacity mode use solid surfaces.

On Android 12, the splash icon appears only for home/system-originated launches. It was visible from Samsung One UI Home but absent from Niagara Launcher and `adb shell am start` on the tested Samsung SM-A217F. Android 13+ supports `icon_preferred`; an Android 15 emulator showed the icon from every launch source. There is no supported per-app override on Android 12.

See [UI maintenance](../docs/android-ui.md) for artwork, splash behavior and asset regeneration, and [architecture](../docs/architecture.md#overlay-material-and-demo-boundaries) for overlay lifecycle and device checks.

## Optional voice experiment

Live Voxide is disabled by default. Every build without the live opt-in, debug
and release (including Telegram APKs), runs an in-process simulation. Tapping
the orb connects instantly, listens without a key, microphone permission, audio
recording or network traffic, and ends after 15 seconds of silence. It never
triggers an app action, and the orb labels every state "Demo". This is a mock
transport, not speech recognition or evidence of provider compatibility. Unit
tests use fakes and synthetic HTTP responses; no provider sessions are used.

For an authorized native-device experiment, configure the ignored
`voxide.local.properties` from its example with `enabled=true` and only a
publishable key, then build with `VOXIDE_LIVE=1`. This is a build-time
environment variable, not a runtime switch; rebuild without it to return to
offline mode. CI rejects live opt-in. Only live builds embed the publishable
key; never use a secret key or distribute a live-configured APK casually.
There is no local attempt cap: whoever runs live tests tracks provider usage
against the dashboard.

Live voice sends microphone audio for tab switching only and never
reconnects automatically. Native authorization, device compatibility and
billing semantics remain unverified; one connection was charged as one session
in device testing. Product voice actions remain separate work (#33, #35).

### Session policy

| Rule | Behavior |
| --- | --- |
| Setup | Ends with an error if Voxide is not ready within 30 seconds. |
| Input window | Five minutes from server ready. |
| Finishing | Microphone released, new actions rejected; the current reply may finish for up to 30 seconds. Shows "Finishing reply · microphone off". |
| Silence | 15 seconds of continuous listening with no speech ends the session. Speech, assistant replies, pending actions and push-to-talk pause the timer. |
| Interruptions | Backgrounding, capture start, Stop and failures end the session. Restart is explicit. |

Transmission is half-duplex: the microphone is not sent while the assistant
speaks, plus a 250 ms echo guard. Speech detection uses a fixed loudness
threshold that has not been tuned on devices. "Thinking" is a visual hint shown
after speech followed by 600 ms of quiet; it does not affect control flow.
Push-to-talk and the user barge-in control (`interrupt`, which sends Voxide's
interrupt message and flushes playback) have not been tested live.

`VoiceController` exposes `state` (with an interaction phase), a 0..1 `level`
updated at most 20 times per second, and lossy orb `events` for presentation.

Voice starts from the orb in the Your space and Explore headers. Tap the small
orb (46 dp, 48 dp touch target) to open it, then tap to start or stop, or hold
for push-to-talk. While the assistant speaks, a tap interrupts it. Scrolling
the list collapses the open orb with the swipe; a fling docks it first. Settings
keeps the status and a Stop control. Starting voice is blocked during capture,
closes the overlay demo and asks for microphone permission. If permission is
permanently denied, the orb opens app settings. An unconfigured build shows a
muted orb that does nothing on tap. Errors show a short reason and return to
idle. Haptic confirmation uses `CONFIRM` on Android 11+ and a plain click on
Android 10.

Checked on a Samsung SM-A217F (Android 12) with the offline simulation and with
unconfigured and unreachable debug builds: open, collapse, scroll, permission
prompt, denial, the settings route and a connection failure. Live Voxide use of
the orb, push-to-talk and barge-in have not been tested.

### Buffers and diagnostics

Incoming events are parsed and decoded on the transport thread into a bounded,
ordered inbox (16 MB conservative accounting and 8,192 events), drained four
events at a time so UI work and Stop are not starved. Assistant PCM goes to a
fixed 10 MB ring buffer (about 3.5 minutes at 24 kHz). Both limits held during
a 5¾-minute device session. Overflow, invalid input and five seconds of pending
audio without speaker progress stop the session explicitly.

Diagnostics use the `OvrlyVoice` log tag and contain only setup stages, status
codes, event categories, counts and byte measurements: no keys, URLs,
transcripts, audio or target IDs. Socket and TLS cleanup run off the UI thread.

### Planned voice command contract

The offline contract defines these exact, case-sensitive names. Every command
takes only `{"id":"target-id"}`; extra arguments (including `confirmed` or
`owner`) are rejected. The tool-call envelope's `id` identifies the call and is
separate from `args.id`, which identifies the target.

| Command | Target | On-screen confirmation |
| --- | --- | --- |
| `open_check` | Investigation/check | No |
| `save_report` | Report | No |
| `queue_cancel` | Job | Required |
| `queue_retry` | Job | No |
| `queue_continue` | Job | No |

The current client ID syntax is 1-128 ASCII characters, starting with a letter
or digit and otherwise containing letters, digits, `_` or `-`. IDs are opaque
and case-sensitive: no trimming, coercion, URL interpretation or guessing a
missing ID from the selected screen. Reconcile this syntax with the shared
schemas in #15 before wiring #33/#35; it is not an implemented backend schema.
Valid syntax does not prove the target exists or belongs to the caller.

Only cancellation requires confirmation. Future integration must bind approval
to the exact call and target, reject a remote `confirmed` flag, and discard
pending approval on stop/backgrounding. Retry and continue remain subject to
backend job-state and resource limits even without a confirmation dialog.
None of these confirmations or product handlers is wired yet.

The active manifest advertises only `open_tab`, which takes exactly
`{"tab":"space"}` or `{"tab":"explore"}`. Settings is not a voice target. If
that tab is already showing, the result says so and nothing changes; otherwise
the app switches tabs (closing the gallery or an open report). Product requests
return `status: error` with
`VOICE_ACTION_UNAVAILABLE` when valid or `VOICE_ACTION_INVALID_ARGUMENTS` when
invalid. All other actions, including delete/publish/settings, return
`VOICE_ACTION_UNSUPPORTED`. No unfinished action returns success. Exact duplicate
call IDs replay their prior result; changing a target under the same call ID
stops the session as a protocol error. Malformed JSON fails protocol parsing.
Tests exercise these paths with fakes and consume no provider sessions.
