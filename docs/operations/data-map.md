# Backend data map and deletion runbook

Scope: #77, current PostgreSQL tables and `LocalFilesystemStore`; synthetic or
consented demo material only. Owner: backend maintainer; policy approval:
product owner. This is an inventory, not a claim of legal compliance.

## Proposed windows

[BC-D06](../decisions/BC-D06-retention.md) is **Proposed**. Automatic retention is
**off** unless `OVRLY_RETENTION_ENABLED=1`. Enabling it selects a destructive
demo policy: an entire principal workspace expires 24 hours after the
principal's creation, including data created more recently. Activity does not
extend this window. This is not a rolling inactivity timeout or a permanent
account/saved-report policy.

`retention_data_seconds` (default 86400, bounds 60..2592000) controls that
workspace deadline and the age limit for legacy ownerless job content.
`retention_tombstone_seconds` (604800, 60..7776000) controls detached job
tombstones and terminal retention-job receipts. Retention runs at a proposed
60-second interval (bounds 1..3600) with up to 100 expired principals, pending
uploads, legacy jobs and old tombstones per sweep (bounds 1..1000 per category).
A principal's dependent rows are deleted together, not split across batches.

These are eligibility deadlines, not a hard deletion SLA: worker sleep,
backlog, row locks or storage/database outages delay execution. No enabled
worker means no cleanup. Monitor successful retention counts and queue
failures; health on an API-only process does not prove a remote worker is alive.

## Current storage inventory

Every listed table is in the configured PostgreSQL database. The backend
maintainer owns its deletion path. Tests enumerate the columns below so adding
a user-data column requires updating this map.

| Table / location | Columns / data | Purpose and owner | Retention and deletion |
| --- | --- | --- | --- |
| `principals` | `id`, `kind`, `created_at`, `google_sub`, `merged_into`, `merged_at` | Guest workspace identity, or a Google-linked account (BC-D07); principal. `google_sub` is the provider subject, a stable pseudonymous identifier; `merged_into`/`merged_at` record a second-device guest absorbed into an account. | Workspace deadline for `kind = guest` only: retention locks the principal, removes dependent content, then deletes it. `kind = account` rows are never expired by the demo sweep (see BC-D06); account deletion is undecided. |
| `credentials` | `id`, `principal_id`, `token_hash`, `created_at`, `revoked_at` | Authentication; principal. Hashes are still sensitive identifiers. Raw tokens are returned once, not stored. | Cascade on principal deletion, including revoked credentials. Requests with the old credential then return 401. |
| `uploads` | `id`, `owner_id`, `state`, `declared_size_bytes`, `declared_sha256`, `content_type`, `max_bytes`, `storage_key`, `expires_at`, `created_at`, `completed_at` | Declared/verified upload and byte locator; principal | Pending targets: eligible at `expires_at`; completed uploads: workspace deadline. Delete bytes while holding the row lock, then delete metadata. No byte deletion is reported complete if storage fails. |
| `OVRLY_STORAGE_DIR/<storage_key>` | Raw complete or partially streamed upload bytes | Media intake; same principal as upload row | Same deadline as upload metadata. `UploadStore.delete` is idempotent; API writes and retention use the same upload row lock so a late write cannot restore a deleted file. API and standalone worker must mount the **same directory**. |
| `investigations` | `id`, `owner_id`, `source_kind`, `source_url`, `upload_id`, `declared_duration_ms`, `state`, `stage`, `version`, `error_code`, `created_at`, `updated_at` | Source URL/media reference and processing state; principal | Workspace deadline; removed before uploads to satisfy the upload foreign key. URLs may contain identifiers or credentials and are never logged. |
| `idempotency_keys` | `owner_id`, `key`, `request_hash`, `investigation_id`, `response_status`, `response_body`, `created_at` | Replay response, including source references; principal | Cascades with investigation deletion. No original response body remains after workspace expiry. |
| `capture_sessions` | `id`, `owner_id`, `request_key`, `chunk_duration_ms`, `state`, `started_at`, `closed_at`, `expires_at`, `continue_research`, `duration_ms` | Live-capture manifest and creation/Stop replay; same ID and owner as its investigation | Guest workspace expiry. Cleanup locks each session before removing bytes and cascading rows with its investigation. Linked-account sessions follow the existing account exclusion. |
| `capture_chunks` | `session_id`, `seq`, `end_ms`, `size_bytes`, `sha256`, `content_type`, `modality`, `storage_key`, `received_at`, `job_id` | Durable byte reservation, client-declared modality, verified receipt and queue reference; session owner | Guest workspace expiry removes all bytes, including failed/partial reservations. Separately, pending reservations (`received_at` null) are removed after close or upload-window expiry, including for accounts. Published job tombstone purge nulls `job_id` without permitting reprocessing. |
| `OVRLY_STORAGE_DIR/<capture storage_key>` | Chunk bytes, including partial or fully written but uncommitted content | Capture intake; session owner | Reservation commits before writing, so a crash cannot leave an untracked persistent key. Session locks serialize writes with deletion. Failure retains metadata for retry; deletion is idempotent. Accepted account-owned bytes are not automatically expired by the guest policy. Multipart spools are request-local temporary files, closed on success/error/cancellation; host temporary-file cleanup after process death is operator-owned. |
| `report_versions` | `id`, `investigation_id`, `owner_id`, `version`, `change_summary`, `fixture`, `payload`, `created_at` | Immutable published report versions (BE-10): claims with their original wording, evidence and assessments in `payload`; same owner as the investigation. `fixture` marks a development stub built from the contract fixtures (`OVRLY_STUB_REPORTS`). | Cascades with investigation deletion, so guest workspace expiry removes them. A trigger rejects `UPDATE`; deletion is the only change. |
| `saved_reports` | `owner_id`, `report_id`, `investigation_id`, `version`, `report`, `saved_at` | Explicit save of one report version with a snapshot of its content (`report`); principal. No foreign key to the version, so a save moved to an account by a second-device link (BC-D07) survives the guest workspace expiry. | Cascades with principal deletion (guest workspace expiry). Account-owned saves are never expired by the demo sweep; this is the saved-report recovery promise and account deletion is undecided. |
| `reanalysis_requests` | `id`, `owner_id`, `investigation_id`, `idempotency_key`, `request_hash`, `reason`, `base_version`, `published_version`, `result_version`, `job_id`, `response`, `created_at` | Idempotency and audit record of each accepted reanalysis (correction, expansion, deeper); principal. The corrected wording itself lives in the published `report_versions` payload, not here or in the job payload. | Cascades with investigation and principal deletion (guest workspace expiry); `job_id` is nulled when the job tombstone is purged. |
| `voice_actions` | `owner_id`, `request_id`, `action`, `target_kind`, `target_id`, `result`, `error_code`, `response`, `created_at` | Audit and replay record of each voice action (BC-D04): the structured request, the typed outcome and the response sent; principal. No transcript or audio is received or stored. | Cascades with principal deletion (guest workspace expiry). Account-owned rows persist until account deletion is decided. |
| `jobs` | `id`, `owner_id`, `cancel_outcome`, `version`, `stage`, `input_hash`, `state`, `cancel_requested`, `fencing_token`, `generation`, `attempts`, `lease_owner`, `lease_expires_at`, `available_at`, `payload`, `failure`, `retry_class`, `retry_counts`, `provider_request_id`, `created_at`, `updated_at` | Durable execution and replay/fencing metadata; principal, or internal/legacy ownerless work | Workspace expiry uses the same `JobQueue.delete` as the API: tombstone, increment generation, clear lease/payload and remove result. Detach owner, replace input hash with job UUID, clear provider reference, failure and cancel receipt. Ownerless non-retention content expires by job creation age. Tombstones are physically removed after their metadata window. |
| `job_results` | `job_id`, `version`, `stage`, `input_hash`, `fencing_token`, `generation`, `result`, `published_at` | Published stage output; same owner as job | Removed atomically by `JobQueue.delete`; cascades when old tombstones or terminal retention jobs are purged. Retention results contain counts only. |
| Process memory | In-flight HTTP bytes, credentials and handler inputs/results | Request/worker lifetime | Released with request/task/process lifetime, not secure memory wiping. Fencing rejects a result retained by a worker after deletion. It does not promise remote provider cancellation. |
| Application logs (stderr) | Fixed event messages/codes, hashed request IDs, job UUIDs and counts | Operational correlation; operator | No application file sink. Shared logging strips messages outside the event allowlist, dynamic text, exceptions/tracebacks, access URLs and extra fields before configured sinks. Operator must set host log expiry; proposed seven days. Do not add unfiltered handlers after startup. |
| PostgreSQL volume, WAL, host snapshots/backups | Physical copies of database rows | Infrastructure operator | SQL deletion removes live rows, not guaranteed physical overwrite/WAL erasure. No backup service is implemented. Before deployment, record provider retention, access and expiry; restore only into an isolated environment and run approved retention before exposing it. |
| Provider copies, object storage, pipeline artifacts | Not implemented in this checkout | Future integration owner | No erasure claim. Each integration must supply a real deletion path, retention/region evidence and failure tests before adding personal data. Synthetic harness artifact stores are not production storage. |

Alembic's version table is schema metadata, not user data. Git and CI artifacts
must contain synthetic fixtures and test reports only: no database dumps,
tokens, user media or private references.

## Execution and failure recovery

Both worker entry points schedule the reserved `privacy_retention` stage on the
existing job engine when enabled. A database-clock time slot forms its unique
stage key; multiple workers cannot create duplicate jobs for that slot. A
restarted worker schedules the current slot and processes existing durable
jobs, rather than replaying every missed wall-clock tick.

Each expired workspace is one database transaction, with byte deletion inside
the upload locks. Storage or database errors propagate to the worker's existing
infrastructure-error recovery (release/re-lease); they are not successful
cleanup. If bytes were removed and the database commit then failed, retry sees
missing bytes as already deleted and finishes the recorded metadata cleanup.
There is no filesystem/PostgreSQL distributed transaction and no byte restore.

Overlapping sweeps serialize on principal/upload/job locks. Pending uploads
locked by active streaming are skipped and reconsidered in a later sweep.
Job publication still requires the original job ID, fencing token and
generation. A detached tombstone or a physically removed row rejects a late
callback; it never recreates the result.

The API DELETE receipt still claims **job payload/result only**, not workspace
deletion. Receipts and idempotency guarantees are bounded by retention: after
principal expiry the credential no longer authenticates, and after tombstone
purge the old job ID is missing. Reprocessing after expiry is new work, never a
restoration of the old job ID.

Before enabling, confirm the policy, take an inventory of the affected demo
principals, verify a shared storage mount, and run synthetic cleanup/retry
tests. Do not enable this policy for permanent accounts or saved reports.
Reconcile #80's identity/dispatch changes first; saved reports (#33) of a linked account are excluded with the account, while a guest's saves expire with the guest;
new tables/columns/storage require an explicit map and cleanup update.

See [PDP transfer checklist](pdp-checklist.md) for separate operational gates.
