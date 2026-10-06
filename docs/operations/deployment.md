# Deployment runbook (BE-11)

Scope: [#21](https://github.com/natnael-solomon/ovrly/issues/21), the CP2 backend on
EthioDeploy Free ([BC-D03](../decisions/BC-D03-provider-hosting.md),
[RFC-D53](../decisions/RFC-D53-deployment-topology.md)). Owner: natnael-solomon
configures the host by hand; the repository supplies the readiness check, smoke test,
keep-alive and backup workflows below. **No deployment, live URL, cold-start time or
restore into a hosted database has been verified by this repository.** Record
measurements in #21 as they are made.

## Topology

One EthioDeploy Free project with one Web Service and the Postgres add-on.

| Item | Value |
| --- | --- |
| Root directory | `backend` |
| Builder | Nixpacks, as configured on the dashboard (builds succeeded before; earlier deploys failed only on missing environment variables) |
| Process | One Uvicorn process running the API with the embedded worker (`OVRLY_EMBED_WORKER=1`). Do not add `--workers`; one process fits the 256 MB container |
| Database | Postgres add-on, attached to the same project, exposed as `DATABASE_URL` |
| Health check path | `/healthz` |
| Media | Container disk under `backend/.data/uploads`; lost on restart or redeploy |

Relayed limits (BC-D03, not support correspondence): web container 256 MB RAM / 0.5 CPU;
50 GB-hours and 20 CPU-hours per month; sleep after 30 minutes without incoming HTTP;
Postgres 256 MB RAM / 0.25 CPU / 512 MB storage on separate compute; quota exhaustion stops
the project until the next month. HTTP body limit, HTTP timeout, region and disk size are
unanswered.

## Environment

Set these on the Web Service. Secrets go in the host's secret store only, never in Git,
chat, workflow logs or the APK.

| Variable | Value | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Provided by the Postgres add-on | The start command turns it into `OVRLY_DATABASE_URL` |
| `PORT` | Provided by the host | Do not set `OVRLY_API_PORT`; it only configures the development helper |
| `OVRLY_EMBED_WORKER` | `1` | Readiness fails with reason `worker` if the worker stops |
| `OVRLY_RETENTION_ENABLED` | `1` | Proposed [BC-D06](../decisions/BC-D06-retention.md) demo policy: every guest workspace, including uploads, is deleted 24 hours after the guest was created |
| `OVRLY_QUOTAS_ENABLED` | `1` | Proposed BC-D06 admission limits (6 checks per guest per UTC day, 2 active, 256 MiB uploads); see the [backend README](../../backend/README.md#opt-in-admission-quotas-22) |
| `OVRLY_SCHOLARXIV_API_KEY` | Secret, `sxv_...` | Registers the evidence stages; never logged |
| `OVRLY_JOB_IDLE_POLL_MAX_SECONDS` | Optional, for example `10` | Idle claim waits double from `OVRLY_JOB_POLL_SECONDS` (1) up to this cap and reset after a claim. Reduces idle database queries at the cost of up to this many seconds of pickup latency after an idle spell |
| `OVRLY_GOOGLE_CLIENT_ID` | Optional | Configuration, not a secret; empty leaves account linking at 503 |

Never set `OVRLY_STUB_REPORTS` on a public deploy; the smoke test fails if a report is a
development fixture. Leave the other `OVRLY_` settings at their documented defaults
([backend README](../../backend/README.md#configuration-and-storage)).

## Start command

```sh
export OVRLY_DATABASE_URL="postgresql+psycopg://${DATABASE_URL#*://}" && alembic upgrade head && exec uvicorn services.api.main:create_app --factory --host 0.0.0.0 --port $PORT --proxy-headers
```

- Migrations run before the server listens, so a failed migration fails the deploy
  instead of serving an old schema.
- `exec` makes Uvicorn the process that receives the host's stop signal. Without it the
  shell receives SIGTERM, Uvicorn may be killed without its shutdown, and the embedded
  worker cannot finish or release its in-flight lease. The lease still expires after
  `OVRLY_JOB_LEASE_SECONDS` (30) and the job is re-leased after the next start.
- `--proxy-headers` only trusts `X-Forwarded-*` from 127.0.0.1 unless
  `--forwarded-allow-ips` is set. The API does not use the client address or scheme, so
  this is harmless either way.

## Health check

`/healthz` answers `200` only when all of these hold; otherwise `503` with
`{"status":"unavailable","reason":...}` and a fixed log line:

| Reason | Meaning |
| --- | --- |
| `database` | The database did not answer within `OVRLY_DATABASE_TIMEOUT_SECONDS` |
| `migrations` | The applied Alembic revision is not the head this build ships (a failed or skipped migration, or a code rollback over a newer schema) |
| `storage` | The upload directory, or its nearest existing parent, is not writable |
| `worker` | The embedded worker stopped, failed, or has not polled or extended a lease for two minutes (longer when `OVRLY_JOB_LEASE_SECONDS` or twice the idle cap is longer) |

A healthy answer also carries the RFC section 16 signals:

```json
{"status": "ok",
 "checks": {"database": "ok", "migrations": "ok", "storage": "ok", "worker": "ok"},
 "signals": {"queue_depth": 0, "oldest_queued_seconds": null,
             "worker_heartbeat_seconds": 0.4, "scholarxiv": "configured"}}
```

`queue_depth` and `oldest_queued_seconds` count claimable queued jobs for stages the
embedded worker has handlers for; stages without handlers yet (`asr`, `device_text`,
`claim_extraction`) are excluded because they wait by design. `scholarxiv` says whether
the key is configured; readiness never calls a provider, because a probe would spend the
shared Scholarxiv quota and a provider outage must not make the host restart the service.

## Sleep, cold start and keep-alive

The Free web service sleeps after 30 idle minutes and the embedded worker sleeps with it.
Accepted jobs are durable in Postgres: on the next start, expired leases return to the
queue and the worker picks them up. A job running when the service sleeps is killed and
retried, so a person checking sees a delay, not a silent loss.

**Budget first.** At 256 MB, 50 GB-hours is about 200 awake hours a month (about
6.5 hours a day), if the host meters the full allocation while awake (unverified). Keeping
the service awake 16 hours a day would stop the project after roughly 12 days. Keep it
awake only for test sessions and demos.

- **Keep-alive workflow** (`.github/workflows/keep-alive.yml`): when the repository
  variable `OVRLY_KEEPALIVE` is `1` and `OVRLY_BASE_URL` is set, it pings `/healthz`
  every 15 minutes from 08:00 to 23:59 EAT. Set the variable to `0` outside demo windows.
  It can also be run by hand. GitHub runs schedules only from `main`, may delay them and
  disables them after 60 days without repository activity.
- **External pinger** (recommended for demos, because GitHub schedules can slip past the
  30-minute sleep): a free cron-job.org job calling `GET <base URL>/healthz` every
  10 minutes for the demo window only.
- **Before a demo**: run **Deployment smoke** 10 to 15 minutes ahead; it records the
  wake time.

Cold-start time is not yet measured. The smoke test's `wake_seconds` is the time from the
first request to the first answer from the API itself; record a few values in #21.

## Post-deploy smoke

Run after every deploy. It needs no secret: it mints its own guests.

```sh
gh workflow run deployment-smoke.yml -f base_url=https://<service host>
```

or, from `backend/`, `uv run --frozen python -m services.smoke --base-url https://<service host>`.
Steps, each printed with its time:

1. **wake**: repeats `GET /healthz` through host 502/503/504 answers until the API
   answers; fails after `wake_seconds` (default 180).
2. **health**: every check `ok`, embedded worker heartbeat at most 120 seconds.
3. **typed-errors**: `401 AUTHENTICATION_REQUIRED` with the echoed `X-Request-Id`, and
   `404 NOT_FOUND`, both in the shared error shape.
4. **guest**: mints a guest credential.
5. **upload**: declares, streams and completes a 1-second synthetic silent WAV with a
   random canary chunk (no consent or rights question).
6. **intake**: missing `Idempotency-Key` is `400 IDEMPOTENCY_KEY_REQUIRED`; the
   investigation is `202`; a replay returns the same investigation.
7. **poll**: the intake job reaches a terminal state within 120 seconds and must be
   `published`; any report must not be a fixture and must cite only its own evidence.
8. **isolation**: a second guest gets `404 NOT_FOUND` for the investigation.
9. **delete**: `DELETE /v1/jobs/{id}` tombstones the job; a later cancel is `404`.
10. **redaction**: no printed line contains a bearer token, the canary or the `ovk_` or
    `sxv_` prefixes. The workflow greps its own log and report for the same text.

The workflow keeps `smoke.json` and `smoke.log` for 7 days as the `deployment-smoke`
artifact. The smoke never opens a Voxide session. Until BE-07 (#20) adds media stages,
an upload investigation stays `queued` at stage `intake` after its intake job publishes,
so the smoke cannot yet reach `SUCCEEDED`/`PARTIAL` or assert ASR, router and Scholarxiv
call budgets; extend it when those stages land. After the run, search the host logs for
`ovrly-smoke-canary-`, `ovk_` and `sxv_`; none may appear.

## Logs

| Where | What | Retention |
| --- | --- | --- |
| EthioDeploy service logs (dashboard) | API, worker and Alembic stderr. Application lines are fixed events, codes, counts, job UUIDs and hashed request IDs; messages, exceptions and access URLs are redacted ([data map](data-map.md)) | Host-owned; unknown. Proposed seven days |
| EthioDeploy Postgres add-on logs | Database server logs, if exposed | Host-owned; unknown |
| GitHub Actions | Smoke, keep-alive and backup logs; secrets are masked and the smoke prints no credentials | Repository log retention; smoke and backup artifacts 7 days |

The application writes no log file. Alembic prints revision names only.

## Rollback

1. Run **Database backup** first if the release added a migration.
2. Redeploy the previous good commit from the EthioDeploy dashboard, or revert the change
   on `main` through a PR and redeploy.
3. If the bad release migrated the schema, the old build reports `503 migrations` until
   the schema matches it. Either run `alembic downgrade <previous head>` with the **new**
   release's code against the same `OVRLY_DATABASE_URL` before redeploying the old one
   (downgrades are covered by Backend checks but can drop data added by the new columns),
   or restore the pre-release backup (below).
4. Run **Deployment smoke** against the rolled-back service.

## Backup and restore

The Free add-on has no confirmed backup or point-in-time recovery; assume none. The data
is short-lived demo content (24-hour guest retention), so a manual dump before risky
changes and once to prove the procedure is the plan.

**Workflow** (`.github/workflows/database-backup.yml`, manual): dumps with `pg_dump`
(custom format, PostgreSQL 16 client), restores the dump into a disposable PostgreSQL 16
service container on the runner and prints the restored Alembic revision and row counts.
Only when the `upload` input is checked (off by default, because the
[data map](data-map.md) otherwise forbids dumps in CI artifacts) does it encrypt the dump
with AES-256 (`gpg --symmetric`) and upload the ciphertext as the `database-backup`
artifact for 7 days. The plaintext is deleted on the runner either way.

Repository secrets:

- `OVRLY_BACKUP_DATABASE_URL`: a `postgres://` or `postgresql://` URL of the add-on that
  is reachable from the internet. If the add-on only accepts connections from inside the
  project, the workflow cannot reach it; use the manual procedure from a host that can.
- `OVRLY_BACKUP_PASSPHRASE` (only for `upload`): at least 20 characters, also kept in the
  owner's password manager. Anyone with read access to a public repository can download
  artifacts, so the passphrase is what protects the dump.

`pg_dump` must be at least the server's major version; the workflow fails with a version
error against a newer server, and the image pin must then move to that major version.

**Manual dump** from a trusted machine:

```sh
pg_dump --format=custom --no-owner --no-privileges --dbname "$DATABASE_URL" --file ovrly.dump
```

**Restore** (only into an isolated database first, as the [data map](data-map.md)
requires; run retention before exposing restored data):

```sh
gpg --decrypt --output ovrly.dump ovrly.dump.gpg
pg_restore --clean --if-exists --no-owner --no-privileges --dbname "$TARGET_URL" ovrly.dump
```

Then start the service against the target (migrations bring it to head) and run the
smoke test. Uploaded media lives on the container disk and is never in the dump; restored
investigations can reference bytes that no longer exist. A dump of a 512 MB add-on fits
comfortably in a runner and an artifact; it does not count against the add-on storage.

## Media storage

Uploads and capture chunks go to the container disk (`OVRLY_STORAGE_DIR`, default
`backend/.data/uploads`). They disappear on restart, sleep or redeploy, and retention
deletes them with the guest workspace after 24 hours. No paid object storage is used.
Deleting bytes as soon as processing finishes belongs to BE-12; if persistence becomes
necessary, a Supabase Storage free-tier `UploadStore` is the candidate. The readiness
`storage` check only proves the directory is writable, not its free space.

## Fallback: Koyeb and Neon

Only if EthioDeploy cannot run the embedded worker or its limits do not fit: one Koyeb
free web service (512 MB / 0.1 vCPU, verify on the dashboard) with the same start
command, environment and `/healthz` health check, plus Neon free Postgres (0.5 GB,
100 CU-hours a month, suspends after 5 idle minutes). Use Neon's pooled or direct URL as
`DATABASE_URL`; its `sslmode=require` query is kept by the start command. A worker that
polls more often than every 5 minutes keeps Neon awake (about 182 CU-hours a month at
0.25 CU if awake all month, over the free allowance), so set
`OVRLY_JOB_IDLE_POLL_MAX_SECONDS` high (up to 600) and measure CU-hours after one day.
Not Hugging Face Docker Spaces, Render, Railway, Fly, Cloud Run or Oracle (#21). None of
this is verified.

## Owner checklist

These need dashboard access or provider answers and are open until recorded in #21:

- [ ] Add `exec` before `uvicorn` in the start command (see above).
- [ ] Deploy with the environment above; confirm `/healthz` returns 200 with all checks
  `ok`. Set the repository variable `OVRLY_BASE_URL` to the service origin.
- [ ] Run **Deployment smoke**; record the run link, `wake_seconds` after a sleep and
  the readiness signals in #21. Search host logs for the canary and key prefixes.
- [ ] Ask EthioDeploy support on Telegram, and record the written answers: whether one
  Web Service container may run a background task in-process; monthly quota metering
  (allocation or usage); ephemeral disk size; HTTP request body limit and timeout;
  datacenter region; whether Postgres and Redis can both attach; whether the add-on
  accepts external connections and has any backups; the stop-signal grace period.
- [ ] Compare the body limit with `OVRLY_UPLOAD_MAX_BYTES` (256 MiB) and capture chunk
  sizes; lower the setting if the host limit is smaller.
- [ ] Set `OVRLY_BACKUP_DATABASE_URL`, run **Database backup** once (its restore check
  proves the dump), and decide whether an encrypted artifact is wanted (then set
  `OVRLY_BACKUP_PASSPHRASE` and check `upload`). Restore one dump into an isolated
  database by hand.
- [ ] Decide keep-alive windows; set `OVRLY_KEEPALIVE` and, for demos, an external pinger.
- [ ] Check host log retention and set it to the proposed seven days if possible.
- [ ] Confirm or revise [RFC-D53](../decisions/RFC-D53-deployment-topology.md).
