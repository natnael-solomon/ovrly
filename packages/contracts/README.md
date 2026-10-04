# Contracts

**Version `0.1.0-draft` (pre-1.0).** Shared JSON Schema definitions and synthetic
fixtures that the Android client and the backend build from. This package was
started by BE-13 ([#67](https://github.com/natnael-solomon/ovrly/issues/67)) with
the voice-actions slice only. BE-03 ([#15](https://github.com/natnael-solomon/ovrly/issues/15))
owns the package as a whole and will add uploads, investigations, capture
sessions, reports, claims, evidence, assessments, OpenAPI and the Contract
checks CI gate. BE-04 part 3 (#75) adds job action schemas and receipts, exercised
by the backend endpoints. The schemas alone do not implement Android wiring.

## Layout and namespaces

| Path | Content |
| --- | --- |
| `VERSION` | Contract version. Stays `0.x.y-draft` until the allowlist is fixed at AN-09 (#35). |
| `schemas/error.schema.json` | Contract-wide error shape from BE-05 (#19): `code`, `message`, `retryable`, `action`, `request_id`. |
| `schemas/voice-action-request.schema.json` | Request body for `POST /v1/voice/actions`. |
| `schemas/voice-action-response.schema.json` | Response body for `POST /v1/voice/actions`. |
| `fixtures/voice-actions/*.json` | Synthetic request/response scenarios with explicit expectations. |
| `schemas/job-*.schema.json`, `fixtures/jobs/*.json` | Job action path parameters, cancellation/deletion receipts and safe error examples. |
| `validate.py` | Standard-library validator for the schemas and fixtures. |
| `tests/` | pytest suite, run from the backend uv project. |
| `ruff.toml` | Extends the backend Ruff configuration so the Python here meets the same gates. |

Schema files are named `<resource>.schema.json`, draft 2020-12, and reference each
other by relative file name (`error.schema.json#/$defs/...`). Fixtures live in one
directory per endpoint (`fixtures/voice-actions/`). Future resources from #15
follow the same layout. The error shape is the one fixed in BE-05 (#19), so
there is exactly one error shape contract-wide; #15
inherits it unchanged, and every error any endpoint returns must use it (the
planned spectral rule "all errors use the error shape").

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
status/outcome and ID consistency. Backend recovery tests compare these
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
  or `-`. This is the current Android client syntax; it is reconciled with the
  other #15 schemas before AN-09 wires the client. Valid syntax proves neither
  existence nor ownership.
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

## Fixtures

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

### Unknown enum values (the #62 pattern)

Enum-typed fields in the response are `result`, `target.kind`, `error.code`
and `error.action`. The server never emits a value outside this version's enums, and strict
validation rejects one. The Android parser (#62) must still map an unknown
string to `UNKNOWN`, never to a success state, and keep reading the rest of the
payload. `validate.py` has a `relax_enums` mode that checks exactly that: with
`enum`/`const` ignored the payload is still structurally valid and the
accepted/denied shape stays unambiguous. Requests are never relaxed; the server
validates them strictly.

Android tests should read the committed fixtures from this directory through
Gradle test resources (the `request` and `response` members of each file), not
copy them.

## Validation

From the repository root. The validator needs only Python 3.11+; the tests use
the backend uv project so Ruff, strict MyPy and pytest match the backend gates.

```sh
python packages/contracts/validate.py
uv run --project backend --frozen pytest packages/contracts/tests -q
uv run --project backend --frozen ruff check packages/contracts
uv run --project backend --frozen ruff format --check packages/contracts
uv run --directory backend --frozen mypy --strict ../packages/contracts/validate.py ../packages/contracts/tests
```

**Backend CI** runs the same five commands in its validate job (any change under
`packages/` counts as a non-documentation change, so the job runs). The
separate required **Contract checks** workflow named in WORKFLOW.md is a #15
deliverable (`openapi-spec-validator`, spectral, server round-trip in `--check`
mode, Android compatibility tests); when it lands, this step can move there.

The validator supports only the schema subset used here: `$ref` (local and
cross-file), `$defs`, `type`, `const`, `enum`, `required`, `properties`,
boolean `additionalProperties`, `oneOf`, `allOf`, `not`, `minLength`,
`maxLength` and `pattern`. Unsupported keywords fail closed. Patterns use the
portable subset shared with `evaluation/` (`(?![\s\S])` for strict end of
input). Adding a feature requires implementing it and a negative test.

## Versioning

- The contract version lives in `VERSION` and in each schema's `$comment`.
- A breaking change (removing or renaming a field, narrowing a type or an
  enum, adding a required field, changing an error code name) requires a
  version bump in the same PR. Adding an optional field or a new enum value is
  additive, but clients must already tolerate unknown enum values.
- #15 adds pinned `oasdiff` against `main` to fail a breaking change without a
  bump. Until then reviewers check this by hand.
- Fixtures change together with the schema they exercise.

## Handoff to #15 and review

- Error shape: `error.schema.json` is the shape agreed in BE-05 (#19)
  (`code`, `message`, `retryable`, `action`, `request_id`, no
  extra fields). #15 inherits it; changing it is a breaking change for every
  endpoint and needs both the backend and this package to move together.
- Identifier syntax and the `target.kind` discriminator should be reconciled
  with the investigation, report and job schemas #15 introduces.
- CODEOWNERS already routes `/packages/contracts/` to the repository owner.
  #15's item adds one Android and one backend reviewer for `packages/contracts/**`;
  this package does not change CODEOWNERS.
- Contract changes need both Android and backend review (WORKFLOW.md). Record
  the reviewed schema/fixture link in #10, #33 and #62.
