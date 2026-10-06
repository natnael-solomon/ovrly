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
| `pending_chunks` (`PendingChunk`) | Reserved for captured chunks keyed by the contract's `(session_id, seq)`; unused, because the capture uploader (#26) tracks chunks in the capture folder. |

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
| Package | One ZIP per chunk (`application/zip`): `chunk.json` (seq, `start_ms`, `end_ms`, `timebase: capture`, modality, audio format, `frames_uploaded: false`, the capture times of the frames sampled in the interval, an empty `text_observations` list, the sampling policy and `recognizer: null`) and `audio-16000-mono-s16le.pcm`. ZIP entry times are fixed to the ZIP epoch. Only audio is uploaded, so a chunk declares `speech`; a chunk without audio is skipped and recorded as such. |
| Frames | Fixed sampling: one JPEG (720 px long edge, quality 72) about every 5 s, as before. Frames stay in `capture/frames/` on the device and are never uploaded; no text is recognized on the device. On-device OCR (AN-06) is deferred by user decision. Local frames are deleted first when space is needed. |
| Local manifest | `no_backup/capture/capture.json` (version 2) lists every chunk with size, SHA-256 and frame times, chunks skipped because they held no audio, gaps, chunks deleted after upload, kept and late-dropped frames, the fixed sampling policy, duration and stop reason. A gap of kind `interrupted` is recorded when playback audio stops arriving for at least 1 s. Screen lock still ends the capture (see [compatibility](../docs/compatibility.md)), so the final chunk ends at the lock. There is no pause control. |
| Storage | The 32 MiB cap applies to what is on the device. When a write would exceed it, local frames are deleted first, then chunks the server already holds, oldest first. Unsent chunks are never deleted: if they alone fill the budget, capture stops with "N chunks are saved on device, not yet sent". A new capture refuses to replace one whose chunks are still waiting to be sent or whose continuation choice has not been closed on the server; it replaces it only after that upload stopped for good (closed, not continued or permanently failed) and says how many unsent chunks were deleted. A capture interrupted by process death keeps its sealed chunks. A single-file capture left by an app version from before chunking is deleted when the app opens, with a message saying so. |
| Upload | Each sealed chunk schedules the WorkManager chain `capture-upload-<local session id>` (`APPEND_OR_REPLACE`, network required, exponential backoff from 10 s). A capture that sealed no chunk schedules nothing and never opens a server session. `CaptureUploader` opens the server session with the local session id as `Idempotency-Key`, sends each unsent chunk with `PUT /v1/captures/{id}/chunks/{seq}` and marks it sent only after the server reports it stored, so retries and lost acknowledgements are idempotent by `(session_id, seq)`. Transport and retryable errors retry; any other contract error stops the upload and keeps the chunks. Upload bookkeeping is bound to the local session id, so a worker that outlives its capture cannot mark a newer capture sent, closed or failed. |
| Status | `CaptureState.upload` reports sending, sent, closed or "Saved on device, not yet sent: N of M chunks". The foreground notification shows elapsed time, upload progress and Stop. |
| Stop | Notification and companion Stop end recording and release media access as before; the continuation choice stays open (`CaptureState.needsContinuationChoice`). `CaptureControl.stop(context, continueResearch)` records the choice (the first choice wins) and stops recording if needed. The overlay may instead send the `STOP` intent with the Boolean extra `CaptureService.EXTRA_CONTINUE_RESEARCH` (`app.ovrly.extra.CONTINUE_RESEARCH`), which records the choice the same way; without the extra Stop leaves the choice open. With `true` the remaining chunks are sent and the session is closed with `continue_research: true` and the actual duration; with `false` nothing more is sent and the session is closed with `continue_research: false`. Without a choice the server session expires as abandoned after its upload window. |

`CaptureSessionApi` covers the four `/v1/captures` operations with the #94
contract models. Every build uses `ServerCaptureSessionApi` over
`data/CaptureApi.kt`, which sends them through the AN-03 `ApiClient` with the
guest credential, `X-Request-Id` and cold-start handling: create with the local
session id as `Idempotency-Key`, `PUT .../chunks/{seq}` as multipart with a
`metadata` JSON part and a `content` file part, close with `continue_research`
and the duration, and the status read. A network failure is retried with
backoff; an error in the shared shape keeps its code and `retryable` flag; an
unreadable response is a permanent `INCOMPATIBLE_RESPONSE`; with no usable base
URL every call fails with `CAPTURE_API_NOT_CONFIGURED`. In each permanent case
the chunks stay "saved on device, not yet sent". `captureApi=memory` in
`api.local.properties` switches a local build to `InMemoryCaptureSessionApi`,
an in-process server with the BE-06 chunk rules whose messages say "to the
in-memory test server"; nothing leaves the device then. Unit tests use it too.
Chunks stay tracked in the capture folder's own files, not in the
`pending_chunks` table: the files are written together with each chunk, and a
second record could disagree with them. Settings has "Upload on Wi-Fi only" (`CapturePreferences`);
when on, the chain requires an unmetered network, the current chain is replaced
with the new constraint, and pending chunks read "Waiting for Wi-Fi".

Tests: `CaptureModelTest` (grid, 3-minute boundary, upload text, continuation
prompt), `CaptureFilesTest` (sequencing, contiguous offsets, package contents,
local-only frames, late frames, gaps, skipped chunks, the 180000 ms stop,
rolling deletion, local frame eviction, replacement refusal, restore after
process death), `CaptureUploaderTest` (idempotent re-upload, offline retention,
retry, close with both choices, no server session without chunks, a pending
close blocking replacement, a stale worker unable to write into a new capture,
unconfigured server), `CaptureApiTest` (MockWebServer: idempotent create, multipart chunk and duplicate
retry, close body, status fixture, error mapping, an end-to-end upload and close)
`CaptureLiveResultsFetcherTest` (status and investigation reads, Room storage of the read,`npolling that ends on a closed session and reports a lost connection) and`n`CaptureStopLogTest` (a normal stop is not logged as an interruption).

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

Live wiring (#26): `capture/CaptureLive.kt` implements `LiveResultsFetcher` as
`CaptureLiveResultsFetcher`, reading `GET /v1/captures/{id}` through `CaptureSessionApi`
and the investigation through `InvestigationRepository.refresh`, which also stores the
capture's investigation and report versions in Room. `CaptureLive.connect` calls
`LiveResultsConnection.start` when the uploader learns the server session id (again after
a lost connection), and `CaptureLive.disconnect` calls `stop` when a new capture starts.
Polling keeps running after Stop until the session settles or the poll policy's bounds end
it, so results that arrive after close still reach the panel. With `captureApi=memory` the
panel stays "not connected": the in-memory server has no investigations to read. When the polled report version is a development stub (`fixture` true, `OVRLY_STUB_REPORTS`), `PollingLiveResultsSource` labels the panel "Fixture / not live", as the fixture source does, so synthetic claims are never shown as live research.

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
