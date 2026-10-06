# Changelog

## [Unreleased]

### Added

- REPO-06 security tests (#28): outbound evidence fetches now go through an SSRF
  guard that resolves each host once, dials only the checked public address (private,
  loopback, link-local and metadata, shared, IPv6 unique-local and IPv4-embedding
  addresses are refused, which defeats DNS rebinding), re-checks every redirect hop
  (at most three, no HTTPS to HTTP downgrade), allows only http(s) on default ports and
  caps response size. New negative tests cover that guard, an authorization matrix
  generated from the OpenAPI paths, prompt-injection fixtures under
  `evaluation/adversarial/`, a media intake fuzz smoke, quota abuse and error-response
  hygiene. Validation errors no longer echo unknown client field names, and
  credential-shaped text is redacted from assessment notes. `MEDIA_INVALID` from a codec
  stage remains blocked by #20.

- Backend `.env.example` now lists every `OVRLY_` setting with its default (job timing
  and retries, stub reports, evidence stages), and the backend README has a
  single-service deployment note for EthioDeploy (#21).

- Opt-in backend quota infrastructure (#22): transactional daily check and upload
  reservations, active-check admission limits, a Scholarxiv near-exhaustion intake
  pause with typed retryable errors, shared request concurrency slots and a
  content-free operator quota summary. Proposed limits remain disabled by default;
  owner approval and Groq/Voxide/claim-stage integration remain pending.

- Live capture wiring (#31): capture chunks now go to the backend. `data/CaptureApi.kt`
  sends `/v1/captures` create (with `Idempotency-Key`), multipart chunk `PUT`, close with
  `continue_research` and status through the AN-03 API client and guest credential, and
  `ServerCaptureSessionApi` maps its failures to retry or keep-on-device. The overlay's live
  results panel polls the capture status and investigation once the server session exists,
  and capture investigations are stored in the Room store like shares. `captureApi=memory` in
  `api.local.properties` keeps the labelled in-memory test server for offline demonstrations.
  The live panel labels stub report versions (`fixture`) as "Fixture / not live". After a
  user Stop, the companion's capture status reads as one line, for example "Stopped · 16
  sent · research continues", instead of "Research is not connected".
  Choosing that research will not continue now stops a running upload before its next chunk.

- Android Inbox and Report screens (AN-08, #34): Your space now shows an Inbox of
  checks with stage and age (never a queue position), Cancel with confirmation,
  Try again and Continue checking, offline and update-needed notes, and a Library
  of finished reports; samples stay labeled ([RFC-D22](docs/decisions/RFC-D22-companion-navigation.md)).
  Reports separate work state from coverage, show the version with a picker and
  per-claim changes, the original wording, normalized meaning, interval with its
  timebase and modality of each claim, and evidence cards with relation and
  rationale, access level, retraction warnings and the passage read, contradicting
  evidence first. Users can correct a claim's meaning (a new version marked
  "Corrected by you"; earlier versions kept) and, for a captured clip, confirm a
  later shared video as its full video before an expansion names it. "Try again"
  reuses one stored key per check so a lost answer cannot create a duplicate
  check (Room schema version 2). Unknown contract values read neutrally.

- RES-02b device benchmarks (#103):
  - The Galaxy A21s measurements cover phone ASR replay (Vosk small-en, whisper.cpp
    tiny.en/base.en) against the existing Groq baselines, ML Kit against Tesseract
    on identical image bytes, a replay of the shipped screen-text sampler, and real
    AN-04 chunk verification.
  - Decision 0004 accepts RFC-D27: Groq turbo on 10 s chunks, falling back to
    large-v3, with explicit `ASR_UNAVAILABLE` semantics for #20 and no on-device
    ASR in the submission. It also records on-device ML Kit and the change trigger.
  - New tooling: native replay drivers in `evaluation/device-harness/`,
    `evaluation/device_benchmark.py`, the `evaluation/asr_policy.py` reference
    policy with local test doubles, and the debug-only `ScreenTextReplayTest`.
  - The 20-frames-per-minute cap dropping a brief card is tracked in #110.

- On-device screen text for live capture (AN-06, #100): frames are probed once a second and
  kept on a visible change or a 5-second heartbeat, at most 20 per minute; likely text regions
  are cropped and read on the phone by bundled ML Kit Text Recognition Latin 16.0.1. Chunks now
  carry `{text, box, frame_pts}` observations, per-frame status and recognition time, the
  sampling policy and the recognizer version, and declare `text` or `both` coverage. Frames
  still never leave the device. ML Kit's Google datatransport upload components are removed
  from the manifest, and a build check fails if any datatransport or Firebase component is
  left in a merged manifest (decision 0005). Stop no longer waits for recognition. A frame's
  text stays in the chunk of its probe time even when recognition runs past the chunk's end
  (the chunk is held until it is in); frames still being read at Stop are counted.

- Android voice actions wired to the API (AN-09, #35): the Voxide manifest
  advertises `open_check`, `save_report`, `queue_cancel`, `queue_retry` and
  `queue_continue` beside `open_tab`, each sent through `data/VoiceApi.kt` to
  `POST /v1/voice/actions` with one `request_id` per command (replayed on
  client retries and repeated tool calls). Tool results carry a `checks` state
  of the five most recent checks (ids, status and source; no URLs), and the
  device resolves the `latest` alias. Cancellation needs an on-screen
  confirmation bound to its target that times out after 30 seconds and is
  discarded when voice stops. A voice panel shows the recognized speech, each
  accepted, denied or failed result and the opened check, and offers a typed
  command alternative that works without a microphone or connection to Voxide.
  Recognized text is never logged or stored.

- Evidence stages (BE-09, #27): `retrieval` and `assessment` queue stages turn a
  published version with claims into the next version with evidence and assessments.
  Router-written neutral and disconfirming queries search Scholarxiv Papers; hits are
  deduplicated by DOI and arXiv id, ranked with in-house BM25 and read as abstracts or
  open-access full text from arXiv or Europe PMC, with the inspection level recorded;
  Crossref flags retracted, withdrawn or corrected sources and unknown stays unknown.
  The router labels each passage, retracted sources are never counted, the overall label
  is computed and abstains when evidence is missing, and every version is
  citation-checked before it is published. Budgets per claim keep unreached claims
  visible but unassessed. Papers and Router calls share a PostgreSQL token bucket under
  the account limit (migration `0010_provider_buckets`); calls wait for a token and a
  provider 429 holds the bucket for its `Retry-After`. Long stages heartbeat in the
  background, and assessment is enqueued with the fenced retrieval publish. The
  production `reanalysis` handler reruns corrected claims, searches deeper, or brings in
  the confirmed full video once it has a report, re-checking with backoff. Registered
  only with the server-side `OVRLY_SCHOLARXIV_API_KEY`; tests replay synthetic
  cassettes and never call a provider.
- Android instrumented tests in CI (REPO-05 part 1, #76): a new **Android
  instrumented checks** workflow runs AndroidX Test, Espresso and Compose tests
  on API 29 and API 34 emulators for every PR that touches Android, with the
  same documentation-only skip as Android checks, an API 34 AVD snapshot cached
  only by `main`, one automatic retry with flakes named in the job summary, and reports
  kept for seven days. The first tests cover share intake against a provider in
  another app with real `content://` grants (granted, ungranted, revoked and
  deleted sources, oversize, overlong and mislabelled files), the share sheet's
  rejections, `CaptureService` start, Stop, the continuation choice, the
  3-minute limit, denied permissions and a projection stopped mid-capture with
  every resource released, and overlay create and dismiss without leaked
  windows, including a refused or revoked overlay permission. JaCoCo unit and
  instrumented line coverage (off unless `-Povrly.coverage=true`) is summarized
  in the job with 90% floors for capture, share and contract parsing and at most
  a one-point drop against `main` (#13).

- Capture pipeline orchestration (#24): successful byte validation atomically
  queues `asr` and `device_text`, then `claim_extraction` after both publish,
  with owner-scoped, idempotent per-chunk jobs and Stop cancellation across the
  chain. Capture polling reads claim progress from the latest report, including
  fixture-labelled stub versions. Real stage handlers and real-claims validation
  remain #20/#25; queued work is not reported as successful analysis.

- Report versions carry `fixture` (true only for development stub versions) in every
  report read, including the `report` of an investigation, so clients can label stub
  claims as a fixture rather than live results. Additive to `0.2.0-draft`; the
  Android contract model mirrors it with a default of false.
- The development-only `OVRLY_STUB_REPORTS` stub covers live captures: fixture
  versions advance as chunks validate and become final on a close that continues
  research, and capture polling lists their claims. Off by default; without it
  capture behaviour is unchanged.

- Full-video expansion names its source: `POST /v1/investigations/{id}/reanalyze`
  with reason `expansion` takes `source_investigation_id`, the caller's own
  investigation of the confirmed full video, validated for ownership, kind and
  state, recorded by migration `0009_reanalysis_source` and echoed in the
  response. The field is optional in the `0.2.0-draft` contract so the addition
  is non-breaking; the server requires it whenever `match_confirmed` is true.

- Backend report versions, explicit saves, reanalysis, export and voice actions
  (BE-10, #33): immutable, owner-scoped report versions with change summaries
  (`GET /v1/investigations/{id}/reports[/{version}]`), idempotent saves with a
  snapshot (`POST /v1/reports/{id}/save`, `GET /v1/reports/saved`) that move to
  the account on a second-device link (BC-D07) and cannot be stranded by a
  concurrent link, `POST /v1/investigations/{id}/reanalyze` (a user correction
  publishes a new version that keeps the original wording and reruns only that
  claim; expansion requires a confirmed match; deeper search; each enqueues an
  owner-scoped job), an allowlisted export payload with provisional flag and
  retrieval date (`GET .../reports/{version}/export`), the full contract read
  model (`processing_status`, `job`, `report`) from every investigation route so
  current Android clients can parse it, and `POST /v1/voice/actions`
  enforcing the BC-D04 allowlist with ownership checks, typed denials, replay by
  `request_id` and an audit row per action without any transcript. Migration
  `0008_reports`. A development-only `OVRLY_STUB_REPORTS` setting (off by
  default) publishes a clearly labelled fixture report until the assessment
  pipeline (#27) exists. The operations join the `0.2.0-draft` OpenAPI document
  as additions. Real assessment content and the production reanalysis handler
  arrive with #27.

- Android data layer and share intake (AN-03, #18): shared videos and links open an intake sheet over the source app instead of Settings. Files are checked for type, video track, duration (10 minutes) and size before anything is copied, streamed into private no-backup staging, uploaded through `/v1/uploads` and turned into an investigation with an `Idempotency-Key`; links create a URL-source investigation. Resharing offers the existing check, rejections explain private, unsupported, expired, too long and too large inputs with a file alternative, and the sheet polls the investigation status. The OkHttp `ApiClient` adds the bearer credential (a guest identity minted on first need, Keystore-encrypted and excluded from backup), a request id per call, request-id-only logging, cold-start timeouts with one retry and a "waking service" notice, and maps every error response through the shared error shape, with unknown codes kept as failures. The API base URL comes from an ignored `api.local.properties` and defaults to the emulator host. Shares, their local job state (`local_pending` to `uploading` to accepted and the server's queued, running, partial, succeeded, failed or cancelled) and cached report versions with a staleness flag live in a Room database (schema exported, KSP-generated, excluded from backup) behind `InvestigationRepository`; a reconciler on app start and every foreground lets the server win for accepted shares, retries shares an earlier process left pending, and cleans up staging. A `PendingChunk` table is ready for the capture chunk uploader (#26).

- Segmented live capture on Android (AN-04, #26): `CaptureService` now writes 10-second
  chunks on a capture-relative timeline (seq, start/end ms, modality) with a local manifest of
  skipped chunks, audio interruptions and dropped frames, and still stops at exactly 3:00.
  Screen frames are still sampled about every 5 s and stay on the device; on-device OCR is
  deferred. Chunks upload while recording through a WorkManager chain per session with
  exponential backoff, idempotent by `(session_id, seq)`, with an optional Wi-Fi-only setting;
  offline chunks stay "saved on device, not yet sent". The 32 MiB cap deletes only local frames
  and chunks the server already holds. The notification shows upload progress, and
  `CaptureControl.stop(context, continueResearch)` closes the session with the user's choice.
  Debug builds upload to a labelled in-memory test server; release builds keep chunks on the
  device until the AN-03 API client is wired. A normal stop no longer logs
  `Playback capture was interrupted`, and a pre-chunk local capture left by an earlier version is
  deleted on open with a message.

- Live overlay results panel for AN-07 (#31): the compact overlay can show the
  capture session's claims in spoken order as waiting, checking evidence, provisional,
  updated or assessed, with a "Captured segment analyzed" label, a non-blocking
  "Assessment updated" notice that opens an explanation of the change, and a hide control
  that keeps capture running. Unknown states stay neutral and are never shown as assessed.
  Stop now asks "Continue research in queue" or "Keep only available results" and passes
  the answer to capture, which closes the session with that choice; unfinished claims stay
  marked incomplete. A polling adapter reads the capture status and investigation with
  backoff and keeps the last results visible while reconnecting. It is not started yet
  because the production capture session API is still unconfigured, so no results appear
  outside previews and a labelled debug-only fixture.
  The overlay demo is unchanged and never receives live data.

- Incremental backend capture sessions with owner-scoped multipart chunk intake,
  durable duplicate receipts, gap/modality manifests, a 180000ms timeline cap,
  explicit Stop continuation and polling. Each chunk enters the durable queue
  before close; byte validation is implemented, while ASR/OCR/claim analysis
  remains separate. Capture storage participates in opt-in retention. Shared
  schemas, Android codecs and synthetic recovery cases cover the new contract.
  Contract `0.2.0-draft` adds the capture investigation source; older clients
  must be updated to parse that response branch.
  Capture polling uses a non-locking consistent database snapshot and
  forward-compatible extraction progress; create/close schemas enforce duration
  bounds. Multipart cleanup uses the public form API, and chunk jobs are indexed.

- Device and provider compatibility matrix (`docs/compatibility.md`) from an Android 12 device run: overlay, playback audio, readable frames and lifecycle cases for YouTube Shorts, TikTok and Instagram Reels, plus `scripts/device_matrix_pull.ps1` to pull a cell's local evidence. BC-D02 records the advertised providers and devices.

- Opt-in backend privacy retention jobs with shared job-deletion fencing,
  upload-write locking, workspace/credential/replay cleanup and bounded
  tombstone expiry. Shared content-free API/worker logging and synthetic
  retention/recovery tests accompany the data map, Proposed BC-D06 windows
  and PDP transfer/sovereignty checklist. Automatic deletion is disabled by
  default and never expires Google-linked accounts; no permanent-account policy
  or legal approval is implied.
- Owner-scoped job cancellation and deletion APIs with durable replay receipts,
  atomic authorization/mutation, tombstones and explicit database-only cleanup.
  Shared job-action schemas and fixtures, API-driven recovery cases and
  cross-owner isolation checks extend the Backend recovery suite. New `intake`
  jobs carry their investigation's owner, so they can be cancelled or deleted
  through the API; jobs enqueued before migration `0006_job_ownership` remain
  ownerless and inaccessible through it.
- RES-02 ASR/OCR benchmark planning and offline scoring against the local corpus: dev-only integrity checks, WER and critical-entity spans, chunk offsets/caps, explicit failures, timestamp pairing, quota estimates, frame identity, spatial OCR matching, sampling misses and deduplicated observations. A separate opt-in Groq runner reuses existing media/SRTs for paired 10/15-second PCM trials, requires an approved plan hash, preserves private attempt records, and stops without retries on provider errors. CI remains offline; no production adapter selection or device performance is claimed.
- Non-phone RES-02 follow-up: whole-subtitle-cue timestamp diagnostics that flag raw provider overruns, actual three-minute request-prefix accounting, local Tesseract screenshot/source-frame comparison helpers, selected-region scoring and fixed/change window coverage. Native empty OCR iterator entries are handled explicitly, and tests cover coordinate assignment, missing detections and timestamp anchors. Detailed measurement artifacts remain private; source-video simulation is not phone evidence or a full-screen missed-text benchmark.
- Android contract models and compatibility tests for the whole `packages/contracts` package, completing AN-13 (#62) against the merged #15 handoff (PR #82): one `UNKNOWN`-tolerant Kotlin enum per `$def` in `enums.schema.json`, typed models for uploads, investigation creation, the investigation read model with its job and report version, claims, evidence, assessments, capture sessions and chunks, whose constructors enforce the schema rules (identifier, hash, timestamp and URL syntax, interval ordering, the investigation `oneOf` branches so a failed investigation can never carry a report, report cross-references), production codecs (`InvestigationCodec`, `UploadCodec`, `CaptureCodec`) with strict requests, additive-tolerant read models and explicit `ContractParseException`s instead of success-shaped defaults, and unit tests that read the six result fixtures, the intake fixtures and the schemas in place, assert the typed values of each fixture, map `"__future_value__"` to `UNKNOWN` for every enum, cross-check every enum and model against the committed schemas, and reject Android-only incompatible fixtures. #18 reuses these entry points; no endpoint is called.
- Opt-in BE-01 router experiment CLI (`services.experiments.router`, offline by default, HTTP-mocked tests) and the proposed [BC-D03](docs/decisions/BC-D03-provider-hosting.md) record for the claim-extraction provider order, EthioDeploy hosting and the limited BE-08 development go, confirmed by the product owner on 4 October 2026 and Conditional until its metrics summary is committed.
- Core contract in `packages/contracts` (still `0.1.0-draft`): JSON Schemas for upload declaration and completion, investigation creation and the investigation read model, jobs, capture sessions and chunks, report versions, claims, evidence and assessments; one `$def` per enum (job state, retry class, investigation state, stage, processing status, coverage, timebase, modality, relation, overall assessment, source inspection level, source type, retrieval relevance, retraction status and more) so the Android parser maps each to an `UNKNOWN`-tolerant enum; six result fixtures (complete, partial, failed, cancelled, insufficient-evidence, no-claims) generated from new backend Pydantic read models and checked for drift by `roundtrip.py --check`; intake fixtures; an OpenAPI 3.1 document whose schemas are references into the package, validated with `openapi-spec-validator` and spectral (all errors use the error shape, no untyped enums); a required **Contract checks** workflow that runs the validator, the round trip, the contract tests, pinned checksum-verified `oasdiff` (a breaking change fails without a `VERSION` bump) and the Android contract unit tests against the same fixtures; and the contract review rule (one Android and one backend reviewer) in WORKFLOW and CODEOWNERS.
- Durable backend job engine: a PostgreSQL queue claimed with `FOR UPDATE SKIP LOCKED`, idempotent stage keys `(version, stage, input hash)`, leases with fencing tokens and a cancellation/deletion generation, fenced compare-and-set publication, a property-tested job state machine (Hypothesis), lease draining on graceful shutdown in standalone and embedded worker modes, and a required **Backend recovery** CI job running the recovery cases against PostgreSQL. Typed retry classes (`transient` with jittered backoff, `rate_limited` honouring the provider hint, `non_retriable_input`, bounded `invalid_model_schema` repair and `unknown_outcome` reconciled by a request id recorded before the provider call) are scheduled through `available_at` with per-class caps from `OVRLY_JOB_RETRY_*` settings; infrastructure errors mid-stage re-lease the job instead of failing it. The recovery suite covers kill before commit, after the provider call and after the artifact store, lease expiry, cancel mid-retrieval, delete with a delayed callback, database connection drop, API lifespan restart, every retry class and duplicate provider callbacks.
- Draft voice-actions contract in `packages/contracts` (version `0.1.0-draft`): JSON Schemas for the `POST /v1/voice/actions` request and response, the shared BE-05 error shape, four typed error codes, synthetic accepted/denied/negative fixtures, a standard-library validator and pytest checks run by Backend CI. Mirrors the BC-D04 allowlist; the client-local tab switch stays out of the server contract. No endpoint or Android wiring.
- Android contract parsing for the shared voice-actions schema (AN-13, #62): `kotlinx.serialization` setup, typed `app.ovrly.contract` models with `UNKNOWN` enum fallbacks that never count as success, a strict production parser, and unit tests that read the committed `packages/contracts` fixtures and schemas in place plus Android-only incompatible fixtures. Builds on the merged BE-13 contract package (#67, PR #71); investigations, jobs, reports, claims and evidence follow the #15 handoff.
- Backend identity and intake API: guest principals with opaque bearer credentials, owner-scoped access that reports other owners' objects as missing, declared-and-verified uploads with byte, duration and expiry limits, idempotent investigation creation that writes a durable record before answering, owner-scoped investigation reads and one typed error shape for every failure. Each investigation is now handed to the durable job queue as an `intake` job in the same transaction as its record, the worker's `intake` stage confirms the owned record and publishes a placeholder result, and investigation reads reflect the job state (`queued`, `running`, `failed`, `cancelled`) without exposing worker internals. Account linking per BC-D07: `POST /v1/principals/link` verifies a Google ID token against the configured `OVRLY_GOOGLE_CLIENT_ID`, upgrades the calling guest in place, or on a second device merges explicitly saved reports into the existing account, revokes the guest credential and returns a credential for the account; investigations, uploads and temporary history never move. Media processing is BE-07; the saved-reports table is BE-10.
- Decision log under `docs/decisions`: RFC section 22 template, confirmed product scope (0001), task tracking and reference namespaces (0002), the CP2 scope decision (0003: AC10 complete with on-device export; AC08 second-device recovery disclosed unless verified before the freeze), BC-D04 Voxide route, session budget and provisional allowlist, BC-D07 account link flow (Google sign-in, in-place upgrade, second-device merge), and BC-D09 hackathon dates with an assumed EAT timezone. Linked from the README, WORKFLOW and architecture docs.
- Eleven-clip RES-06 draft metadata with 259 traceable occurrences, single-pass adjudications, separate main-argument assessments and a 7 dev / 4 test split. Explicit draft validation and CI preserve unresolved rights, coverage and media-review limitations; this is not a frozen benchmark.
- Voice session policy: 30-second setup timeout, five-minute input window from server ready, a bounded 30-second finishing reply with the microphone released, and a 15-second listening-silence stop. No automatic reconnection.
- Voice orb engine interface: interaction phases, a rate-limited 0..1 level, lossy presentation events, push-to-talk hold and user barge-in (the last two untested live).
- Voice orb in the Your space and Explore headers: tap to open, then tap to start, stop or interrupt, or hold for push-to-talk. Includes the microphone-permission flow and a gallery study. Settings keeps Stop. Checked on one device with the offline simulation and safe test builds; live voice is untested.
- Offline voice simulation in every build without the live opt-in, release included, labelled "Demo" on the orb and never triggering app actions. Explicit live build opt-in. No local attempt cap; provider usage is tracked against the dashboard.
- Offline five-action voice contract with strict target arguments and cancellation-only confirmation policy. Unconnected product commands return typed errors and remain absent from the live manifest.
- Pinned Android detekt/Compose and ktlint gates with reviewed finding baselines, Gradle dependency verification, and backend/repository vulnerability audits in CI and local pre-commit.
- Checksum-pinned Gitleaks scans of reachable history and tracked changes, with full redaction, a private Voxide configuration guard and synthetic enforcement tests.
- Shared backend/workflow quality gates with Ruff security rules, coded type ignores, pinned actionlint and offline zizmor.
- Backend CI with PostgreSQL/migration tests, strict types, service coverage, main-baseline comparison, seven-day reports and grouped uv dependency updates. Android coverage and required-check activation remain open under #13.
- Backend foundation with FastAPI readiness, PostgreSQL/Alembic, standalone or embedded worker lifecycle, local tests and one-command Linux/WSL setup. Durable jobs remain separate work.
- Native Kotlin/Compose Android companion with manually activated floating controls, consented playback-audio and sampled-screen capture, and temporary-media retention/deletion. Capture is limited to three minutes and 32 MiB.
- Share validation for supported video content URIs and web URL references, without downloading or analysis.
- Design gallery with 15 glass treatments, seven states per design, synthetic backdrops and higher-opacity comparisons.
- Configuration-gated Voxide experiment whose only action switches between Your space and Explore and reports when that tab is already open.
- Saved Light/Dark appearance, labeled Your space and Explore sample reports, search, topic filters and session-local sample saves.
- Larger sample overlay with simulated evidence interactions and confirmation before stopping a real session.
- Android 10+ native launch splash with a chrome ring, a 900 ms handoff and no artificial delay. Android 12+ follows the saved Light/Dark appearance.
- Versioned evaluation-data schemas, provenance-labeled annotation/adjudication workflow, synthetic JSONL examples and dependency-free validation with CI. The reviewed clip corpus is still pending.
- Reduced-scope eleven-clip local evaluation snapshot `res01-local-frozen-2026-10-04` in `evaluation/corpus-local/` (`kind: frozen-local`) with the owner's 2026-10-04 local-use acceptance, preserved 259 original/264 final decisions and 7/4 split, plus the intermediate `reviewed-draft/` and `freeze-candidate/` revisions it builds on. A new explicit `--frozen-local` validation mode checks it with the draft rules and additional modality/negative checks; strict full-coverage `--frozen` validation is unchanged and rejects it. Rights clearance remains pending on every clip and RES-01 stays open.

### Changed

- Evaluation contract v2 requires exactly one occurrence pass and one final adjudication per clip, without a second annotator. Explicit provenance replaces mandatory blind-human attestations; original-occurrence traceability, rights, scenario coverage and split-isolation safeguards remain. Version-1 snapshots require explicit migration, not silent relabeling.
- Added manual Telegram APK distribution: an exact merged `main` commit builds without credentials, then signs and delivers after owner approval. An immutable ledger supplies version codes; redelivery resends the current issued bytes. Release builds use R8 and resource shrinking. See [release signing](docs/release-signing.md).
- Reserved Instrument Serif for editorial report/gallery headings and the text-based wordmark. Functional headings, including capture dialogs and demo claims, use Lexend without changing type sizes.
- Added rendered chrome splash, adaptive launcher and header-wordmark assets. The splash fills Android's icon mask; header glare and Light-mode contrast treatments remain local drawing effects. Notifications retain the monochrome split ring. See [UI maintenance](docs/android-ui.md).
- Corrected the "A moment outside" artwork to use circles.
- Updated to Gradle 9.8.0, Android Gradle plugin 9.4.1 with Kotlin 2.2.10, and compileSdk/targetSdk 37. Dependencies include core 1.19.1, splashscreen 1.2.0, activity 1.13.0, lifecycle 2.11.0, savedstate 1.5.0, Compose BOM 2026.09.00, coroutines 1.11.0 and OkHttp 5.5.0. Lint treats warnings as errors.
- Organized the Android foundation under `android`, updated the Windows helper and reserved the `backend` boundary.
- Made Liquid Chrome the fresh-install default while preserving saved appearance choices; restyled controls and retained all 15 gallery studies and font licenses.
- Moved working controls into Settings without removing capture consent. The demo overlay stays below half the usable screen, with 16 dp margins, a draggable header and separate body scrolling.
- Added public-window background blur on supported Android 12+ devices, with opaque fallbacks.

### Fixed

- Android instrumented checks no longer hang or fail before testing on API 29
  (#76 follow-up): API 29 cold-boots instead of resuming a cached snapshot, the
  device must publish its system services before any test, the emulator is
  stopped with a bounded grace period, and a hung test fails within its
  5-minute timeout or the 14-minute test budget with logcat and the running
  test's name.

- Share intake no longer crashes on a file whose provider calls it a video but
  whose bytes are not a readable media container; it is rejected as not a
  readable video.

- Addressed RES-02 benchmark review: withdraw the unreviewable hosted model/chunk preference, label the private OCR procedure explicitly, distinguish existing evidence from remaining plan gates, and guard zero-reference aggregate rates. Opt-in `res02-v2` scoring fixes currency and letter-number tokenization and counts ignored punctuation-only ASR segments without changing v1 baseline scoring or timestamp indexes.

- Telegram PR announcements survive delayed or dropped GitHub webhooks: `synchronize` and `reopened` post the PR card only when none is recorded for that PR number, an hourly catch-up (also manual) announces open PRs without a recorded card, and a malformed or missing announcement state fails the job without posting rather than risking duplicates. Draft, ready-for-review, merge and close handling is unchanged.
- Accept bounded incoming voice bursts without a 16-message UI backlog cutoff; decode off the UI thread and deliver ordered, fair batches with explicit memory limits.
- Make voice playback buffering independent of audio chunk count with a fixed 10 MB ring buffer, add a stalled-speaker watchdog, and show the current voice status in Settings.
- Move voice socket cancellation and pooled TLS cleanup off the UI thread to prevent a stop-time Android crash. Add bounded, payload-free connection and traffic diagnostics.

- Reject database-test URL and inherited routing overrides before connecting, keeping migration tests on their disposable database.
- Reject whitespace-suffixed evaluation IDs and SHA-256 values, including hashes used for cross-split isolation. Preserve Unicode separators inside JSON strings when reading physical JSONL lines.

### Known limitations

- Research, transcription, OCR and evidence retrieval are not connected; shared media is recorded by the backend intake API and queued as an `intake` job, but no media stage processes it yet.
- Gallery and report content are labeled samples. Gallery selection does not change the live overlay.
- Native blur depends on device/system support; no backdrop refraction is implemented.
- Physical-device testing is partial. Full cross-app capture/lifecycle behavior and live Voxide authorization/compatibility remain unverified.
- Android 12 shows the splash icon only for home/system-originated launches. It was visible from Samsung One UI Home but absent from Niagara Launcher and `adb shell am start`; the Android 15 emulator showed it from every launch source. No supported per-app override exists below Android 13.
