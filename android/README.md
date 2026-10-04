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

`app.ovrly.contract` holds the typed models and the production parsers for the
shared schemas in [`packages/contracts`](../packages/contracts/README.md): the
voice-actions slice (`POST /v1/voice/actions`, BE-13 / #67) and, from the #15
handoff (PR #82), uploads, investigation creation, the investigation read model
with its job and report version, claims, evidence, assessments, capture sessions
and capture chunks (AN-13 / #62). Serialization uses `kotlinx.serialization`
through the `org.jetbrains.kotlin.plugin.serialization` plugin and
`kotlinx-serialization-json`. AN-03 (#18), AN-09 (#35) and BE-10 (#33) reuse
these entry points instead of adding a second parser or DTO set:

| Entry point | Purpose |
| --- | --- |
| `VoiceActionCodec.parseRequest` / `encodeRequest` | Strict request handling: unknown keys, a missing `target`, an action outside the allowlist, a target kind that does not match the action or an invalid identifier all throw `ContractParseException`. |
| `VoiceActionCodec.parseResponse` / `encodeResponse` | Response handling: required fields, types, identifier syntax and the accepted/denied shape are enforced; unknown keys are tolerated as additive fields. |
| `InvestigationCodec.parseInvestigation` / `encodeInvestigation` | The investigation read model (`POST /v1/investigations` 202, `GET /v1/investigations[/{id}]`) with its nested `Job` and `ReportVersion`. |
| `InvestigationCodec.parseReportVersion` / `encodeReportVersion` | A report version on its own, as `GET .../reports/{version}` will return it (#33). |
| `InvestigationCodec.parseCreateRequest` / `encodeCreateRequest` | `POST /v1/investigations` body with its `InvestigationSource.Url` or `InvestigationSource.Upload`; a source kind outside the union is rejected. |
| `UploadCodec.parseUpload` / `encodeUpload`, `parseDeclareRequest` / `encodeDeclareRequest`, `parseCompleteRequest` / `encodeCompleteRequest` | `POST /v1/uploads` and `POST /v1/uploads/{id}/complete`; both responses are the `Upload` read model and the completion body is pinned empty. |
| `CaptureCodec.parseSession` / `encodeSession`, `parseChunkRequest` / `encodeChunkRequest`, `parseChunk` / `encodeChunk` | Live capture sessions and chunk declarations/acknowledgements, published ahead of their endpoints. |
| `VoiceActionRequest`, `VoiceActionResponse`, `VoiceTarget`, `ContractError`, `Investigation`, `Coverage`, `InvestigationError`, `InvestigationSource`, `InvestigationCreateRequest`, `Job`, `ReportVersion`, `Claim`, `ClaimCorrection`, `Evidence`, `EvidenceSource`, `Assessment`, `EvidenceRelation`, `Interval`, `SeqRange`, `Upload`, `UploadDeclareRequest`, `UploadCompleteRequest`, `CaptureSession`, `CaptureChunkRequest`, `CaptureChunk` | Models whose constructors enforce the schema, so an instance built in code is also one the contract allows: identifier, hash, timestamp and URL syntax, string lengths, `0 <= start_ms < end_ms`, the capture timebase, the investigation `oneOf` branches (an error is only present when failed, a report only when partial, complete or cancelled, never both), the report echoing `id` and `version`, evidence and assessments referring to claims of the same version, relations referring to evidence of the same version, at most one assessment per claim, an assessment without relations being `insufficient_evidence`, and a complete report being final and assessing every claim. |
| `ContractJson.CONTRACT_VERSION` (also `VoiceActionCodec.CONTRACT_VERSION`) | Must equal `packages/contracts/VERSION`; a version bump fails the Android tests until the models are reviewed. |

Every `$def` in `enums.schema.json` is one Kotlin enum with the same wire values
in the same order and a trailing `UNKNOWN` fallback: `JobState`, `RetryClass`,
`InvestigationState`, `Stage`, `ProcessingStatus`, `CoverageStatus`, `UploadState`,
`SourceKind`, `Timebase`, `Modality`, `Relation`, `OverallAssessment`,
`SourceInspectionLevel`, `SourceType`, `RetrievalRelevance`, `RetractionStatus`,
`CorrectionAttribution`, `CaptureSessionState` and `ChunkDisposition`, plus the
voice enums `VoiceAction`, `VoiceTargetKind`, `VoiceActionResult`,
`VoiceActionErrorCode` and `ContractErrorAction`. A value this version does not
define parses to `UNKNOWN`, the rest of the payload is kept, and `UNKNOWN` is
never success: `Investigation.isComplete`, `Job.isPublished`, `Upload.isCompleted`,
`CaptureChunk.isStored`, `CaptureSession.isOpen` and
`VoiceActionResponse.isAccepted` are false for it and it cannot be encoded. The
contract's own `unknown` value of `source_inspection_level` and
`retraction_status` is `UNDETERMINED`, distinct from the fallback. Requests
(`ContractJson.strict`) reject unknown keys because the server validates them
with `additionalProperties: false`; read models (`ContractJson.tolerant`) ignore
unknown keys as additive fields. Missing required fields, `null` for a non-null
field, quoted numbers or booleans, fractions where an integer is required and
every rule above are explicit `ContractParseException`s; nothing is replaced by a
success-shaped default. The #18 Room state names are its own and must not be
confused with `JobState` or `ProcessingStatus`.

Unit tests read the committed fixtures and schemas in place: `app/build.gradle.kts`
adds `../packages/contracts` as a test resources directory (only `VERSION`,
`schemas/**` and `fixtures/**` are copied to the test classpath) and
`ContractFixtures` loads `fixtures/voice-actions/*.json`, `fixtures/results/*.json`
and `fixtures/intake/*.json`. Nothing is duplicated into the module.

| Test | What it proves |
| --- | --- |
| `VoiceActionFixturesTest` | Typed values for every accepted voice fixture, the error code of every denied fixture, the stated failure of every invalid request fixture, the `UNKNOWN` mapping of `unknown-enum-response`, and an encode/parse round trip. |
| `VoiceActionCompatibilityTest` | The voice enums against the schema enums and `oneOf` pairs, malformed payloads and the Android-only fixtures in `app/src/test/resources/fixtures/voice-actions-incompatible/`. |
| `ContractResultFixturesTest` | Each of the six result fixtures (`complete`, `partial`, `failed`, `cancelled`, `insufficient-evidence`, `no-claims`) against its own `expect` block (status, state, claim and assessment counts, `error_code`) and the typed values the contract README promises: the user correction and superseded version in `complete`, the provisional capture-timebase report with one unassessed claim in `partial`, `MEDIA_UNSUPPORTED` with `non_retriable_input` and no report in `failed`, `cancel_requested` with no report and no error in `cancelled`, the retracted abstract-only and metadata-only sources with no supported assessment in `insufficient-evidence`, and empty findings with no verdict in `no-claims`; plus an encode/parse round trip of every payload. |
| `ContractIntakeFixturesTest` | Every intake request parses through the schema it names and encodes back to the fixture, every response parses to the expected typed values (pending and completed uploads, the intake read model, the open session with its gap, the `out_of_order` and `duplicate` dispositions), and `investigation-create-mixed-source` fails. |
| `ContractEnumsTest` | Every `$def` in `enums.schema.json` equals the matching Kotlin enum's wire values exactly (a new or renamed value fails the build), every enum maps `"__future_value__"` and case or whitespace variants to `UNKNOWN`, a payload full of future values parses with `UNKNOWN` everywhere and no success flag, every model's serial names and required members equal the `properties` and `required` of the schema object it mirrors (and every schema file is mirrored or listed as having no model), the capture constants and the investigation `oneOf` branches match the schemas, and `CONTRACT_VERSION` equals `VERSION`. |
| `ContractCompatibilityTest` | The Android-only fixtures in `app/src/test/resources/fixtures/contract-incompatible/` (a missing required field, a report of the wrong type, a failed investigation with a report, a complete one with an error, version and provisional mismatches, an unknown source kind and an extra field in a create request, a quoted integer, a string boolean, a reversed interval, a report without `claims`, dangling claim and evidence references, an unsupported assessment with no relations, an uppercase hash, a zero-byte declaration, a completion body with a field, a media-timebase chunk, an unstated duplicate rule and a non-UUID session id) are rejected for the stated reason while the additive-field fixture parses; malformed JSON fails every parser; `null`, quoted numbers and booleans are never coerced; and models built in code with the same violations cannot exist. |

Adding a fixture to `packages/contracts/fixtures/` fails the classification test of
its directory until it is covered here; adding a schema file fails
`ContractEnumsTest` until it is mirrored. The schema `pattern` for `error.code`
is enforced, so a future code must still be `SCREAMING_SNAKE_CASE`; a voice
response with a JSON `null` target is treated as an absent target. Schema or
fixture problems belong in `packages/contracts` through #15, not in Android-side
workarounds. API calls, authentication, Room and UI integration are #18.

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
