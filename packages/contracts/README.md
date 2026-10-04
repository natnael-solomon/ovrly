# Contracts

**Version `0.1.0-draft` (pre-1.0).** Shared JSON Schema definitions and synthetic
fixtures that the Android client and the backend build from. This package was
started by BE-13 ([#67](https://github.com/natnael-solomon/ovrly/issues/67)) with
the voice-actions slice. BE-03 ([#15](https://github.com/natnael-solomon/ovrly/issues/15))
owns the package as a whole: the upload, investigation, job, capture, report,
claim, evidence and assessment schemas, the shared enums, the six result
fixtures, the OpenAPI document, the server round-trip check and the required
**Contract checks** workflow (schema validation, spectral, oasdiff, server
round trip and the Android compatibility tests against the same fixtures).
Nothing here implements an endpoint or Android wiring.

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
| `validate.py` | Standard-library validator for the schemas and all three fixture directories. |
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
| `job.schema.json` | `JobSummary` (client-visible columns of `jobs`) | Nested in an investigation; voice `job` targets. |
| `report-version.schema.json` | `ReportVersion` | Nested in an investigation; `GET .../reports/{version}` in #33. |
| `claim.schema.json` | `Claim`, `ClaimCorrection` | Items of `report.claims`. |
| `evidence.schema.json` | `Evidence`, `EvidenceSource` | Items of `report.evidence`. |
| `assessment.schema.json` | `Assessment`, `EvidenceRelation` | Items of `report.assessments`. |
| `capture-session.schema.json` | Hand-authored; no backend model yet | Live-overlay session read model. |
| `capture-chunk-request.schema.json`, `capture-chunk.schema.json` | Hand-authored; no backend model yet | Chunk declaration and acknowledgement. |
| `voice-action-request.schema.json`, `voice-action-response.schema.json` | BE-13 slice, unchanged | `POST /v1/voice/actions`. |

"Mirrors" names the Pydantic class in `backend/services/api/schemas.py`. The
request models and `InvestigationResponse`, `UploadResponse` existed before this
revision and are unchanged; `Coverage`, `Interval`, `Claim`, `ClaimCorrection`,
`EvidenceSource`, `Evidence`, `EvidenceRelation`, `Assessment`, `ReportVersion`,
`JobSummary` and `InvestigationReadModel` are new read models that no route emits
yet. `GET /v1/investigations/{id}` still returns the nine-field
`InvestigationResponse`; adopting `InvestigationReadModel` there (adding
`processing_status`, `job` and `report`) is #19 part 2 / #33 work, which is why
those three fields are required in the schema rather than optional.

### Investigation read model

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

`error` is the stored subset of the shared shape: `code`, `message` and
`retryable`, by `$ref` into `error.schema.json`. `request_id` is absent because a
stored failure is not tied to the request that reads it and `action` is not
stored today (`services/api/errors.py safe_error`).

Capture sessions (`capture-session`, `capture-chunk-request`, `capture-chunk`)
state the duplicate and out-of-order rules as data: `(session_id, seq)` is the
idempotency key, a repeat with the same bytes replays the acknowledgement with
`disposition: duplicate`, a chunk above a missing `seq` is stored with
`disposition: out_of_order` and the missing range appears in `gaps` until it
arrives. Intervals on these schemas must use the `capture` timebase; claims from
a shared video use `media`.

## Enums

Each value list is one `$def` in `enums.schema.json` so #62 maps it to one Kotlin
enum with an `UNKNOWN` fallback. The source column says where the list comes
from; `tests/test_results.py` diffs `job_state` and `retry_class` against the
backend enums and every other list against the `Literal` alias of the same name
in `backend/services/api/schemas.py`.

| Enum | Values | Source |
| --- | --- | --- |
| `job_state` | `queued`, `leased`, `running`, `published`, `cancelled`, `deleted`, `failed` | `services/jobs/states.py JobState` |
| `retry_class` | `transient`, `rate_limited`, `non_retriable_input`, `invalid_model_schema`, `unknown_outcome` | `services/jobs/retries.py RetryClass` |
| `investigation_state` | `queued`, `running`, `completed`, `failed`, `cancelled` | `schemas.py InvestigationState` (pre-existing) |
| `stage` | `intake`, `media_validation`, `asr`, `device_text`, `claim_extraction`, `retrieval`, `assessment`, `reconciliation`, `publication` | Build contract section 4; only `intake` exists in code today (`routes/investigations.py INITIAL_STAGE`), the other eight are introduced here |
| `processing_status` | `waiting`, `checking`, `partial`, `complete`, `failed`, `cancelled` | Build contract section 3 |
| `coverage_status` | `not_started`, `partial`, `complete` | `routes/investigations.py COVERAGE_PLACEHOLDER` plus the two pipeline values |
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

The code names are final only after review with the BE-10 (#33) owner. Request
bodies that fail schema validation (missing target, unknown action, extra
fields) are rejected by the framework before a handler runs and do not produce
a voice-action response; #33 decides the HTTP status and whether that generic
validation error also uses the shared error shape.

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
`/v1/investigations/{id}`) plus `POST /v1/voice/actions`. Every request and
response schema is a `$ref` into `schemas/`, through named components
(`#/components/schemas/Investigation` is `schemas/investigation.schema.json`),
so the document cannot describe a shape the JSON Schemas and the fixtures do not
have. The two small FastAPI-only bodies without a schema file
(`GuestPrincipalRequest`, `GuestPrincipalResponse`, `InvestigationList`) are
defined inline and mirror `services/api/schemas.py`. `info.version` must equal
`VERSION`. Capture-session, capture-chunk, job, report-version, claim,
evidence and assessment schemas are published as components for the Android
models before their own endpoints exist (#33 and the pipeline tasks); the
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
`oneOf`, `allOf`, `not`, `minLength`, `maxLength` and `pattern`. Unsupported
keywords (including `minimum`, `prefixItems`, `if`/`then`) fail closed, which is
why numeric lower bounds such as a positive `size_bytes` are documented and
enforced by the server rather than expressed in the schema. Patterns use the
portable subset shared with `evaluation/` (`(?![\s\S])` for strict end of
input). Adding a feature requires implementing it and a negative test.

## Versioning

- The contract version lives in `VERSION`, in `openapi.json` `info.version` and
  in each schema's `$comment`. It is still `0.1.0-draft`: this revision only
  adds schemas, fixtures and enum lists and changes nothing the voice slice or
  the Android parser already depend on. `VoiceActionCodec.CONTRACT_VERSION`
  must equal `VERSION`.
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

- Adoption of `InvestigationReadModel` by the live routes, `GET
  .../reports/{version}`, reanalysis, and the capture endpoints behind
  `capture-session` and `capture-chunk`: #19 part 2, #33 and the pipeline tasks.
  When they land, their paths join `openapi.json` and the matching spectral
  override entries are removed.
- Android models for the new schemas: #62 part 2, from the handoff above. Until
  it merges, the Android job of Contract checks runs the voice-slice tests only.
