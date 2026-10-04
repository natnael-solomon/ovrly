# BC-D06: proposed demo retention windows

**Decision ID and question:** BC-D06 (retention portion only). How long should
the current demo backend retain a workspace and its execution receipts?

**Status:** Proposed. Implementation does not establish approval.

**Owner and participants:** Product owner @natnael-solomon must accept or revise.
Backend implementation: @Nattyy-1. No acceptance is recorded.

**Options considered:** Indefinite retention; rolling per-object/inactivity
windows; an explicit, fixed-lifetime demo workspace.

**Evidence and uncertainties:** #77 requires bounded, configurable deletion
across current stores. The current backend has guest identities, uploads,
investigations, idempotency responses and jobs, but no permanent saved-report
storage. A durable account policy, provider terms, infrastructure backup
retention and approved hosting locations remain unresolved.

**Chosen option and rationale:** Proposed only: expire the whole workspace
86400 seconds (24 hours) after principal creation, including its credentials
and all dependent content. Remove unused pending uploads at target expiry.
Apply the same content age limit to legacy ownerless jobs. Retain detached
tombstones and terminal retention-job metadata for 604800 seconds (seven days),
then physically delete them. Run every 60 seconds with batches of 100 subjects.
Automatic retention remains disabled by default until explicitly enabled for
approved demo data. These numbers are proposals, not statutory deadlines.

**User-visible consequences:** Enabling this policy can remove recently
uploaded content in an older workspace. Activity does not extend its lifetime.
After expiry, old credentials fail, responses cannot be replayed indefinitely,
and users need a new guest identity. Do not enable for permanent accounts or
saved reports without a revised decision.

**Technical, privacy, cost and evaluation consequences:** One locked deletion
path per store, with shared `JobQueue.delete` fencing; fewer duplicate
implementations and no paid infrastructure. Expiry makes work eligible for
cleanup, not an absolute timing SLA during outages/backlogs. Filesystem removal
can precede a rolled-back database transaction and is retried idempotently.
Physical disk/WAL/backup erasure and remote-provider deletion are not proven.
Proposed host log retention is seven days and must be configured by the operator.

**Dependencies / capability gates:** Owner acceptance, matching API/worker
settings and storage mounts, #80/#81 integration and the operational transfer
checklist. No quotas, token buckets or global intake stop are decided here.
Those remain #22 and the separate BC-D06 budget discussion.

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
