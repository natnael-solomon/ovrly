# Android

Kotlin/Jetpack Compose app for Android 10+ (API 29), with floating controls, local capture and share intake. Shared videos and links are sent to the ovrly backend configured at build time (see [Data layer](#data-layer-and-share-intake)); research processing beyond intake is not connected yet.

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

## Capture endpoint contract

BE-06 adds `CaptureApiCodec.parseCreateRequest`, `parseCloseRequest`, `parseMetadata`
and `parseStatus`, with matching encoders and strict typed models. The shared
intake fixtures include the multipart metadata envelope and waiting status with
a missing tail. `InvestigationSource.Capture` is supported on reads but rejected
by the ordinary shared-media create request. AN-04 (#26) packages and uploads
chunks (see below); showing polled progress is still AN-07.
Empty capture claims with `not_started` are not a no-claims finding.
Extraction progress uses `CoverageStatus`: `partial` and `complete` parse today,
and future values map to `UNKNOWN` without dropping the rest of the status.

## Data layer and share intake

AN-03 (#18) connects share intake to the backend on the existing OkHttp client
and the `app.ovrly.contract` codecs, with no Retrofit and no second serializer,
and keeps investigations, their local job state and report versions in Room so
the UI is rebuilt from the store and the server, never from ViewModel memory.

### Configuration

The API base URL and the device-side upload cap are build-time `BuildConfig`
fields (`OVRLY_API_BASE_URL`, `OVRLY_UPLOAD_MAX_BYTES`). Copy
`api.local.properties.example` to the ignored `api.local.properties` to change
them. The default is `http://10.0.2.2:8000/`, the emulator's view of a backend
on the host; for a USB device run `adb reverse tcp:8000 tcp:8000` and use
`http://127.0.0.1:8000/`. Debug builds allow cleartext only to those loopback
hosts (`src/debug/res/xml/network_security_config.xml`); release builds stay
HTTPS-only. Never commit a hosted address. The upload cap must not exceed the
backend `OVRLY_UPLOAD_MAX_BYTES` (256 MiB default, pending BC-D06); the server
still enforces its own limit.

### Client behaviour

| Concern | Behaviour |
| --- | --- |
| Identity | `POST /v1/principals/guest` on first need. The opaque token is AES-GCM encrypted with an Android Keystore key and stored in `noBackupFilesDir`, so it is excluded from backup and device transfer. If the server refuses it (`INVALID_CREDENTIAL`, `AUTHENTICATION_REQUIRED`), the client mints a new guest once and repeats the call. Linking an account is #36. |
| Headers | `Authorization: Bearer`, a new UUID `X-Request-Id` per request, `Idempotency-Key` on `POST /v1/investigations`. Redirects are refused and a target outside the base origin is never called. |
| Timeouts | 15 s connect, 30 s read, 60 s write. |
| Cold start | The free host sleeps after 30 minutes idle. The first call of a process, or any call 25 minutes after the last response, uses 60 s connect/read timeouts and is retried once if no response arrives or the gateway answers 502 to 504. After 1.5 s the intake sheet shows "Waking the ovrly service..." instead of an error. |
| Errors | Every non-2xx body is parsed as the shared error shape (`ErrorCodec.parseError`) into `ApiFailure.Server` with `code`, `message`, `retryable`, `action` and `request_id`. Codes the backend emits today map to `ApiErrorCode`; any other code is `ApiErrorCode.UNKNOWN` and still a failure. A body outside the error shape, or a success body the contract codecs reject, is `ApiFailure.Incompatible`, never success. No response is `ApiFailure.Network`. |
| Logging | Tag `OvrlyApi`: route label, status, elapsed time, attempt and request id only. No URLs, bodies, headers or tokens. |

### Share intake

Shares open `ShareIntakeActivity`, a translucent bottom sheet over the source
app, instead of Settings. A file is checked before anything is copied: the
provider must call it `video/*`, the container must contain a video track and
its sniffed MIME type wins over the provider's (file names are ignored), the
duration must be 1 ms to 10 minutes and the declared size must be within the
cap. Accepted files are streamed in 64 KiB chunks into a per-intake directory under
`noBackupFilesDir/share-staging` while the URI grant is valid, hashed on the way and
cut off as soon as the cap is passed. Then `POST /v1/uploads`, `PUT` content,
`POST .../complete` and `POST /v1/investigations` with an `Idempotency-Key`. The
staged copy is deleted once the investigation exists, when the sheet is dismissed or
closed, and when a newer share replaces the intake; dismissing stops a copy in
progress. Two share sheets (shares from different apps) never touch each other's
copies; staging directories nothing waits for any more are swept by reconciliation.
A retry after a failure reuses the staged copy and the same key, so the server
replays the first answer. It first completes the upload it already declared, so a
lost `PUT` or `complete` response costs no second upload; the bytes are sent again
only on `UPLOAD_CONTENT_MISSING` or `UPLOAD_MISMATCH`, and a new upload is declared
only on `UPLOAD_EXPIRED` or `NOT_FOUND`. A link share creates a `url` source
investigation.

The sheet keeps the three states apart: a private copy on this device, uploading,
and accepted by the service (then the polled status). Sharing an item that
already has an investigation (same SHA-256, or same link) offers the existing
check, or "Check it again" with a new key. Private or protected, unsupported,
expired grant, over 10 minutes, over the size limit and unreadable inputs get
their own copy and a "Choose a video file" alternative; nothing is truncated.
Settings shows a one-line summary of the latest share.

### Local store and reconciliation

Room 2.8 (code generated by KSP) holds one database, `ovrly.db`, in app-private
storage that the data-extraction rules already exclude from backup and transfer.
Schema version 1 is exported to `app/schemas/` and committed; a later change bumps
the version and adds a migration checked against that export. There is no
destructive fallback.

| Table | Contents |
| --- | --- |
| `investigations` (`InvestigationRecord`) | One row per share: local id, server id, `LocalJobState`, source, idempotency key, duplicate key, staged copy and declared upload (while local), last `processing_status`, the last investigation read as contract JSON, error code and timestamps. |
| `report_versions` (`ReportCacheEntry`) | Immutable report versions as contract JSON, with `provisional`, `fetched_at` and a `stale` flag. |
| `pending_chunks` (`PendingChunk`) | Captured chunks waiting for their capture session, keyed by the contract's `(session_id, seq)`; the chunk uploader is AN-07 (#26). |

`LocalJobState` is the app's own state, not `JobState` or `ProcessingStatus`:
`local_pending -> uploading -> accepted -> queued / running / partial /
succeeded / failed / cancelled`. Transitions go through `LocalJobState.next`
and `LocalJobs`; anything else (for example `UploadStarted` on an accepted
share) is refused and leaves the row unchanged. After acceptance every server
read wins, even over a terminal local state, and an `UNKNOWN` status keeps the
current state, so it never becomes success. A `NOT_FOUND` read marks the share
failed and stops offering it as a duplicate.

`Reconciler.reconcile()` runs from `MainActivity.onResume`, so on app start and
every return to the foreground. It reads every accepted, unfinished
investigation again (the server wins), retries shares left `local_pending` or
`uploading` by an earlier process with their staged copy, declared upload and
idempotency key (marking them failed when the copy is gone), leaves alone the
shares an open sheet is handling and that sheet's staging directory (registered
before any byte is staged), and deletes staging nothing waits for that is older
than the process start the platform reports. Each row is read again just before
it is acted on, so a share the user dismissed meanwhile is not retried.

The declared upload is stored as soon as `POST /v1/uploads` answers and the
upload id as soon as the upload completes, both before the next call, the same
rule as for the idempotency key. A retry after process death therefore never
declares or sends the file again, and a retried create sends the same upload id
with the same key, so the server replays its answer instead of refusing it.

A cached report is stale when a newer investigation version is known, or when it
is provisional and could not be confirmed against the server within 10 minutes
(the server was unreachable). A final, complete version never goes stale by age.
`InvestigationRepository.cachedReport(id)` returns it with that flag.

### Entry points for #31 and #34

| Entry point | Use |
| --- | --- |
| `ApiServices.get(context)` | Process-wide `OvrlyApi`, `LocalJobs`, `InvestigationRepository` and `Reconciler`; null if the base URL is unusable. |
| `InvestigationRepository.refresh(id)` / `track(id)` | One read, or a flow of `InvestigationUpdate`s polled every 3 s (5 s while `partial`, backing off to 30 s on failures) until `complete`, `failed` or `cancelled`, or a non-retryable failure such as `NOT_FOUND`. Every read is stored. |
| `InvestigationRepository.cached(id)` / `cachedReport(id)` | The last stored read, and the newest cached report version with its `stale` flag, without a network call. |
| `CheckStatus` (`Investigation.checkStatus`) | `WAITING`, `CHECKING`, `PARTIAL`, `COMPLETE`, `FAILED`, `CANCELLED` from `processing_status`; `UNKNOWN` is neither complete nor terminal. |
| `OvrlyApi.waking` | True while a cold call waits for the service. |

Every investigation route returns the full contract read model
(`processing_status`, `job`, `report`) since BE-10 (#33, PR #95). A response
without those fields, such as an older backend's nine-field body, is reported
as `ApiFailure.Incompatible` ("Update needed") instead of a guessed status.

| Test | What it proves |
| --- | --- |
| `ApiClientTest` | Bearer and per-request `X-Request-Id`, logs without tokens, bodies or URLs, the cold-start retry (connection drop and gateway error) with the waking state, no retry when warm, every known error code and an unknown code and action, HTML/invalid/redirect responses as incompatible, origin pinning and base URL parsing. |
| `OvrlyApiTest` | Guest minting once and token reuse, replacing a refused credential, the six result fixtures read through `GET` and the repository with their typed status, `UNKNOWN` never complete, the legacy nine-field body rejected, idempotent create replay and polling until terminal or `NOT_FOUND`. |
| `ShareIntakeTest` | Device checks (MIME, track, duration, size), streamed staging with hash and cap, per-intake staging and the orphan sweep, dismissing during staging (copy stops, file deleted, no stale state), the full upload path with request bodies and the stored record, duplicate offer and expired duplicate, URL source, retry with the same key and no second upload, resuming after a lost `PUT` response with the declared upload stored, re-sending missing bytes to the same upload, re-declaring an expired upload, dismissing before acceptance forgets the share, rejection cases that never reach the server, server limit mapping and a build without a service. |
| `LocalStoreTest` | The `LocalJobState` transition table, including refused transitions and `UNKNOWN` never reaching success; server reads winning and caching the report; importing an unseen investigation; staleness by age for provisional versions only and by a newer version; `NOT_FOUND` and abandoning; the committed schema export. |
| `ReconcilerTest` | Unfinished accepted shares refreshed and finished ones left alone, provisional reports going stale when the server is unreachable, a pending link created with its stored key, an interrupted upload completing its declared upload first, a lost staged copy failing without a request, a retryable failure staying pending, shares of an open sheet left alone and the staging sweep. |

Unit tests use OkHttp MockWebServer on loopback (already a test dependency) and
an in-memory `InvestigationDao` with the same semantics as the Room one; KSP
checks every Room query against the schema at build time. The Android Keystore,
Room on a device, `ContentResolver` and the sheet are not unit-tested.

## Segmented capture and chunk upload

AN-04 (#26). `CaptureService` records the selected interval as sequenced
chunks on a capture-relative timeline; no wall-clock or original-video time is
stored or sent.

| Rule | Behavior |
| --- | --- |
| Chunk grid | `CaptureLimits.CHUNK_MS` = 10000. Chunk `seq` covers `[seq * 10000, min((seq + 1) * 10000, 180000))` ms from the start of capture, the grid BE-06 enforces. Only the final chunk may be shorter; the 3-minute stop seals seq 17 at exactly 180000 ms. RES-02 may tune the length within the contract's 1000..30000. |
| Package | One ZIP per chunk (`application/zip`): `chunk.json` (seq, `start_ms`, `end_ms`, `timebase: capture`, modality, audio format, `frames_uploaded: false`, each frame read in the interval with its `frame_pts`, status, `recognition_ms` and `failed_regions`, `text_observations` as `{text, box, frame_pts}` with the box normalized to 0..10000 of the frame, the sampling policy and the recognizer name and version) and, when there was audio, `audio-16000-mono-s16le.pcm`. Frame images are never packaged. ZIP entry times are fixed to the ZIP epoch. Modality is `speech` when the chunk has audio, `text` when at least one frame in it was read (even if it held no text), `both` for both; a chunk with neither is skipped and recorded as such. |
| Screen text (AN-06, #100) | The screen is probed once a second at a 720 px long edge. A probe is kept when its 64x64 grayscale thumbnail differs from the last kept one by a mean absolute difference of at least 12, or 5 s after the last kept frame (heartbeat), and at most 20 frames are kept in any 60 s; refused frames are counted as capped. Probe rate, threshold and heartbeat are the RES-02 workstation values (#14). Each kept frame is cropped to likely text regions (rows with many sharp brightness steps, padded; the whole frame when they cover most of it; none when no row qualifies, status `no_text_regions`) and read on the device by ML Kit Text Recognition Latin 16.0.1 with the bundled model ([0005](../docs/decisions/0005-on-device-screen-text.md)). Regions are grown to at least 32 px on each edge and read one at a time. Each line becomes an observation; a region whose read errors or times out (5 s) is counted in `failed_regions`, and the frame is `failed` only when every region failed. After a timeout the frame's remaining regions are skipped (counted as failed), and the timed-out bitmap is left to the garbage collector because ML Kit does not cancel the task. A frame's text always goes into the chunk of its probe time: the probe is registered when its time is taken, and when it is still being copied or read at the chunk's 10 s boundary, that chunk (and any after it, so chunks stay in order) is held and sealed as soon as the text is in, while audio continues into the next chunk. A frame still being read 45 s after its probe time (every region timing out, plus a margin) is given up on and counted as unfinished, so a held chunk always seals. Frames still being read at Stop or 3:00 are counted as unfinished. A card shorter than the 1 s probe interval can still be missed. Recognition runs on the frame thread after the frame is copied out of the reader, never under the lock that Stop takes, so Stop does not wait for it. The kept frame JPEG (quality 72) stays in `capture/frames/` and is deleted first when space is needed. |
| Local manifest | `no_backup/capture/capture.json` (version 2) lists every chunk with size, SHA-256, frame times and observation count, chunks skipped because they held neither audio nor a read frame, gaps, chunks deleted after upload, kept, late-dropped, capped, failed and unfinished (still being read at Stop) frames, the sampling policy, the recognizer version, duration and stop reason. A gap of kind `interrupted` is recorded when playback audio stops arriving for at least 1 s. Screen lock still ends the capture (see [compatibility](../docs/compatibility.md)), so the final chunk ends at the lock. There is no pause control. |
| Storage | The 32 MiB cap applies to what is on the device. When a write would exceed it, local frames are deleted first, then chunks the server already holds, oldest first. Unsent chunks are never deleted: if they alone fill the budget, capture stops with "N chunks are saved on device, not yet sent". A new capture refuses to replace one whose chunks are still waiting to be sent or whose continuation choice has not been closed on the server; it replaces it only after that upload stopped for good (closed, not continued or permanently failed) and says how many unsent chunks were deleted. A capture interrupted by process death keeps its sealed chunks. A single-file capture left by an app version from before chunking is deleted when the app opens, with a message saying so. |
| Upload | Each sealed chunk schedules the WorkManager chain `capture-upload-<local session id>` (`APPEND_OR_REPLACE`, network required, exponential backoff from 10 s). A capture that sealed no chunk schedules nothing and never opens a server session. `CaptureUploader` opens the server session with the local session id as `Idempotency-Key`, sends each unsent chunk with `PUT /v1/captures/{id}/chunks/{seq}` and marks it sent only after the server reports it stored, so retries and lost acknowledgements are idempotent by `(session_id, seq)`. Transport and retryable errors retry; any other contract error stops the upload and keeps the chunks. Upload bookkeeping is bound to the local session id, so a worker that outlives its capture cannot mark a newer capture sent, closed or failed. |
| Status | `CaptureState.upload` reports sending, sent, closed or "Saved on device, not yet sent: N of M chunks". The foreground notification shows elapsed time, upload progress and Stop. |
| Stop | Notification and companion Stop end recording and release media access as before; the continuation choice stays open (`CaptureState.needsContinuationChoice`). `CaptureControl.stop(context, continueResearch)` records the choice (the first choice wins) and stops recording if needed. The overlay may instead send the `STOP` intent with the Boolean extra `CaptureService.EXTRA_CONTINUE_RESEARCH` (`app.ovrly.extra.CONTINUE_RESEARCH`), which records the choice the same way; without the extra Stop leaves the choice open. With `true` the remaining chunks are sent and the session is closed with `continue_research: true` and the actual duration; with `false` nothing more is sent and the session is closed with `continue_research: false`. Without a choice the server session expires as abandoned after its upload window. |

`CaptureSessionApi` covers the four `/v1/captures` operations with the #94
contract models. Debug builds use `InMemoryCaptureSessionApi`, an in-process
server with the BE-06 chunk rules, and label upload messages "to the in-memory
test server"; nothing leaves the device. Release builds use
`ServerCaptureSessionApi`, which fails with `CAPTURE_API_NOT_CONFIGURED`, so
chunks stay "saved on device, not yet sent" until it delegates to the AN-03
(#18) API client. Settings has "Upload on Wi-Fi only" (`CapturePreferences`);
when on, the chain requires an unmetered network, the current chain is replaced
with the new constraint, and pending chunks read "Waiting for Wi-Fi".

Tests: `CaptureModelTest` (grid, 3-minute boundary, upload text, continuation
prompt), `CaptureFilesTest` (sequencing, contiguous offsets, package contents,
text frames and observations, local-only frame images, late frames, gaps, skipped
chunks, the 180000 ms stop,
rolling deletion, local frame eviction, replacement refusal, restore after
process death), `CaptureUploaderTest` (idempotent re-upload, offline retention,
retry, close with both choices, no server session without chunks, a pending
close blocking replacement, a stale worker unable to write into a new capture,
unconfigured server), `CaptureTextTest` (probe rate, change and heartbeat
decisions, the per-minute cap, a brief synthetic title card caught by the change
trigger and missed by fixed 5 s sampling, text-region crops, box normalization,
the bundled recognizer version) and `CaptureStopLogTest` (a normal stop is not
logged as an interruption). Recognition itself runs only on a device.

### Telemetry

ML Kit brings Google's `datatransport` libraries, which would upload SDK usage
metrics. `app/src/main/AndroidManifest.xml` removes their upload components
(`JobInfoSchedulerService`, `AlarmManagerSchedulerBroadcastReceiver`,
`TransportBackendDiscovery`) with `tools:node="remove"`, and
`verify<Variant>NoTelemetry` fails `assemble` and `check` if any
`com.google.android.datatransport` or `com.google.firebase` component is left in
the merged manifest of any variant. Nothing recognized or sampled goes to Google;
the text observations go to the ovrly backend in the chunks.

### APK size

The bundled recognizer adds native libraries for each ABI (about 10.6 MB for
arm64-v8a and 6.5 MB for armeabi-v7a, compressed) and a 1.2 MB model. Release
builds keep only `arm64-v8a` and `armeabi-v7a` (`ndk.abiFilters` on the release
build type); debug builds keep every ABI so x86_64 emulator CI can run them.
Measured on 6 October 2026: the unsigned release APK is 24.1 MB, of which the
native libraries are 17.0 MB (arm64-v8a 10.6 MB, armeabi-v7a 6.5 MB), the model
1.2 MB and the minified code 3.8 MB; the debug APK with every ABI is 83.9 MB. A
ChromeOS (x86_64) release is not built; `ChromeOsAbiSupport` is suppressed on that
line only, since ChromeOS is not a supported device (BC-D02).

## Live overlay results

AN-07 (#31) adds the compact overlay's live results panel in `app.ovrly.overlay`. The
panel reads `OverlayStore.liveSource`. Until `LiveResultsConnection.start` is called for a
capture session, it holds `NotConnectedLiveResultsSource`, which reports "not connected" and
never emits claims, so builds look as before apart from the Stop choice.

| Piece | Behavior |
| --- | --- |
| `LiveResultsSource` | Emits `LiveResults`: the session phase, the captured duration and missing time, claim-extraction progress and claims in spoken order (capture-relative start, then status order). |
| `reduceLiveResults` | Folds one capture status poll (`CaptureApiCodec.parseStatus`) and the latest report version of the same investigation into `LiveResults`. Per-claim `processing_status` maps to waiting, checking evidence, provisional or assessed; failed and cancelled stay incomplete. A provisional assessment the user saw that is then replaced becomes updated, with the before and after assessment and the report's change summary. `UNKNOWN` statuses, a missing status and `UNKNOWN` assessments render as "Unknown state" and never as assessed. A report of another investigation is ignored. |
| `LivePanelController` | Hide and show, the "Assessment updated" notices, claim detail and the Stop prompt. Hiding the panel never stops capture. |
| `StopChoiceHandler.onStopChoice(continueResearch: Boolean)` | Called once after the user picks "Continue research in queue" (`true`) or "Keep only available results" (`false`). `CaptureServiceStopChoice` sends `CaptureService.STOP` with the Boolean extra `CaptureService.EXTRA_CONTINUE_RESEARCH`, which `CaptureService` passes to `CaptureControl` to close the session with `continue_research`. |
| `PollingLiveResultsSource` | The live adapter. Each poll reads the capture status and the investigation through an injected `LiveResultsFetcher` (to be backed by `CaptureSessionApi.status` and `OvrlyApi.getInvestigation`; this module has no HTTP code of its own), every 2 s while healthy. Failures back off 4, 8, 16 then 30 s, keep the last results on screen with "reconnecting", and after 8 consecutive failures stop with "connection lost". Polling ends when the session is abandoned, closed with "Keep only available results", or closed with "Continue research in queue" once claim extraction is complete and every claim is assessed, failed or cancelled. After close it polls every 5 s for at most 120 polls (10 minutes), then stops with "stopped waiting for updates", so a claim in an unknown state cannot keep it polling. |
| `LiveResultsConnection.start(scope, fetcher, sessionId)` / `stop()` | The integration seam: installs the polling source as `OverlayStore.liveSource` for one session, or returns to "not connected". |
| `FixtureLiveResultsSource` | Development, previews and tests only. Labelled "Fixture / not live". Built from `packages/contracts` fixtures (the `capture-status-waiting` session and the `partial` report, checked by `LiveResultsFixtureTest`) plus an Android-side version 2 that finalizes one claim. |

The compact overlay keeps the recording pill. Stop on the pill opens the choice with a
third option, "Keep recording"; the choice replaces the results panel while it is open. The
panel is capped at 60% of the usable screen height; its header (with Stop and hide) stays
fixed and everything below it scrolls, so it stays usable at 200% text. Once a live source
is connected the panel shows "Live results", the session phase, "Captured segment analyzed" with the captured length (not
the full video) and any time not received, a non-blocking "Assessment updated" notice, and
the claim list; tapping a notice or a claim opens a minimal in-panel detail that explains
the change. After "Keep only available results" or an abandoned session, unfinished claims
read "Incomplete". The panel's close control hides it and leaves a "Show live results"
button; capture continues, and Stop stays available on the pill and in the capture
notification. The window drags only by the header so the area below it can scroll.

`DemoOverlayPanel` is unchanged and remains labelled "DEMO / SIMULATED"; the service
renders it from `OverlayContent.Demo`, which carries no results, and
`LivePanelControllerTest` checks that its signature accepts no overlay or contract types.

Debug builds include `LiveFixtureActivity` (in `src/debug`, absent from release) for device
screenshots of the fixture panel without recording or network requests. It needs the
overlay permission and is refused while capture is running:

```text
adb shell am start -n app.ovrly/.overlay.LiveFixtureActivity
```

The fixture advances every five seconds through waiting, checking evidence, provisional and
updated; its Stop choice closes the fixture session instead of calling capture. Previews
for both themes and 200% text are in `ui/LiveResultsPreviews.kt`.

Not yet wired: a `LiveResultsFetcher` over `CaptureSessionApi.status` and
`OvrlyApi.getInvestigation`, and the `LiveResultsConnection.start` call when a capture
session opens. The production `ServerCaptureSessionApi` still answers
`CAPTURE_API_NOT_CONFIGURED`, so live polling has nothing to read yet.

## Instrumented tests and coverage

REPO-05 part 1 (#76) adds `app/src/androidTest` with AndroidX Test (runner, core,
rules, ext-junit), Espresso and the Compose test rule (`ui-test-junit4`, with
`ui-test-manifest` as a debug dependency). The **Android instrumented checks**
workflow runs them on emulators; do not run an emulator on a shared low-memory
machine, but compile the tests locally with:

```bash
sh gradlew --no-daemon --console=plain :app:assembleDebugAndroidTest
```

| Test | What it proves |
| --- | --- |
| `ShareInputReaderTest` | `ShareInputReader` against a provider in another app with real `content://` grants: a granted video is accepted and streamed (size from `OpenableColumns.SIZE`, from the descriptor length, or unknown and capped while staging), an ungranted or revoked URI is private, a revoked grant stops the copy, a deleted file is expired, oversize and overlong videos, a provider type that is not video, a sniffed container winning over the provider, an audio-only file and a text file called `video/mp4`, a non-`content` URI and the share intent shapes. |
| `SharePolicyDeviceTest` | Link and video rules on ART's `java.net.URI`. |
| `ShareIntakeSheetTest` | The sheet's oversize and malformed rejections, the file alternative and Close. |
| `CaptureServiceTest` | `CaptureService` with a real MediaProjection: start and Stop, Stop with a continuation choice (the first choice wins), the 3-minute limit, playback-audio permission denied, consent not granted, and the projection stopped by the system mid-capture. After every stop the playback recorder, notification and service are gone, the platform lists no projection for the app (`dumpsys media_projection`, checked positive while recording) and every capture display, in any state, is removed, which only the app's `VirtualDisplay.release()` can do. One accommodation: on Android 10 (API 29) the platform keeps the display of a projection it stopped until the app's process dies, and `release()` can no longer remove it, so for that one path the display check runs only from API 30 and the test asserts the stop reported no cleanup issue; counts are taken against the value before start, so the kept display does not affect later tests. |
| `CaptureUploadsTest` | The Wi-Fi-only preference is saved and reschedules the upload chain on unmetered networks, and `CaptureUploadWorker` under WorkManager sends every chunk to the in-memory server and closes it with either choice. |
| `OverlayServiceTest` | `OverlayService` show, hide, repeated show and demo/fixture/reset modes leave exactly one window, then none; a refused or mid-display revoked overlay permission leaves none; `OverlayWindow` shows and closes one window. Windows are counted from `dumpsys window`. |

The fixture provider (`ShareFixtureProvider`) is declared in the androidTest
manifest, so it runs in the test package's own process and uid; it is plain Java
because that process does not load the app's Kotlin or AndroidX classes. The
shell grants and revokes its URIs for `app.ovrly`. Videos are generated on the
emulator with `MediaCodec`; no media is committed. Screen-capture consent comes
from `appops set app.ovrly PROJECT_MEDIA allow`, which makes the system consent
screen answer without a dialog, and the overlay permission from the
`SYSTEM_ALERT_WINDOW` app op. Revoking `RECORD_AUDIO` kills the app process, so
the mid-capture audio revocation path cannot run in-process; the
projection-stopped case covers revocation during capture instead.

Coverage is off by default. `-Povrly.coverage=true` turns on JaCoCo 0.8.14 for
unit and instrumented tests of the debug variant and registers
`:app:jacocoDebugReport`, which reads whatever execution data exists (unit
tests, connected runs and retried attempts) without running tests. Excluded,
and listed in `app/build.gradle.kts`: generated code (`R`, `BuildConfig`,
`Manifest`, Room `_Impl`, serializers, `ComposableSingletons`), the Compose
preview files (`GalleryPreviews`, `AppearancePreviews`, `LiveResultsPreviews`)
and the gallery fixtures. Previews inside production files (`GlassOverlay.kt`,
`DemoOverlayPanel.kt`) stay counted. The API 34 job enforces at least 90% of
lines in `capture/`, `share/` and `contract/`, and at most a one-point drop in
overall lines against the last `main` measurement; API 29 is reported only.

Tests run under Android Test Orchestrator, so each test has its own process and a
crash fails only that test. `.github/scripts/android_instrumented.py` also
requires every `@Test` declared in `src/androidTest` to appear in the first
attempt's results; missing or undeclared tests fail the job without a retry, so
an aborted run cannot pass on a retry of the one test it reported.

Flake policy: `.github/scripts/android_instrumented.py` retries each failed test
once, in its own run, in the same job and names flakes in the job summary and as warnings. A
test that flakes twice in 48 hours, or twice on one PR, is quarantined with
`@Ignore` and an issue on the same day. Never re-run a job to get a green
result.

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

Live voice sends microphone audio for tab switching and the voice actions
below, and never reconnects automatically. Native authorization, device
compatibility and billing semantics remain unverified; one connection was
charged as one session in device testing.

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

### Voice actions

AN-09 (#35) wires the BC-D04 allowlist to `POST /v1/voice/actions` (BE-10, #33)
through `data/VoiceApi.kt` on the shared `ApiClient` and guest credential. The
manifest advertises `open_tab` and these five actions, each taking exactly
`{"id":"..."}`; extra arguments (including `confirmed` or `owner`) are refused
on the device and never sent. The tool-call envelope's `id` identifies the call
and is separate from `args.id`, which identifies the target.

| Action | Target | On-screen confirmation | After the server accepts |
| --- | --- | --- | --- |
| `open_check` | Investigation | No | The voice panel opens the check (status and claim count; a development fixture report is labelled as a fixture) until the report screens (#34) exist. |
| `save_report` | Report | No | Nothing else; the server saved it. |
| `queue_cancel` | Job | Required | Stored checks are reconciled. |
| `queue_retry` | Job | No | Stored checks are reconciled. |
| `queue_continue` | Job | No | Stored checks are reconciled. |

The assistant cannot see the app, so every tool result carries the state key
`checks`: the five most recent checks from the Room store, each with
`investigation_id`, `report_id`, `job_id`, `status` and `source` (`link` or
`video`); no URL, title or transcript. An id may also be the word `latest`,
which the device resolves to the most recent check's investigation, report or
job before anything is sent; with no check, report or job it answers without a
request. Any other id is opaque and case-sensitive (1-128 ASCII letters, digits,
`_` or `-`, starting with a letter or digit) and is sent as given.

Each command is one request with a new `request_id`. A retried tool call (same
call id and arguments) replays its first result instead of running again, and
client retries inside one request (cold start, a refreshed credential) resend
the same `request_id`, so the server replays rather than acting twice. A
response whose `request_id` does not echo the request is never used.

| Server answer | What the user sees and the assistant is told |
| --- | --- |
| 200 `accepted` | Success with the server's message. |
| 200 `denied` with `VOICE_TARGET_NOT_FOUND` (missing or another owner's target; the server never sends `VOICE_TARGET_NOT_OWNED`), `VOICE_ACTION_INVALID_STATE` (retry or continue on a finished job) or `VOICE_ACTION_UNSUPPORTED` | Error with the server's message and code. |
| 200 with a `result` this version does not know | Error; nothing is assumed to have happened. |
| 422 `VALIDATION_FAILED`, 409 `IDEMPOTENCY_KEY_REUSED`, any other error shape | Error with its code. |
| No response, or a body outside the contract | "Cannot reach ovrly" or "update needed". |

Cancellation waits for an on-screen dialog bound to that exact request and
target; it is declined after 30 seconds, and every pending approval is
discarded when voice stops, fails or the app leaves the foreground, including
cancellations queued behind the dialog or still resolving their target. A remote
`confirmed` flag is never read. Retry and continue never change a job on this
server: they are accepted while the job is in progress and denied once it has
finished.

The voice panel above the tabs shows what the user said (`text_user`, joined
per turn), the last result and the opened check. It appears while voice is
live, after any result, and when the microphone is denied or voice ended with
an error. Recognized text stays in memory for the screen: it is never logged,
stored or uploaded, and a new session starts with an empty panel. The panel's
typed alternative takes `open`, `save`, `cancel`, `retry` or `continue`,
optionally followed by an id (default `latest`), and runs the same command path
without a voice session, so it works with a denied microphone, after a
disconnect and in the offline simulation; it never starts or reconnects voice.

`open_tab` is unchanged: it takes exactly `{"tab":"space"}` or
`{"tab":"explore"}`, Settings is not a voice target, and an already showing tab
is reported without changes. Everything else, including delete, publish and
settings, is refused on the device with `VOICE_ACTION_UNSUPPORTED`, shown on
screen and never sent. Builds without an ovrly service address refuse the five
actions with `VOICE_ACTION_UNAVAILABLE`. Changing a target under the same call
id stops the session as a protocol error; malformed JSON fails protocol
parsing. The offline simulation never issues tool calls.

All unit and CI tests use fakes: `MockVoiceTransport`, a fake transport, MockWebServer on
loopback or an OkHttp interceptor. `NoLiveSessionsTest` checks that test
builds have no live opt-in or key and that every test constructing
`VoxideTransport` points it at a fake. No test opens a provider session.

| Test | What it proves |
| --- | --- |
| `VoiceApiTest` | Every shared voice-actions fixture with a valid request, sent and parsed through the client with its outcome; 422, 409 and network failures; a mismatched `request_id`; the same `request_id` on a cold-start retry. |
| `VoiceBackendTest` | `latest` per target kind, explicit ids, no request without a target, the `checks` state without URLs, cancellation approved, declined, mis-answered, timed out and discarded, effects only after acceptance, the fixture allowlist and the unsupported-action denial. |
| `VoiceCommandSessionTest` | Allowlisted calls reach the executor and others are refused, late answers carry the state and are shown, denials are visible, a repeated call runs once, an answer after stop is dropped with the approval discarded and no reconnect, and user speech is shown while assistant text is not. |
| `VoiceCommandContractTest`, `VoiceProtocolTest` | The advertised manifest and `checks` state schema, refused-command results, typed-command parsing and the text events. |
