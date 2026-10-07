# BC-D06: demo retention windows and admission budgets

**Decision ID and question:** BC-D06. How long should the current demo backend
retain a workspace and its execution receipts? The admission and provider budget
portion (#22) is recorded in its own section below.

**Status:** Retention portion: Proposed. Implementation does not establish
approval. Budget portion: Accepted in part on 7 October 2026, see
[Admission and provider budgets](#admission-and-provider-budgets-22).

**Owner and participants:** Product owner @natnael-solomon must accept or revise.
Backend implementation: @Nattyy-1. No acceptance of the retention portion is
recorded.

**Options considered:** Indefinite retention; rolling per-object/inactivity
windows; an explicit, fixed-lifetime demo workspace.

**Evidence and uncertainties:** #77 requires bounded, configurable deletion
across current stores. The current backend has guest identities, Google-linked
accounts (BC-D07), uploads,
investigations, idempotency responses and jobs, but no permanent saved-report
storage yet (BE-10, #33). A durable account policy, provider terms, infrastructure backup
retention and approved hosting locations remain unresolved.

**Chosen option and rationale:** Proposed only: expire a whole **guest**
workspace 86400 seconds (24 hours) after principal creation, including its
credentials and all dependent content. Principals linked to a Google account
under [BC-D07](BC-D07-account-link.md) (`kind = account`) are excluded from this
sweep: they carry the saved-report recovery promise (AC08), so the account row,
its credentials and its objects are never expired by the demo policy, however
old the original guest row is. A guest merged into an account on a second
device remains a guest and still expires with its revoked credential. Remove
unused pending uploads at target expiry.
Apply the same content age limit to legacy ownerless jobs. Retain detached
tombstones and terminal retention-job metadata for 604800 seconds (seven days),
then physically delete them. Run every 60 seconds with batches of 100 subjects.
Automatic retention remains disabled by default until explicitly enabled for
approved demo data. These numbers are proposals, not statutory deadlines.

**User-visible consequences:** Enabling this policy can remove recently
uploaded content in an older guest workspace. Activity does not extend its
lifetime. After expiry, old credentials fail, responses cannot be replayed
indefinitely, and users need a new guest identity or must link an account
before the lifetime ends. Linked accounts keep their data; a retention
policy for accounts and saved reports needs a revised decision.

**Technical, privacy, cost and evaluation consequences:** One locked deletion
path per store, with shared `JobQueue.delete` fencing; fewer duplicate
implementations and no paid infrastructure. Expiry makes work eligible for
cleanup, not an absolute timing SLA during outages/backlogs. Filesystem removal
can precede a rolled-back database transaction and is retried idempotently.
Physical disk/WAL/backup erasure and remote-provider deletion are not proven.
Proposed host log retention is seven days and must be configured by the operator.

**Dependencies / capability gates:** Owner acceptance, matching API/worker
settings and storage mounts, and the operational transfer
checklist. #81 (owner-scoped job actions) and #80 (account linking) are on
`main`; the sweep reuses `JobQueue.delete` and honours `principals.kind`.
The quota, token bucket and global intake stop decisions are in the budget
section below, with their own status.

**Rejected alternatives and why:** Indefinite retention is unsuitable for a
privacy-oriented demo. Per-object rolling windows require product decisions
about shared uploads, retained history and saved reports that are not yet made.

**What evidence would reverse this decision:** A requirement for persistent
accounts/reports, accepted different retention promises, legal review,
measured demo needs, or provider deletion/backup constraints.

**Linked contract, test, source and ideation record:** #77; #22; #75; RFC section
11; `backend/tests/recovery/test_privacy.py`;
[data map](../operations/data-map.md);
[PDP checklist](../operations/pdp-checklist.md).

## Admission and provider budgets (#22)

**Status: Accepted in part on 7 October 2026.** The product owner
@natnael-solomon directed on 7 October 2026, while taking over the remaining
#22 work, that a number is Accepted only when a cited upstream source backs
it, and that every other number stays an explicit assumption until the owner
confirms it on the provider or host dashboard. The table below applies that
rule. Enforcement stays opt-in per deployment with `OVRLY_QUOTAS_ENABLED=1`;
acceptance does not enable it. The #77 retention portion above is unchanged
and still Proposed.

### Upstream sources (read 7 October 2026)

| Ref | Source | Limit used |
| --- | --- | --- |
| S1 | [Scholarxiv Rate Limits & Quotas](https://scholarxiv.com/developers/docs/papers-api/limits.md) | Free plan: 1,200 requests per rolling hour, shared by every key on the account; a 429 carries `Retry-After` |
| S2 | [Scholarxiv Router API](https://scholarxiv.com/developers/docs/router-api.md) | Routing decisions count against the same regular API rate limit; no separate router quota |
| S3 | [Groq rate limits](https://console.groq.com/docs/rate-limits), Free plan table | `whisper-large-v3` and `whisper-large-v3-turbo`: 20 RPM, 2,000 RPD, 7,200 audio-seconds/hour, 28,800 audio-seconds/day. `openai/gpt-oss-20b`: 30 RPM, 1,000 RPD, 8,000 TPM, 200,000 TPD. Limits are per organization |
| S4 | [Groq speech-to-text](https://console.groq.com/docs/speech-to-text) | 25 MB per file on the free tier; a request is billed for at least 10 seconds |
| S5 | [BC-D04](BC-D04-voxide-route.md) | 100 Voxide sessions, client-managed, dashboard is the only ledger |
| S6 | [BC-D03](BC-D03-provider-hosting.md) | EthioDeploy Free: web disk allowance and body limit unanswered |

These figures match the dated assumptions in [0004](0004-asr-ocr-benchmarks.md)
(7,200 audio-seconds per hour, 2,000 requests per day, 10 s minimum) and the
8,000 TPM rejection observed in BC-D03. Public documentation is not the
account's dashboard: the Groq page says exact limits are on the organization's
Limits page, and nothing here reads a live balance.

### Worst-case upstream cost of one check under the current caps

| Provider | One check at most | Derived from |
| --- | --- | --- |
| Groq speech, upload | 1 request, 600 audio-seconds | `OVRLY_MAX_SHARED_DURATION_SECONDS=600`; a 16 kHz mono WAV of 600 s is 19.2 MB, under S4's 25 MB, so one request |
| Groq speech, capture | 18 requests, 180 audio-seconds | 3-minute capture in 10 s chunks (0004); each chunk is at the S4 10 s minimum |
| Scholarxiv | 79 requests | Extraction and reconciliation share `OVRLY_EXTRACTION_BUDGET_REQUESTS=24`; evidence uses up to 4 router calls, 3 searches and 4 feedback calls per claim, 55 for 5 claims (S2 counts router calls) |
| Groq extraction fallback | inside the same 24 requests | Fallback only after Scholarxiv availability failure (BC-D03) |

Provider retries after a 429 also spend local units; these figures are before
retries.

### Decided numbers

| Limit | Value | Status | Derivation |
| --- | --- | --- | --- |
| Daily checks per principal (UTC day) | 6 | **Accepted** (S1, S3) | 6 uploads x 600 s = 3,600 audio-seconds: 50% of the hourly and 12.5% of the daily Groq speech limit. 6 captures x 18 = 108 requests: 5.4% of 2,000 RPD. 6 x 79 = 474 Scholarxiv requests: under half of one hour's application budget. One principal at the maximum cannot exhaust the team's day; speech per day is the binding limit (28,800 / 3,600 = 8 principals at the maximum). |
| Active checks per principal | 2 | **Accepted** (S3) | Two concurrent captures send one 10 s chunk every 10 s each: 12 requests/minute against Groq's 20 RPM. Three would reach 18, the configured ceiling, leaving nothing for uploads or retries. Two concurrent uploads hold 1,200 audio-seconds, a sixth of the hourly limit. |
| Claims researched per run | 5 | **Accepted** (S1, S2) | 79 Scholarxiv requests per check against 950 usable requests per hour (1,000 application budget less the 50 reserve) allows 12 complete checks per hour across all users. Ten claims would cost 134 and allow 7. Unreached claims stay visible and unassessed. |
| Scholarxiv application budget | 1,000 requests/hour | Ceiling **Accepted** (S1); headroom is an **assumption** | The 200-request headroom for other keys on the shared account (experiments, S1 "shared per user") is not measured. Confirm on the Scholarxiv Usage page. |
| Groq speech settings (operator-entered) | 18 RPM, 1,800 RPD, 6,480 audio-seconds/hour, 25,920 audio-seconds/day, 10 s minimum | Ceilings and minimum **Accepted** (S3, S4); 10% headroom is an **assumption** | Recommended values for the required `OVRLY_ASR_*` limits. They are 90% of the Free limits for other users of the organization. `OVRLY_ASR_LIMITS_VERIFIED_ON` still requires a dated dashboard check before activation. |
| Groq extraction fallback buckets | 27 RPM, 900 RPD, 7,200 TPM, 180,000 TPD | Ceilings **Accepted** (S3); 10% headroom is an **assumption** | Defaults of the new `OVRLY_GROQ_LLM_*` settings. Tokens are the conservative local approximation (escaped request bytes + output cap + 256), not Groq's tokenizer; a request larger than a bucket waits for the full bucket. |
| Daily upload bytes per principal | 256 MiB | **Assumption**, pending the owner's EthioDeploy dashboard | No upstream limit governs local storage. S4's 25 MB is per Groq request, not per day, and the web disk allowance is unanswered (S6). The value equals one maximum upload. |
| Concurrent Scholarxiv requests | 2 | **Assumption**, pending owner confirmation | No provider concurrency limit is documented (S1 states only an hourly limit). The value bounds the 0.5 CPU host and holds no database connection during calls. |
| Intake reserve | 50 Scholarxiv units; 5% of each Groq limit | **Assumption**, a policy threshold | Not an upstream number. Configurable with `OVRLY_QUOTA_PROVIDER_RESERVE` and `OVRLY_QUOTA_PROVIDER_RESERVE_FRACTION`. |
| Voxide sessions | 100 (BC-D04) | Accepted in BC-D04, not enforced by the backend | Sessions are client-managed; the summary reports the balance as unknown and points at the dashboard. |

Per-principal limits are not per-human anti-abuse protection: creating another
guest principal obtains another allowance. The provider buckets and the speech
ledger are account-wide, so they still bound the team's total spend.

### Enforcement

Counters are charged in the same transaction as admission, after idempotent
replay checks. Cancel, delete and failed processing do not refund daily checks.
Failed admission transactions do not spend budget. A new reanalysis counts as a
check, including a correction, but it does not count as a second active
investigation when that investigation already has active work. The counters
start when enforcement is enabled; historical usage is not backfilled. Expired
open captures without active jobs no longer count; a closed capture can still
hold an active slot while its queued research is unfinished. Payloads not yet
recognized by the quota code count conservatively as separate active jobs.

Every provider limit is shared across processes in PostgreSQL and checked
before the expensive call:

| Provider | Mechanism | Where it is taken |
| --- | --- | --- |
| Scholarxiv | `provider_buckets` token bucket `scholarxiv`, 2 request slots | Evidence Papers and Router calls (existing); with quotas on, also every extraction and reconciliation routing, completion and feedback request before it is recorded |
| Groq speech | `asr_requests` rolling minute/day request and hour/day audio windows (BE-07) | Before every speech request, unchanged; refusal is `ASR_QUOTA_EXHAUSTED` |
| Groq extraction fallback | `provider_buckets` `groq_llm:*` minute/day request and token buckets, charged all or nothing | With quotas on, before every fallback request is recorded |

With quotas on, a Scholarxiv or Groq 429 seen by extraction or reconciliation
holds that provider's shared buckets for its `Retry-After`, as the evidence stages already
do. A request refused afterwards by the per-input extraction budget or by
cancellation is refunded because it was never sent. Admission refusals are HTTP
429 `QUOTA_EXCEEDED`; a paused provider is HTTP 429 `PROVIDER_QUOTA_EXHAUSTED`.

### Global stop

New investigations, captures, upload targets and reanalyses pause while any
configured provider is near exhaustion: the Scholarxiv bucket below its
reserve, any Groq speech window within 5% of its limit or under a recorded
`Retry-After`, or any Groq fallback bucket below 5%. The `Retry-After` header
is the longest time until every paused provider is back at its reserve: bucket
refill, the age-out of the oldest speech reservations, or a provider hold. It
is an estimate, since accepted work keeps spending. Already accepted capture
chunks and uploads can finish, and reads, Stop/cancel and saves remain
available. No status endpoint exists for the Android app, so the visible status
is the typed 429 with its message and `Retry-After`; no contract changed.

All API and worker processes must use the same quota flag and limits; mixed
enabled/disabled workers cannot enforce a shared policy. No provider
entitlement, account-wide cost ceiling or paid overage protection is inferred
from these local counters.

### Demo-day summary

`python -m services.quota_summary` (JSON) or `--text` prints, per provider, the
local remaining units of every limit, the reserve, whether it pauses intake and
the time to recover. Upstream balances are never read: Scholarxiv and Groq show
`unknown (local estimate: ...)`, and Voxide shows `unknown (local estimate:
none; ...)` with a pointer to its dashboard. It needs database credentials, so
it is operator-only; no public endpoint is added.

**Still pending:** owner confirmation of the assumptions above on the
Scholarxiv, Groq and EthioDeploy dashboards; observed upstream balances; Voxide
session accounting stays client-side under BC-D04.
