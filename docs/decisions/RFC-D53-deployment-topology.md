# RFC-D53: BE-11 deployment topology

The research RFC is a private team document; this record uses the wording of #21, which
cites RFC-D53 for the CP2 hosting topology. Hosting provider evidence stays in
[BC-D03](BC-D03-provider-hosting.md); this record does not repeat or change it.

**Question:** How is the CP2 backend deployed at no cost so phones can reach it, and what
is the fallback?

**Status:** Proposed. Drafted for #21 on 7 October 2026 from the #21 checklist and the
owner's dashboard configuration; awaiting confirmation by the product owner,
natnael-solomon, and a written EthioDeploy support answer on in-process background work.

**Owner and participants:** natnael-solomon (product owner, configures the host).
Neb-iyu selected EthioDeploy Free in BC-D03. No sponsor or provider statement is on file.

## Options considered

1. EthioDeploy Free: one Web Service running FastAPI with the embedded worker
   (`OVRLY_EMBED_WORKER=1`) and the Postgres add-on in the same project.
2. EthioDeploy Free with a second service for a standalone worker.
3. Koyeb free web service with the same single-process image and Neon free Postgres.
4. Hugging Face Docker Spaces, Render, Railway, Fly, Cloud Run or Oracle.

## Evidence and uncertainties

- Relayed EthioDeploy limits in BC-D03: one Free project, 256 MB / 0.5 CPU web container,
  50 GB-hours and 20 CPU-hours a month, sleep after 30 idle minutes, separate Postgres
  compute with 512 MB storage, quota exhaustion stops the project for the month.
- EthioDeploy documentation describes Service Type as platform routing, not a process
  cap; that one container may run background work is inferred, not verified.
- The durable job engine (BE-04) recovers jobs after a killed process, so sleep and
  restart delay work instead of losing it. Hosted recovery is not yet observed.
- At 256 MB, 50 GB-hours is about 200 awake hours a month if the full allocation is
  metered while awake (unverified), so always-on keep-alive does not fit.
- Koyeb and Neon free limits come from #21 and were not checked on their dashboards.
- Option 4 is excluded by #21: paid in 2026, no free workers, trial only, or card required.

## Chosen option and rationale

Option 1, with option 3 as the fallback. One process keeps the 256 MB container within
budget and matches the embedded worker already built; the add-on keeps jobs durable
across sleep. Option 2 needs a second service that one Free project may not allow.

## User-visible consequences

After 30 idle minutes the first request waits for a cold start, and running checks pause
until the service wakes. Keep-alive is used only during test and demo windows.

## Technical, privacy, cost and evaluation consequences

- Media lives on the container disk and is lost on restart; guest workspaces expire
  after 24 hours under the proposed BC-D06 retention.
- `/healthz` gates on the database, the migration head, writable storage and the
  embedded worker heartbeat; a post-deploy smoke workflow proves an intake round trip.
- No paid service, card or object storage. PDP Articles 18 to 22 remain open
  ([checklist](../operations/pdp-checklist.md)); the region is unknown.

## Dependencies / capability gates

- Product owner confirmation.
- EthioDeploy support answers listed in the [deployment runbook](../operations/deployment.md#owner-checklist).
- A passing smoke run against the deployed service.

## Rejected alternatives and why

Option 2: may exceed one Free project and doubles memory. Option 4: see #21.

## What evidence would reverse this decision

Support stating that one Web Service cannot run background work; a body limit, timeout
or disk size the intake cannot fit; quota metering that leaves too few awake hours for
CP2 testing; or a smoke run that cannot pass on the host. Any of these moves CP2 to the
Koyeb and Neon fallback.

## Links

#21, [BC-D03](BC-D03-provider-hosting.md), [BC-D06](BC-D06-retention.md),
[deployment runbook](../operations/deployment.md), RFC section 14 (EthioDeploy) and
section 16 (operational signals).
