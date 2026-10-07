# Contracts

Investigation `analysis` is optional extraction progress, separate from report
`processing_status`: `pending`, `partial`, `no_usable`, or `complete`. See
`analysis.schema.json`. Partial analysis can contain usable device text even when
speech quota is exhausted. `text_deadline` is a persisted, configurable grace
deadline (default 60 seconds); `text_expired` records resolution without completion.
Late text may enrich eligible input without redoing speech. Empty OCR, missing
delivery and frame failures remain distinct; sampled text never proves continuous
coverage. No analysis state implies claim reconciliation, research or a report.

**Version `0.2.0-draft` (pre-1.0).** Shared JSON Schema definitions and synthetic
fixtures that the Android client and the backend build from. This package was
started by BE-13 ([#67](https://github.com/natnael-solomon/ovrly/issues/67)) with
the voice-actions slice. BE-03 ([#15](https://github.com/natnael-solomon/ovrly/issues/15))
owns the package as a whole: the upload, investigation, job, capture, report,
claim, evidence and assessment schemas, the shared enums, the six result
fixtures, the OpenAPI document, the server round-trip check and the required
**Contract checks** workflow (schema validation, spectral, oasdiff, server
round trip and the Android compatibility tests against the same fixtures).
BE-04 part 3 (#75) adds the job action schemas and receipts, exercised by the
backend endpoints. Nothing here implements Android wiring.

## Layout and namespaces

| Path | Content |
| --- | --- |
| `VERSION` | Contract version. Stays `0.x.y-draft` until the allowlist is fixed at AN-09 (#35). |
| `openapi.json` | OpenAPI 3.1 document for the served `/v1` routes; every schema is a `$ref` into `schemas/`. |
| `.spectral.yaml` | Spectral ruleset: `spectral:oas` plus the two contract rules (errors use the error shape, no untyped enums). |
| `schemas/*.schema.json` | One file per resource or endpoint body; see the [schema index](#schema-index). |
| `fixtures/voice-actions/*.json` | Synthetic voice request/response scenarios with explicit expectations. |
| `fixtures/results/*.json` | The six investigation read payloads (complete, partial, failed, cancelled, insufficient-evidence, no-claims), generated from the backend models. |
| `fixtures/intake/*.json` | Hand-authored upload, investigation-create and capture samples, each naming the schemas it exercises. |
| `fixtures/jobs/*.json` | Synthetic job action receipts and safe error examples; see [Job actions](#job-actions). |
| `validate.py` | Standard-library validator for the schemas and all four fixture directories. |
| `roundtrip.py` | Builds `fixtures/results` from `backend/services/api/schemas.py` and diffs them (`--check`) or rewrites them (`--update`). |
| `openapi_check.py` | Validates `openapi.json` with `openapi-spec-validator` and applies the contract rules without Node. |
| `compat.py` | Breaking-change gate: pinned `oasdiff` plus enum/schema-file diff against a base; fails without a `VERSION` bump. |
| `tests/` | pytest suite, run from the backend uv project with its `contracts` dependency group. |
| `ruff.toml` | Extends the backend Ruff configuration so the Python here meets the same gates. |

Schema files are named `<resource>.schema.json` or `<endpoint>-request|response.schema.json`,
draft 2020-12, `additionalProperties: false` at every object, shared types in
`$defs`, cross-file references by relative file name
(`enums.schema.json#/$defs/job_state`). Fixtures live in one directory per area.
The error shape is the one fixed in BE-05 (#19), so there is exactly one error
shape contract-wide; every error any endpoint returns must use it (the
spectral and `openapi_check.py` rule "all errors use the error shape"). JSON
files are ASCII with LF endings (`.gitattributes` pins `*.json` to LF; refresh
an older Windows checkout with `git checkout -- packages/contracts` if the
round-trip check reports whole-file diffs).

## Schema index

| Schema | Mirrors | Used by |
| --- | --- | --- |
| `error.schema.json` | `services/api/errors.py` error body | Every error response; `investigation.error` reuses `code`, `message`, `retryable` by reference. |
| `common.schema.json` | `$defs` only: `opaque_id` (the voice-action syntax), `uuid`, `sha256`, `timestamp`, `interval`, `seq_range`, nullable helpers | All schemas below. |
| `enums.schema.json` | `$defs` only; see the [enum table](#enums) | All read models. |
| `upload-declare-request.schema.json` | `UploadCreateRequest` | `POST /v1/uploads` body. |
| `upload.schema.json` | `UploadResponse` | Read model; `upload-declare-response` and `upload-complete-response` are `$ref` wrappers so each endpoint has a pair. |
| `upload-complete-request.schema.json` | No body (empty object) | `POST /v1/uploads/{id}/complete`. |
| `investigation-create-request.schema.json` | `InvestigationCreateRequest`; `UrlSource` or `UploadSource` discriminated on `kind` | `POST /v1/investigations` body. |
| `investigation.schema.json` | `InvestigationReadModel` (`InvestigationResponse` plus `processing_status`, `job`, `report`) | `POST /v1/investigations` 202, `GET /v1/investigations[/{id}]`; the six result fixtures. |
| `speech.schema.json` | `SpeechResult`, `SpeechSegment` | Optional `investigation.speech`, independently readable from media coverage. |
| `analysis.schema.json` | `AnalysisRead` | Optional `investigation.analysis` extraction progress, gaps and deadlines. |
| `speech-retry-request.schema.json`, `speech-retry-response.schema.json` | `SpeechRetryRequest`, `SpeechRetryResponse` | `POST /v1/investigations/{id}/speech/retry`; the outcome is recorded once per Idempotency-Key. |
| `job.schema.json` | `JobSummary` (client-visible columns of `jobs`) | Nested in an investigation; voice `job` targets. |
| `report-version.schema.json` | `ReportVersion` | Nested in an investigation; `GET /v1/investigations/{id}/reports/{version}` and the `report` snapshot of the inline `SavedReport` component (BE-10, #33). The optional `fixture` boolean (absent reads as false; the server always sends it) is true only for development stub versions (`OVRLY_STUB_REPORTS`), which clients label as a fixture, never as live results; the Android `ReportVersion` mirrors it with a default of false. |
| `claim.schema.json` | `Claim`, `ClaimCorrection`, `Interpretation`, `SourceRef` | Items of `report.claims`; the interpretation types live in `services/claims.py`. |
| `evidence.schema.json` | `Evidence`, `EvidenceSource` | Items of `report.evidence`. |
| `assessment.schema.json` | `Assessment`, `EvidenceRelation` | Items of `report.assessments`. |
| `capture-session.schema.json` | `CaptureSession` in `services/api/capture_schemas.py` | Live-overlay session read model. |
| `capture-chunk-request.schema.json`, `capture-chunk.schema.json` | `CaptureChunkRequest`, `CaptureChunk` in `services/api/capture_schemas.py` | Chunk declaration and acknowledgement. |
| `voice-action-request.schema.json`, `voice-action-response.schema.json` | BE-13 slice, unchanged | `POST /v1/voice/actions`. |
| `job-action-request.schema.json` | Path parameters of the job actions (`job_id`); no body | `POST /v1/jobs/{job_id}/cancel`, `DELETE /v1/jobs/{job_id}`. |
| `job-cancel-response.schema.json` | `CancelResponse` in `services/api/routes/jobs.py` | `POST /v1/jobs/{job_id}/cancel` 200 and 202. |
| `job-delete-response.schema.json` | `DeleteResponse` in `services/api/routes/jobs.py` | `DELETE /v1/jobs/{job_id}` 200. |

"Mirrors" names the Pydantic class in `backend/services/api/schemas.py`. The
request models, `InvestigationResponse` and `UploadResponse` predate the core
read-model contract; `InvestigationResponse` now adds optional timed `speech`.
`Coverage`, `Interval`, `Claim`, `ClaimCorrection`,
`EvidenceSource`, `Evidence`, `EvidenceRelation`, `Assessment`, `ReportVersion`,
`JobSummary` and `InvestigationReadModel` are read models. Since BE-10 (#33)
`POST /v1/investigations` (202), `GET /v1/investigations/{id}` and the list items
return `InvestigationReadModel`, so `processing_status`, `job` and `report` are
always present, as the schema requires; `backend/tests/test_investigation_read_model.py`
validates the live responses in each status against `investigation.schema.json`.

### Completed-upload device text (backend-only v1)

This upload-specific handoff reuses
Android `CaptureText.kt`'s frame fields and normalized boxes, **not** its
capture chunk transport. Android completed-upload sending and physical-device
end-to-end verification are separate follow-up work. The committed
`fixtures/device-text/synthetic.json` is invented backend test input, not
device-origin evidence.

`device-text.schema.json` defines `TextBatch`, `TextCompletion` and
`DeviceTextRead`; `python device_text.py` checks generated-schema drift.
Authenticated routes for a validated, owned **video** upload:

- `PUT /v1/investigations/{id}/device-text/batches/{batch_id}` stores a batch.
- `POST /v1/investigations/{id}/device-text/complete` seals delivery.
- `GET /v1/investigations/{id}/device-text` reads the separate text model.

Every request identifies `protocol_version: 1`, `upload_id`, `source_sha256`,
`timebase: media`, `rotation_degrees: 0`, `box_space: normalized_10000`.
The sender must apply media rotation before normalizing full-frame boxes
`[left, top, right, bottom]` to integers 0..10000. Crops are translated back
to that full upright frame. No image is sent. `frame_pts` is an integer
original-media timestamp in milliseconds, `0 <= frame_pts < validated duration`;
an observation's timestamp must equal its containing frame.

Frames retain `regions`, `recognition_ms`, `failed_regions` and
`text_observations` (`id`, original `text`, `box`, `frame_pts`):
`recognized` requires at least one successful region and may contain zero text;
that is checked-empty, not missing work. Partial region failures remain explicit.
`failed` means every region failed and has no observations.
`no_text_regions` means the producer's heuristic found zero regions, **not**
successful full-frame OCR. Optional recognizer name/version and the existing
`change_triggered` sampling facts are retained and must agree across submissions.

Batch IDs are 0..63 and may arrive out of order. Frame timestamps and observation
UUIDs are unique within an investigation; reuse in another batch conflicts.
Identical parsed-content replay returns the current snapshot without duplication;
changed content under an identity is 409. JSON ordering/whitespace and omitted
optional null metadata do not change identity; observation text is never trimmed.
Duplicate JSON keys, coerced scalars and unknown fields are rejected.

Completion names `batch_count` and requires exactly batches `0..batch_count-1`.
Zero batches is valid. It retains explicit `dropped_frames`, `capped_frames`
and `unfinished_frames`; counters are bounded nonnegative integers, reported
facts rather than independently measured telemetry. Completion is immutable;
existing batch replays remain safe, new batches conflict. A completed delivery
does not establish successful OCR, continuous coverage, or completed research.
Missing completion remains `receiving`; no submission is `not_started`.

Limits: 262144 request bytes (also enforced on chunked bodies), 100 frames/batch,
100 observations/frame, 4096 characters/text; per source 64 batches, 1200 frames,
5000 observations and 2097152 canonical JSON batch bytes. Source-level limits and
identity changes are atomic. Source/identity/lifecycle conflicts return safe 409,
payload budgets 413, media type 415, and malformed fields/timestamps 422.

The read model's `job_id` is an owner-scoped cancellation/deletion fence
(`upload_device_text`), deliberately unclaimed until later aggregation work.
Completion does not publish a research-stage result. Cancellation preserves prior
readable text but blocks all subsequent submissions. Deleting the text job,
intake, validated-media or uploaded-speech prerequisite clears text content and
leaves an investigation-scoped fence, even before the first submission. Purging
the prerequisite's job tombstone cannot reopen ingestion or completion. Guest retention
cascades it with the investigation. Text stays separate from speech/captions;
no server OCR, provider call, 60-second timer or aggregation is added here.

### Investigation read model

BE-08 whole-input reconciliation adds optional nullable `reconciliation_progress`
to investigation/capture reads and `reconciliation` to reports. Its typed status
is separate from assessment completion: success publishes `status: complete` but
does not change `report.provisional` to false. `coverage_limited` discloses settled
upstream/observation gaps. `reassessment_claim_ids` and the immutable report/version
form the downstream handoff for changed nonsuperseded claims; their old evidence
and assessments are removed. `change_summary` explains the update.
Optional reciprocal `claim.corrects_occurrence_id` and
`claim.superseded_by_occurrence_id` preserve both original appearances. Earlier
versions and original wording stay unchanged.

Reconciliation failure preserves available provisional claims, with a safe error
under `reconciliation_progress.error`; the top-level report/error branches stay
unchanged. The new `reconciliation_status` enum is waiting, checking, complete,
failed or cancelled. Android retains unknown values as `UNKNOWN`, not completion.
These additions are backward-compatible in `0.2.0-draft`; older payloads omit them.
Android/backend human review is still required before merge.

`investigation.schema.json` keeps progress, stored state, the queue job, the
error and the findings apart so a failure can never be read as a finding:

| `processing_status` | `state` | `report` | `error` |
| --- | --- | --- | --- |
| `waiting` | `queued` | `null` | `null` |
| `checking` | `running` | `null` | `null` |
| `partial` | `running` | object, `provisional: true` | `null` |
| `complete` | `completed` | object, `provisional: false`, every claim assessed | `null` |
| `failed` | `failed` | `null` | object (`code`, `message`, `retryable`) |
| `cancelled` | `cancelled` | `null`, or the version published before the cancel | `null` |

The three `oneOf` branches are discriminated by the JSON types of `report` and
`error`, not only by the enum values, so they stay unambiguous when a client
relaxes enum checks. `coverage` says how much media was checked and is never a
verdict; the intake placeholder is `{"status": "not_started"}`. There is no
report-level verdict anywhere in the contract (decision record 0001): an
`overall_assessment` belongs to one claim, `insufficient_evidence` describes the
evidence, and an empty `claims` array means no assessable factual claim was
found, not that the video is accurate.

After upload-backed media validation, optional `coverage.media` records
`has_audio`, `has_video`, `speech_status`, `text_status`, and nullable
`speech_unavailable_reason`. Its enum definitions are centralized alongside the
other vocabularies. Video without audio reports speech `unavailable` with
`no_audio_track`; text remains `pending`. Audio-bearing media reports speech
`pending`, including silent audio tracks. Preparation keeps coverage
`not_started`; measured `total_ms` is not assessed coverage. Absent or null
`media` preserves compatibility with pre-validation payloads. Android's
investigation parser is still pending #62 part 2; the server tests do not
establish client integration.

Optional `speech` is null before speech eligibility is known, or contains
`status`, nullable `reason`, `provider`, `model`, `processing_version`,
source/audio/settings SHA-256 digests, and `segments` (`text` plus a half-open
millisecond `interval`). It does not replace preparation coverage, supplied
captions or device text. Completed empty segments mean no recognized speech;
they do not mean absent audio or no claims. No-audio and disabled processing
report `unavailable` with separate reasons. Provider request/account identifiers,
request markers and storage paths are never exposed.

The server currently implements Groq-only uploaded speech, on by default; without its
provider configuration speech is `unavailable` with reason `provider_unavailable`.
`ASR_QUOTA_EXHAUSTED` stops automatic attempts; `ASR_UNAVAILABLE` includes an
explicit `unknown_outcome` reason for interrupted calls that cannot be
reconciled. Neither a quota reset nor polling retries old speech. Successful
speech leaves investigation state queued for later analysis; media coverage
remains the original preparation facts. Partial analysis is described at the
top of this file and owner retry in the schema index; Android speech parsing is
later work.
See [hosted speech configuration](../../backend/README.md#hosted-uploaded-speech).

For a capture-source investigation, `speech` concatenates the published
chunks' segments on the `capture` timebase (aggregate source/audio digests are
null because several chunks contribute), and `analysis.text` uses the
`capture_text` shape: `{"timebase": "capture", "chunks": [...]}`, where each
chunk carries `seq`, its `interval`, the package `source_sha256`, `sampling`,
`recognizer` and `frames` from the device-text definitions. Capture text is
complete on delivery, so `text_deadline` is null. Gaps are per chunk; the
additional reasons `CAPTURE_CHUNK_MISSING` (an interval the manifest never
received) and `CAPTURE_CHUNK_INVALID` (a delivered chunk whose package failed
validation) apply only to captures. The speech retry endpoint also accepts
capture investigations and re-queues only quota-blocked chunks.

`error` is the stored subset of the shared shape: `code`, `message` and
`retryable`, by `$ref` into `error.schema.json`. `request_id` is absent because a
stored failure is not tied to the request that reads it and `action` is not
stored today (`services/api/errors.py safe_error`).

Optional nullable `extraction_progress` on investigation and capture-status reads
describes the internal incremental producer's accepted observations, not upstream
ASR/OCR completeness. It is absent for legacy/non-incremental work. Each original
interval has `observation_id`, `timebase`, `status` (`pending`, `processed`,
`skipped`, `failed`) and nullable diagnostic `reason`. Budget gaps use
`budget_exhausted` (including producer input beyond the run's observation bound);
input arriving after the producer closed uses `input_closed`; missing/purged jobs use
`job_unavailable`, cancellation and
deletion are skipped, and unusable context uses `context_without_target`.
Failed windows use allowlisted public extraction error codes, or
`PROCESSING_FAILED` for an unknown internal failure; exception names are not
part of the contract. A successful overlapping window counts its source
observations as processed even if their first assigned window failed.
Processed means its extraction window published, not that the content is true
or that reconciliation finished. Unknown states remain `UNKNOWN` on Android.
`closed` means producer input closed (or capture Stop disallowed continuation).
The nonnegative int32 `requests_used`/`tokens_reserved` are conservative pre-send
reservations, not actual usage; positive int32 ceilings and reconciliation
reserves are disclosed alongside them. Failures retain the existing error/report
branch rules; prior immutable reports stay available on their own endpoints.

Capture sessions (`capture-session`, `capture-chunk-request`, `capture-chunk`)
state the duplicate and out-of-order rules as data: `(session_id, seq)` is the
idempotency key, a repeat with the same bytes replays the acknowledgement with
`disposition: duplicate`, a chunk above a missing `seq` is stored with
`disposition: out_of_order` and the missing range appears in `gaps` until it
arrives. Intervals on these schemas must use the `capture` timebase; claims from
a shared video use `media`.

## Enums

BE-08 adds optional nullable `claim.interpretation`: taxonomy, source/context
references, assertion mode, speaker commitment, attribution, eligibility and
uncertainty. Existing payloads without it remain readable; the server omits
an absent interpretation rather than rewriting legacy fixtures with nulls.
References are half-open character offsets into stable observation IDs.
Source references are nonempty, spans ordered and uncertainty flags distinct.
The bounded pipeline validates IDs and offsets against the original input.
Normative content is not empirically eligible, and extraction alone never
creates an assessment. Kotlin models retain typed unknown-enum fallbacks.
The standard-library validator now also supports `minItems` and `uniqueItems`.

Reports may include nullable `processing_attempts` diagnostic provenance:
provider, actual model, completion decision ID when present, task, outcome,
nullable nonnegative int32 token counters, hygiene flags, repair and feedback.
Absent metadata is omitted by the server and remains compatible with older
fixtures. These descriptive strings are not enums or client success states;
unknown feedback/outcome values never imply a completed assessment. The Kotlin
mirror preserves nullable identifiers and strictly validates token counters.

Each value list is one `$def` in `enums.schema.json` so #62 maps it to one Kotlin
enum with an `UNKNOWN` fallback. The source column says where the list comes
from; `tests/test_results.py` diffs `job_state` and `retry_class` against the
backend enums and the read-model lists against their `Literal` aliases in
`backend/services/api/schemas.py`. Interpretation lists are compared with the
field schemas generated by `backend/services/claims.py`.

| Enum | Values | Source |
| --- | --- | --- |
| `job_state` | `queued`, `leased`, `running`, `published`, `cancelled`, `deleted`, `failed` | `services/jobs/states.py JobState` |
| `retry_class` | `transient`, `rate_limited`, `non_retriable_input`, `invalid_model_schema`, `unknown_outcome` | `services/jobs/retries.py RetryClass` |
| `investigation_state` | `queued`, `running`, `completed`, `failed`, `cancelled` | `schemas.py InvestigationState` (pre-existing) |
| `stage` | `intake`, `media_validation`, `asr`, `device_text`, `claim_extraction`, `retrieval`, `assessment`, `reconciliation`, `publication` | Build contract section 4; intake, upload preparation, opt-in speech, bounded upload-text admission, capture byte validation, and configured evidence stages are implemented; claim extraction and text aggregation remain separate work |
| `media_speech_status` | `pending`, `unavailable` | `MediaSpeechStatus`; preparation eligibility, not a speech result |
| `media_text_status` | `pending` | `MediaTextStatus`; no frame-processing outcome claimed |
| `speech_unavailable_reason` | `no_audio_track` | `SpeechUnavailableReason`; not silence within a track |
| `speech_status` | `pending`, `running`, `completed`, `unavailable` | `SpeechStatus`; separate from preparation/research progress |
| `speech_reason` | `disabled`, `no_audio_track`, `quota_exhausted`, `unknown_outcome`, `provider_unavailable`, `cancelled` | `SpeechReason`; unavailable speech is not an empty transcript |
| `asr_provider` | `groq` | `ASRProvider`; no fallback |
| `processing_status` | `waiting`, `checking`, `partial`, `complete`, `failed`, `cancelled` | Build contract section 3 |
| `coverage_status` | `not_started`, `partial`, `complete` | `routes/investigations.py COVERAGE_PLACEHOLDER` plus the two pipeline values |
| `extraction_coverage_status` | `pending`, `processed`, `skipped`, `failed` | `schemas.py ObservationProgress`; accepted-observation coverage, not a finding |
| `upload_state` | `pending`, `completed` | `schemas.py UploadState` (pre-existing) |
| `source_kind` | `url`, `upload` | `schemas.py UrlSource` / `UploadSource` |
| `timebase` | `capture`, `media` | Build contract section 4, decision record 0001 |
| `modality` | `speech`, `text`, `both` | Evaluation contract, RFC section 13 |
| `relation` | `support`, `challenge`, `qualify`, `mixed`, `insufficient` | RFC section 08, build contract section 3 |
| `overall_assessment` | `supported`, `challenged`, `qualified`, `mixed`, `insufficient_evidence` | Per-claim aggregate; introduced here |
| `source_inspection_level` | `abstract_only`, `full_text`, `metadata_only`, `unknown` | RFC section 08 |
| `source_type` | `peer_reviewed`, `preprint`, `government`, `news`, `reference_work`, `primary_document`, `organization`, `other` | RFC section 08 source identity; first draft |
| `retrieval_relevance` | `high`, `medium`, `low` | Introduced here; distinct from `relation` on purpose |
| `retraction_status` | `none`, `corrected`, `retracted`, `withdrawn`, `unknown` | RFC section 08, evaluation tag `withdrawn-retracted-source` |
| `correction_attribution` | `user`, `pipeline` | BE-10 (#33) reanalyze with reason `correction` |
| `capture_session_state` | `open`, `closed`, `abandoned` | Introduced here |
| `chunk_disposition` | `stored`, `duplicate`, `out_of_order` | Introduced here |
| `claim_taxonomy` | `empirical`, `causal`, `documentary`, `predictive`, `normative`, `mixed`, `unclear` | `claims.py Interpretation` |
| `assertion_mode` | `asserted`, `reported`, `questioned`, `hypothetical`, `counterfactual`, `unclear` | `claims.py Interpretation` |
| `speaker_commitment` | `endorsed`, `rejected`, `uncommitted`, `unclear` | `claims.py Interpretation` |
| `eligibility_reason` | `factual-claim`, `factual-premise`, `opinion`, `quoted-not-endorsed`, `insufficient-context`, `not-a-claim` | `claims.py Interpretation` |
| `claim_uncertainty` | `unresolved-reference`, `missing-context`, `ambiguous-attribution`, `ambiguous-commitment`, `ambiguous-meaning`, `source-text-conflict` | `claims.py Interpretation` |

`processing_status`, `job_state` and `investigation_state` share no value with
`relation` or `overall_assessment` (tested), so a failed or cancelled request is
not representable as a finding. The `error.action` enum stays inline in
`error.schema.json` as fixed by BE-05.

### Unknown enum values (the #62 pattern)

The server never emits a value outside this version's lists and strict validation
rejects one. Every read model (investigation, job, report version, claim,
evidence, assessment, upload, capture session, capture chunk) and the voice
response must still be parseable when a newer server adds a value: the Android
parser maps the unknown string to `UNKNOWN`, never to a success, stored,
completed or finding state, and keeps reading the rest of the payload.
`validate.py` has a `relax_enums` mode that checks exactly that, and
`tests/test_results.py` proves it for every `$def` in `enums.schema.json`
(`test_every_enum_has_an_unknown_tolerant_read_path` fails when an enum is added
without a read path). Requests (`*-request.schema.json`) are never relaxed; the
server validates them strictly and a fixture cannot declare `unknown-enum` for one.

## Result fixtures

Every file in `fixtures/results/` is one investigation read payload:

```json
{
  "synthetic": true,
  "description": "...",
  "expect": {
    "payload": "valid",
    "processing_status": "complete",
    "state": "completed",
    "claim_count": 2,
    "assessment_count": 2
  },
  "investigation": { "...": "..." }
}
```

`expect.payload` is `valid` or `unknown-enum`. `failed` adds `expect.error_code`.
The validator enforces the expectations and the model's own rules: status/state
and status/job-state fit the table above, counts match the report, `report.
investigation_id` and `version` echo the investigation, assessments and evidence
refer to claims in the same version, relations refer to evidence in the same
version, at most one assessment per claim, intervals satisfy
`0 <= start_ms < end_ms`, an assessment with no relations is
`insufficient_evidence`, a complete report is not provisional and assesses every
claim, no claims means no evidence and no assessments, and a failed payload has no
report.

| Fixture | Scenario |
| --- | --- |
| `complete` | Shared video fully checked; report version 2 after a user correction of one claim's proposition (`correction.attributed_to: user`), superseding version 1; one claim `supported`, one `qualified`; job `published`. |
| `partial` | Live capture in progress on the `capture` timebase; coverage partial, report provisional, one of two claims assessed (`challenged`); job `running` on attempt 2 after a `transient` retry. |
| `failed` | Media validation rejected the input: `error.code` `MEDIA_UNSUPPORTED`, job `failed` with `retry_class: non_retriable_input`, no report. |
| `cancelled` | Cancelled during `asr` before any publication: job `cancelled` with `cancel_requested: true`, coverage partial, no report, no error. |
| `insufficient-evidence` | Complete; the only claim has a retracted study read at abstract level and a metadata-only news item, both `insufficient`, overall `insufficient_evidence`. |
| `no-claims` | Complete; report with empty `claims`, `evidence` and `assessments` and a change summary saying so. Not a verdict that the video is true. |

All six are generated by `roundtrip.py` from the Pydantic models with
`model_dump(mode="json")`, the call the routes use; none is hand-authored. Backend
row keys are UUIDs and cannot spell "synthetic", so fixtures draw them from the
reserved prefix `00000000-0000-4000-8000-`; every other identifier contains
`synthetic`. The validator rejects both a real-looking UUID and a plain opaque id.
`MEDIA_UNSUPPORTED` is a proposed code for the media-validation stage; #33 and
#19 part 2 confirm the names.

### Intake fixtures

`fixtures/intake/*.json` are hand-authored samples with a `schemas` block naming
the schema of each payload and a matching `expect` block (`valid`, `invalid` or,
for responses, `unknown-enum`): `upload-declare`, `upload-complete`,
`investigation-create-url` (declared duration), `investigation-create-upload`
(`duration_ms: null`), `investigation-create-mixed-source` (must fail),
`capture-session-open` (gap at seq 3), `capture-chunk-out-of-order` and
`capture-chunk-duplicate`. The tests load the request payloads into the Pydantic
request models and check that the upload and investigation responses are exactly
what `UploadResponse` and `InvestigationReadModel` serialise.

## Handoff to #62 and #18

The schemas and fixtures above are the handoff named in #15 and #62. For the
Android models and parser:

- Entry points: `schemas/enums.schema.json` (one Kotlin enum per `$def`, each
  with `UNKNOWN`), `schemas/investigation.schema.json` and the schemas it
  references (`job`, `report-version`, `claim`, `evidence`, `assessment`,
  `common`, `error`), then `upload.schema.json`, `capture-session.schema.json`
  and `capture-chunk.schema.json`.
- Fixtures to parse: the `investigation` member of each `fixtures/results/*.json`
  (all six), the `response` members of `fixtures/intake/*.json`, and the
  `request` members as encode targets. Read them in place through the existing
  Gradle test resources directory; do not copy them.
- Assert typed values from the fixture table above (status, state, counts,
  `error.code`, `overall`, `retraction_status`, `correction.attributed_to`,
  `cancel_requested`, `disposition`), not merely that parsing does not throw.
- UNKNOWN rule: every enum field maps an unknown string to `UNKNOWN`; `UNKNOWN`
  is never `complete`, `published`, `completed`, `stored`, `supported` or any
  other success or finding state, and `isComplete`-style helpers return false
  for it. Keep reading the rest of the payload. Missing required fields, wrong
  types and `additionalProperties` violations are explicit parse failures;
  unknown keys on read models are tolerated as additive fields, exactly as the
  voice parser does.
- `report` and `error` are nullable, never absent. `coverage.covered_ms` and
  `total_ms` may be absent or `null`; `source.duration_ms` is absent when not
  declared.
- Identifier types: `id` of uploads, investigations, jobs and `upload_id`,
  `investigation_id`, `session_id` are UUID strings; claim, evidence, source,
  assessment and report-version ids are `opaque_id` strings. A UUID is a valid
  `opaque_id`, so voice-action targets can name any of them.
- #18 reuses the models and parser #62 delivers; the Room state names it chooses
  are its own and must not be confused with `job_state` or `processing_status`.

Record the reviewed schema/fixture link in #62, #18 and #33.

## Job actions

`POST /v1/jobs/{job_id}/cancel` and `DELETE /v1/jobs/{job_id}` require bearer
authentication and **no HTTP body**. `job-action-request.schema.json` describes
the path parameters, not a JSON body. IDs in fixtures/responses use canonical
UUID strings, matching the queue. Job owners come only from authenticated
enqueue callers, never client body fields or payload contents.

Cancellation returns `job_id` and `cancellation`: `effective` (200) or
`requested` (202). Its first outcome is persisted and replayed unchanged, even
after the worker acknowledges or the API restarts; this is a receipt, not a
progress snapshot. A published/failed job is 409 `JOB_NOT_CANCELLABLE`.
Cancellation makes no promise about provider interruption or billing.

Deletion returns `job_id`, `state: deleted`, `access_revoked: true`,
`cleanup_status: complete` and `cleanup_scope: job_payload_and_result` (200).
Cleanup is limited to the database payload/result: uploads, external artifacts,
provider copies and backup expiry are **not** covered. The tombstone retains
ownership/fencing metadata. The owner can repeat DELETE without mutation,
but cancellation receipts become inaccessible after deletion (404).

Missing, legacy ownerless and other-owner jobs return the identical shared
404 `NOT_FOUND` shape. No owner-specific error distinguishes them.

`fixtures/jobs` uses `synthetic`, `description`, `operation`, `status`,
`request` (path parameters) and `response`. The validator checks schema,
status/outcome, synthetic ids and ID consistency. `openapi.json` publishes both
operations with the `JobId` path parameter (a reference into
`job-action-request.schema.json`), the `JobCancelResponse`
and `JobDeleteResponse` components and the shared `Error` responses. Backend recovery tests compare these
fixtures with the real endpoints, round-trip the response models, reject
invalid payloads and prove unauthorized requests cannot alter a job.
The addition leaves existing voice schemas and version unchanged; both Android
and backend review are required under #15. Android job parsing remains #62.

## Voice actions

Decision record: [BC-D04](../../docs/decisions/BC-D04-voxide-route.md). The
server enforces the same allowlist the Android `VoiceCommandContract` checks
offline, so a command the client rejects is also one the server rejects.

| Action | Target kind | Confirmation |
| --- | --- | --- |
| `open_check` | `investigation` | None |
| `save_report` | `report` | None |
| `queue_cancel` | `job` | On the device, before the request is sent |
| `queue_retry` | `job` | None |
| `queue_continue` | `job` | None |

There are no delete, publish or settings actions, and no free-form arguments.
Confirmation is never a request field: a remote `confirmed` flag fails
validation. Retry and continue remain subject to job state on the server.

### Request

```json
{
  "request_id": "req_synthetic_0001",
  "action": "open_check",
  "target": { "kind": "investigation", "id": "inv_synthetic_0001" }
}
```

- `request_id` is a client-generated idempotency key. Repeating it returns the
  original outcome without executing the action twice.
- `target.kind` must match the action (`oneOf` in the schema); a mismatch is a
  validation error, not a `denied` response.
- Identifiers (`request_id`, `target.id`) are opaque and case-sensitive: 1 to 128
  ASCII characters, starting with a letter or digit, then letters, digits, `_`
  or `-`. `common.schema.json#/$defs/opaque_id` references this definition, so
  it is the one identifier syntax of the contract; backend row keys are UUIDs,
  which satisfy it. Valid syntax proves neither existence nor ownership.
- `additionalProperties` is `false` at every level.

### Response

```json
{
  "request_id": "req_synthetic_0001",
  "result": "accepted",
  "action": "open_check",
  "target": { "kind": "investigation", "id": "inv_synthetic_0001" },
  "message": "Opened the check."
}
```

`result` is `accepted` or `denied`. Accepted responses carry an allowlisted
`action`, echo the `target` and never carry `error`. Denied responses require
`error` in the shared shape, echo the requested action name (so the overlay can
name an unsupported action) and echo the target when the request carried a
well-formed one. `message` is always present: one short English sentence that
is safe to show in the overlay or speak back, with no transcript and no other
user's identifier.

| Error code | Meaning | `retryable` / `action` |
| --- | --- | --- |
| `VOICE_ACTION_UNSUPPORTED` | The action is outside the allowlist. | `false` / `fix_request` |
| `VOICE_TARGET_NOT_FOUND` | No such target is visible to the caller. | `false` / `fix_request` |
| `VOICE_TARGET_NOT_OWNED` | The target exists but belongs to another caller. The owner is not revealed. | `false` / `none` |
| `VOICE_ACTION_INVALID_STATE` | The target's state does not allow the action, for example `queue_continue` on a completed job. | `false` / `none` |

The error object is the shared shape: `code`, `message`, `retryable`
(boolean), `action` (`none`, `retry`, `authenticate`, `fix_request`,
`upload_again`) and `request_id`, echoed from the `X-Request-Id` middleware. In
a voice-action response `error.request_id` equals the top-level `request_id`;
the validator checks that echo. No voice denial is retryable without the caller
changing something, so every fixture uses `retryable: false`; the
`retryable`/`action` values above are proposals for #33 to confirm.

The code names are final only after review with the BE-10 (#33) owner. The
BE-10 server (`backend/services/api/routes/voice.py`) answers as follows:

- A well-formed `request_id` with an action name of 1 to 64 characters outside
  the allowlist is a 200 denial with `VOICE_ACTION_UNSUPPORTED`, echoing the
  target only when it is well-formed (the `unsupported-action` fixture).
- For an allowlisted action, a body that fails the request schema (missing or
  mismatched target, extra fields such as `confirmed`, a bad `request_id`) is
  422 `VALIDATION_FAILED` in the shared error shape, with no voice response and
  no audit row.
- A missing target and another caller's target are both
  `VOICE_TARGET_NOT_FOUND`, matching the 404 policy of every REST route. The
  server does not emit `VOICE_TARGET_NOT_OWNED`; it stays in the schema, so
  the `cross-owner-denied` fixture remains valid contract data but does not
  describe this server.
- `queue_retry` and `queue_continue` never change a job: the queue has no
  transition out of a terminal state. They are accepted for a queued, leased
  or running job and denied with `VOICE_ACTION_INVALID_STATE` for a published,
  failed or cancelled one.
- Repeating a `request_id` (per caller) replays the stored response; reusing it
  for a different action or target is 409 `IDEMPOTENCY_KEY_REUSED`.

### Provisional status and the client-local action

The allowlist is Conditional in BC-D04 and is fixed at AN-09 (#35). Until then
the version stays `0.x.y-draft`, and `validate.py` refuses a `1.x` version.

The Android client also has a sixth, client-local action, `switch_tab`
(advertised to the provider as `open_tab` with `{"tab":"space"|"explore"}`).
It is provisional, is the only action live today, and never reaches the
backend, so it is deliberately absent from the request `action` enum rather
than represented as a server action. A request carrying `switch_tab` or
`open_tab` fails validation (covered by tests). If the client-only action is
ever formalized, it belongs in a separate client-side document, not in this
server contract.

## Voice fixtures

Every file in `fixtures/voice-actions/` is one scenario:

```json
{
  "synthetic": true,
  "description": "...",
  "expect": { "request": "valid", "response": "valid", "error_code": "VOICE_TARGET_NOT_OWNED" },
  "request": { "...": "..." },
  "response": { "...": "..." }
}
```

`expect.request` and `expect.response` are `valid`, `invalid` or (responses
only) `unknown-enum`; each is present exactly when the payload is. Denied
responses declare `expect.error_code`. The validator enforces the expectation
in both directions: a positive fixture that fails or a negative fixture that
passes is an error, and responses must echo the request's `request_id`,
`action` and `target`.

| Fixture | Scenario |
| --- | --- |
| `open-check-accepted`, `save-report-accepted`, `queue-cancel-accepted`, `queue-retry-accepted`, `queue-continue-accepted` | One accepted request/response pair per allowed action. |
| `unsupported-action` | `delete_report`: the request fails validation; the recorded response shows `VOICE_ACTION_UNSUPPORTED` for a server that still answers in-contract. |
| `queue-continue-invalid-state` | `queue_continue` on a completed job: `VOICE_ACTION_INVALID_STATE`. |
| `cross-owner-denied` | `save_report` on another caller's report: `VOICE_TARGET_NOT_OWNED`. |
| `target-not-found` | `open_check` for a missing investigation: `VOICE_TARGET_NOT_FOUND`. |
| `missing-target` | Request without `target`; must fail validation, no response recorded. |
| `mismatched-target-kind` | `open_check` with a `job` target; must fail validation. |
| `free-form-argument` | `queue_cancel` with a remote `confirmed` flag; must fail validation. |
| `unknown-enum-response` | A newer server's `result`, `error.code` and `error.action` values; fails strict validation, passes with enums relaxed. |

All identifiers contain `synthetic` and nothing behind them exists. Fixtures are
contract evidence, not evidence that any endpoint or client behaves this way.

### Unknown enum values in the voice response

Enum-typed fields in the response are `result`, `target.kind`, `error.code`
and `error.action`. The same rule as above applies: strict validation rejects a
future value, the relaxed check proves the payload stays structurally valid and
the accepted/denied shape stays unambiguous, and the Android parser maps the
value to `UNKNOWN`, never to a success state. Requests are never relaxed.

Android tests should read the committed fixtures from this directory through
Gradle test resources (the `request` and `response` members of each file), not
copy them.

## OpenAPI document

`openapi.json` is an OpenAPI 3.1 document for the routes the backend serves
today (`/v1/principals/guest`, `/v1/uploads`, `/v1/uploads/{id}/content`,
`/v1/uploads/{id}/complete`, `/v1/investigations`,
`/v1/investigations/{id}`, `/v1/jobs/{job_id}/cancel`, `/v1/jobs/{job_id}`, the
capture routes, and from BE-10 (#33) `/v1/investigations/{id}/reports`,
`/v1/investigations/{id}/reports/{version}`,
`/v1/investigations/{id}/reports/{version}/export`,
`/v1/investigations/{id}/reanalyze`, `/v1/reports/{report_id}/save` (`POST`
to save and, from AN-10 (#36), `DELETE` to remove the caller's own save) and
`/v1/reports/saved`) plus `POST /v1/voice/actions`. Every request and
response schema is a `$ref` into `schemas/`, through named components
(`#/components/schemas/Investigation` is `schemas/investigation.schema.json`),
so the document cannot describe a shape the JSON Schemas and the fixtures do not
have. The small FastAPI-only bodies without a schema file
(`GuestPrincipalRequest`, `GuestPrincipalResponse`, `AccountLinkRequest`,
`AccountLinkResponse`, `InvestigationList` and, from BE-10, `ReportVersionList`,
`ReportVersionSummary`, `SavedReport`, `SavedReportList`, `ReanalysisRequest`,
`ReanalysisResponse` and `ReportExport`) are defined inline
and mirror `services/api/schemas.py`. The BE-10 bodies are inline so the
Android schema cross-check (`ContractEnumsTest`) needs no change until the
Android side models them; their nested report is
`schemas/report-version.schema.json`. `info.version` must equal
`VERSION`. Capture-session, capture-chunk, job, claim,
evidence and assessment schemas are published as components for the Android
models before their own endpoints exist (the pipeline tasks); the
spectral override list in `.spectral.yaml` names them and shrinks as paths are
added. The document is hand-maintained; the FastAPI-generated document at
`/openapi.json` is bootstrap output and is not the contract.

Rules enforced on the document, by `openapi_check.py` and by spectral:

| Rule | Check |
| --- | --- |
| Document validity | `openapi-spec-validator` on OpenAPI 3.1 with the relative file references resolved. |
| All errors use the error shape | Every 4xx/5xx response has an `application/json` body whose schema is `#/components/schemas/Error`, which is `schemas/error.schema.json`. Error codes per operation are listed in the response descriptions. |
| Every response echoes `X-Request-Id` | Every response (success and error) declares the header. |
| No untyped enums | Every `enum`, in the document and in every schema file, sits next to `"type": "string"` and lists nonempty strings. |
| Nothing unpublished | Every `schemas/*.schema.json` file is reachable from the document (the `$defs`-only `enums.schema.json` through the others). |

## Validation

From the repository root. The validator needs only Python 3.11+; the OpenAPI
check, the round trip and the tests use the backend uv project and its
`contracts` dependency group (`openapi-spec-validator`), so Pydantic, Ruff,
strict MyPy and pytest match the backend gates. Spectral needs Node 22.

```sh
python packages/contracts/validate.py
uv run --project backend --frozen --group contracts python packages/contracts/openapi_check.py
uv run --project backend --frozen python packages/contracts/roundtrip.py --check
uv run --project backend --frozen --group contracts pytest packages/contracts/tests backend/tests/test_contract_roundtrip.py -q
uv run --project backend --frozen ruff check packages/contracts
uv run --project backend --frozen ruff format --check packages/contracts
uv run --directory backend --frozen mypy --strict ../packages/contracts/validate.py ../packages/contracts/roundtrip.py ../packages/contracts/openapi_check.py ../packages/contracts/compat.py ../packages/contracts/tests
npx --yes @stoplight/spectral-cli@6.17.0 lint --fail-severity warn --ruleset packages/contracts/.spectral.yaml packages/contracts/openapi.json
```

`roundtrip.py --update` regenerates `fixtures/results` after a model change;
review the diff, then rerun `validate.py`. `backend/tests/test_contract_roundtrip.py`
runs the `--check` in the backend suite as well.

To run the breaking-change gate by hand against `main`, with a pinned
`oasdiff` binary (version and SHA-256 are in `.github/workflows/contracts.yml`):

```sh
git fetch origin main
rm -rf /tmp/base && mkdir -p /tmp/base && git archive origin/main packages/contracts | tar -x -C /tmp/base
uv run --project backend --frozen python packages/contracts/compat.py --base /tmp/base/packages/contracts --oasdiff /path/to/oasdiff
```

### Contract checks (CI)

`.github/workflows/contracts.yml` is the required **Contract checks** workflow
named in WORKFLOW.md. It runs on PRs to any branch, `main` pushes and manual
runs, with no path filter, and reports one result from two jobs that must both
pass against the same checkout:

| Job | Steps |
| --- | --- |
| Validate contract (server) | Ruff, format and strict MyPy on the contract Python; `validate.py`; `openapi_check.py`; `roundtrip.py --check`; the contract pytest suites; spectral (`@stoplight/spectral-cli@6.17.0`, `--fail-severity warn`); pinned, checksum-verified `oasdiff 1.33.0` via `compat.py` against the PR base commit (or the previous commit on `main`). |
| Validate contract (Android) | JDK 21, SDK and Gradle setup as in Android CI, then `:app:testDebugUnitTest --tests 'app.ovrly.contract.*'`, which reads `VERSION`, `schemas/` and `fixtures/` in place through Gradle test resources. |

A breaking change is a removed path or operation, a removed request enum value,
a response property removed or made optional, a type change (oasdiff), a
removed enum `$def` or enum value in `enums.schema.json`, or a removed schema
file (`compat.py`). Any of them fails the job unless `VERSION` differs from the
base. A base without `openapi.json` reports "no baseline" and passes, so the
document can land. **Backend CI** still runs `validate.py` and the round trip
as a cheap early signal; the full gate is this workflow. A maintainer adds
**Contract checks** to the `main` ruleset after the first green run.

The validator supports only the schema subset used here: `$ref` (local and
cross-file, including `#/properties/...` paths), `$defs`, `type`, `const`,
`enum`, `required`, `properties`, boolean `additionalProperties`, `items`,
`oneOf`, `allOf`, `not`, `minItems`, `uniqueItems`, `minLength`, `maxLength`,
`pattern`, numeric `minimum`/
`maximum`, and the annotation-only `default` (never injected into payloads).
Capture create/close duration bounds are machine-readable and enforced by the
validator as well as the server. Unsupported keywords (including `prefixItems`,
`if`/`then`) still fail closed. Patterns use the
portable subset shared with `evaluation/` (`(?![\s\S])` for strict end of
input). Adding a feature requires implementing it and a negative test.

## Versioning

### BE-06 capture endpoint additions

`POST /v1/captures`, `PUT /v1/captures/{capture_id}/chunks/{seq}`,
`POST /v1/captures/{capture_id}/close` and `GET /v1/captures/{capture_id}`
are served. The existing session/chunk shapes are preserved; new
`capture-create-request`, `capture-close-request`, `capture-metadata` and
`capture-status` schemas specify the transport and polling envelope.
`CaptureMetadata` wraps the unchanged chunk declaration plus its client-declared
modality. Send it as a JSON **text** multipart part named `metadata`, alongside
one file part named `content`. Multipart file names never become storage keys.

Capture investigations add the `capture` read-source branch and `capture_id`;
ordinary `POST /v1/investigations` still accepts only URL/upload sources.
Create uses an owner-scoped idempotency key. Close's continuation boolean is
required; final duration is optional, but should be supplied to expose missing
tails. New chunks after close are rejected while identical stored retries
preserve the receipt time and return current gaps. See the
[backend behavior and limits](../../backend/README.md#incremental-capture-api).

These are draft implementation choices pending Android/backend review, not
retroactive product-owner decisions. The new shared intake fixtures cover the
metadata wrapper and waiting status with a missing tail. Android mirrors the
schemas and parses those fixtures without networking. A validation-stage result
is not a research result: `claims: []` plus `claim_extraction_status: not_started`
does not mean no claims were found. Per-claim pipeline population, live-provider
verification and Android completed-upload text sending remain separate work.

`claim_extraction_status` uses the shared `coverage_status` enum. The backend
currently emits `not_started`; Android accepts `partial`/`complete` and maps
future values to `UNKNOWN` without discarding the rest of the poll response.

### Compatibility policy

- The contract version lives in `VERSION`, in `openapi.json` `info.version` and
  in each schema's `$comment`. BE-06 bumps it to `0.2.0-draft`: the new capture
  source expands the investigation response union, which `oasdiff` classifies
  as breaking for clients that only understand URL/upload sources. Android's
  `ContractJson.CONTRACT_VERSION` and `VoiceActionCodec.CONTRACT_VERSION`
  must equal `VERSION`. Voice payloads and existing session/chunk shapes are unchanged.
- A breaking change (removing or renaming a field, narrowing a type or an
  enum, adding a required field, changing an error code name) requires a
  version bump in the same PR. Adding an optional field or a new enum value is
  additive, but clients must already tolerate unknown enum values.
- `compat.py` with pinned `oasdiff` fails a breaking change without a bump in
  **Contract checks**; reviewers still read the diff, because a tool cannot
  judge whether a renamed enum value was intended.
- Fixtures change together with the schema they exercise; `roundtrip.py --check`
  fails when a model and its result fixture disagree.
- The error shape in `error.schema.json` is the one agreed in BE-05 (#19);
  changing it is a breaking change for every endpoint and needs the backend and
  this package to move together.

## Review

Contract PRs need one Android reviewer and one backend reviewer (WORKFLOW.md
section 5); `.github/CODEOWNERS` routes `/packages/contracts/` to the owner
(Android reviewer today) and the backend reviewer. Record the reviewed
schema/fixture link in #62, #18 and #33.

## Not in this package

- Real assessment content: the pipeline tasks (#27). Reanalysis and the export payload (BE-10) are published as the
  inline `ReanalysisRequest`, `ReanalysisResponse` and `ReportExport` components.
  When they land, their paths join `openapi.json` and the matching spectral
  override entries are removed.
- Android models for the new schemas: #62 part 2, from the handoff above. Until
  it merges, the Android job of Contract checks runs the voice-slice tests only.
