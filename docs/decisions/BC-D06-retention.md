# BC-D06: proposed demo retention windows

**Decision ID and question:** BC-D06 (retention portion only). How long should
the current demo backend retain a workspace and its execution receipts?

**Status:** Proposed. Implementation does not establish approval.

**Owner and participants:** Product owner @natnael-solomon must accept or revise.
Backend implementation: @Nattyy-1. No acceptance is recorded.

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
No quotas, token buckets or global intake stop are approved here.
The opt-in proposal below remains subject to owner acceptance under #22.

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

## Proposed admission and provider budgets (#22)

**Status: Proposed, not accepted.** The implementation is opt-in with
`OVRLY_QUOTAS_ENABLED=1`; it does not change a deployment or enable retention.
The existing #77 privacy implementation is retained. Product-owner acceptance,
actual account entitlements and the missing provider-stage integrations remain
requirements for closing #22.

| Proposed limit | Rationale and boundary |
| --- | --- |
| 2 active checks per principal | Counts distinct investigations with queued/leased/running work, plus unexpired open captures. Chunk fan-out remains one check. Two checks allow a comparison without an unbounded queue on the small host. It is an admission limit, not a worker-job or account-wide concurrency guarantee. |
| 6 new checks/reanalyses per UTC day per principal | At the existing 10-minute shared-input cap, six inputs represent at most 60 declared audio-minutes before retries, below the issue's reported 120 audio-minutes/hour. This is not ASR enforcement: missing durations, retries, other principals and the unverified Groq entitlement prevent that claim. |
| 256 MiB of upload reservations per UTC day per principal | Reuses the existing per-input byte budget as a daily aggregate instead of multiplying local storage by an unlimited number of uploads. Ordinary upload declarations and new capture-chunk reservations share it. Failed/abandoned accepted reservations are not refunded; retransmitting the same capture reservation costs no additional bytes. |
| 5 claims researched per run | With the current standard evidence budget, up to 4 router calls, 3 searches and up to 4 feedback calls per claim gives a planning envelope of 330 requests for six five-claim runs, before provider retries, claim extraction and expansions. Deeper searches increase this; actual outbound attempts still pass through the shared bucket. Unreached claims remain visible and unassessed. |
| 2 concurrent Scholarxiv requests | Bounds simultaneous Papers, Router and feedback requests across workers without holding database connections during network calls. Each request has the configured hard evidence-provider timeout and a slot lease lasting five seconds longer; the slot is fenced by a unique lease id. |
| Pause new intake below 50 local Scholarxiv tokens | A 5% reserve under the existing 1000/hour application budget leaves room for already accepted work. `Retry-After` estimates recovery to the reserve, not a guaranteed start time: accepted work can consume tokens meanwhile. |

Counters are charged in the same transaction as admission, after idempotent
replay checks. Cancel, delete and failed processing do not refund daily checks.
Failed admission transactions do not spend budget. A new reanalysis counts
as a check, including a correction, but it does not count as a second active
investigation when that investigation already has active work. The counters
start when enforcement is enabled; historical usage is not backfilled.

The shared global stop covers new investigations, captures, upload targets and
reanalyses when Scholarxiv is configured. Already accepted capture chunks and
uploads can finish, and reads, Stop/cancel and saves remain available. A closed
capture can still consume an active slot while its queued research is unfinished.
Expired open captures without active jobs no longer count. Payloads not yet
recognized by the quota code count conservatively as separate active jobs.

All API and worker processes must use the same quota flag and limits; mixed
enabled/disabled workers or different concurrency limits cannot enforce a shared
policy. Deploy only after owner review. Per-principal limits are not per-human
anti-abuse protection: creating another guest principal can obtain another
allowance. No provider entitlement, account-wide cost ceiling or paid overage
protection is inferred from these local counters.

**Still pending:** Groq ASR duration/retry reservations in #20; claim-extraction
and fallback/router-specific limits in #25; Voxide client-side session accounting;
observed upstream remaining balances; approved per-provider rates, concurrency
and account-wide budgets. The generic weighted bucket can meter other units,
but its existence is not integration. The operator summary reports these
providers as unintegrated with unknown balances. It is a local refill estimate,
not a rolling-hour provider dashboard or a measured daily allowance.
