# Workflow

Use the [ovrly development Project](https://github.com/users/natnael-solomon/projects/3) for tasks. Setup is in the [Android](android/README.md) and [backend](backend/README.md) READMEs; agent rules are in [AGENTS.md](AGENTS.md).

## 1. Pick up work

Read every `.md` file in the repository before modifying code, committing, pushing, or creating/updating a PR. Re-read files added or changed while working.

Features and bugs need an issue with one owner, an outcome and a completion checklist. Small documentation or cleanup changes may go directly to a PR. Coordinate overlapping changes and keep one main task in progress per developer.

Board stages are Backlog, Ready, In progress, In review and Done. Ready means the outcome is clear and dependencies are available. Use Area (Android, Backend, Research, Repo/tooling) and Priority (Now, Next, Later). Mark blockers with `blocked` and explain what is needed.

The issue is the task card; link its PR without creating a duplicate card. Move it to Done after merge. Close cancelled work as not planned and archive it.

Cross-cutting product, scope and date decisions are recorded in [docs/decisions](docs/decisions/README.md) using the RFC section 22 template. Issues cite them by ID (`BC-D..`, `RFC-D..`, `AC..`).

## 2. Branch and implement

Start from up-to-date `main` on a short-lived task branch, such as `feat/share-intake`, `fix/capture-stop`, `docs/setup-guide` or `chore/build-config`. Do not use permanent developer branches.

Keep PRs focused. Avoid unrelated refactoring, dependency upgrades and formatting. No routine direct or force pushes to `main`; the initial owner-authorized import was a one-time exception.

Dependent PRs may target an unmerged parent feature branch. Link the parent and keep the child diff focused. After an authorized parent squash merge, transplant only the child's commits onto the updated base and retarget it; do not merge the old parent history back into `main`. Rewriting published history requires explicit authorization. Stacking does not authorize merging.

## 3. Commit

Inspect the diff and staged files. Use small, coherent commits with `type(scope): description`; the scope is optional.

- `feat(android): add share intake`
- `fix(capture): release projection on stop`
- `docs: clarify setup`

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

Use the authorized human contributor's configured identity. Do not invent identities or include AI authors/co-authors or AI `Co-authored-by` trailers. This applies to the final squash message too.

Never commit credentials, signing keys, personal media, machine configuration, generated builds or session artifacts. Preserve applicable attribution.

## 4. Validate

| Change | Required evidence |
| --- | --- |
| Android code/build | Debug build, unit tests and lint; add or update tests for changed behavior. |
| Backend | Frozen uv install, Ruff lint/format, strict MyPy, PostgreSQL tests, migrations and coverage checks in the [backend README](backend/README.md#local-checks). Job-engine changes also run the [recovery suite](backend/README.md#durable-jobs-and-recovery). Android/evaluation checks do not replace Backend CI. |
| Recording, permissions, overlay or voice behavior | Applicable automated checks and checks on an authorized physical device. |
| Documentation only | Relevant documentation checks; no Android build required. |

Record commands and outcomes, remaining limitations and, when applicable, device model/Android version. Use approved content, never personal media or device serial numbers. Work requiring device evidence waits until that evidence exists.

### Continuous integration

| Workflow | What it checks |
| --- | --- |
| **Android checks** | On PRs to any branch, pushes to `main` and manual runs: Ubuntu 24.04, JDK 21, wrapper-based debug build/tests/lint, detekt/Compose/ktlint, unsigned release build with a ledger-style code, and real `apksigner`/`aapt2` verification using a temporary fixture key. Dependencies are checksum-verified; no production key or APK upload. |
| **Android instrumented checks** | Same triggers and change detection as Android checks, no path filter: AndroidX Test, Espresso and Compose instrumented tests on `reactivecircus/android-emulator-runner` with API 29 and API 34 x86_64 emulators (KVM; API 29 cold-boots without a snapshot, API 34 uses an AVD snapshot cached only by `main`; the device must publish its system services before any test, and the emulator is always stopped with a bounded grace period), Android Test Orchestrator, a check that every declared test reported a result, one automatic retry of each failed test with flakes named in the job summary, and JaCoCo unit plus instrumented line coverage. The API 34 job enforces the Android coverage floors. Reports are kept for seven days as `android-instrumented-reports`. Not yet a required check. See [instrumented tests and coverage](android/README.md#instrumented-tests-and-coverage). |
| **Evaluation contract checks** | On PRs, pushes to `main` and manual runs: standard-library validator tests, synthetic examples, explicit draft metadata and frozen metadata if `evaluation/corpus/` exists. No media downloads, provider keys or pipeline scoring. See [evaluation](evaluation/README.md). |
| **Backend checks** | On PRs to any branch, edits/retargeting, `main` pushes and manual runs: change-detection tests, frozen Python 3.11/uv environment, Ruff, strict MyPy, the [contracts package](packages/contracts/README.md) validator and server round-trip check, PostgreSQL 16, Alembic upgrade/drift checks and service coverage. Validation runs the suite for the change and again for `main`'s coverage baseline, so it has a 25-minute timeout. |
| **Contract checks** | On PRs to any branch, edits/retargeting, `main` pushes and manual runs, no path filter: lint/type-check of the contract tooling, JSON Schema and fixture validation, the OpenAPI document (`openapi-spec-validator`, spectral with the error-shape and typed-enum rules), the server round trip in `--check` mode, the contract pytest suites, pinned checksum-verified `oasdiff` against the PR base (a breaking change fails without a `VERSION` bump), and the Android `app.ovrly.contract` unit tests against the same fixtures. One result from both jobs. See [Contract checks](packages/contracts/README.md#contract-checks-ci). |
| **Backend recovery** | Same triggers and change detection as Backend checks, as a separate job: Alembic upgrade, then the Hypothesis job state-machine properties, retry-policy tests, queue invariant tests and the in-process API + worker recovery cases (worker killed before commit, after the provider call and after the artifact store, lease expiry with a live worker, cancel mid-retrieval, delete with a delayed callback, database connection drop, API lifespan restart, graceful and forced drain, every retry class, duplicate provider callbacks) against PostgreSQL 16. Documentation-only diffs skip execution but still report the check. |
| **Quality checks** | On every PR target/edit, `main` push and manual run: shared pre-commit Android/backend gates, workflow analysis, secret and dependency scanning, and negative fixtures. Includes JDK/SDK setup; no path skips, PostgreSQL or product-provider calls. Thirty-minute timeout. |
| **Deployment smoke** | Manual only, after each deploy: `python -m services.smoke` against the `base_url` input or the `OVRLY_BASE_URL` variable (wake bound, readiness, typed errors, guest intake round trip, isolation, delete, output redaction). No secrets; report and log kept 7 days. Not a PR check. See the [deployment runbook](docs/operations/deployment.md). |
| **Keep-alive** | Every 15 minutes, 08:00 to 23:59 EAT, only while the `OVRLY_KEEPALIVE` variable is `1`, plus manual runs: pings `/healthz` at `OVRLY_BASE_URL`. Spends the host's Free quota; enable for test and demo windows only. |
| **Database backup** | Manual only: `pg_dump` of `OVRLY_BACKUP_DATABASE_URL`, restore check in a disposable PostgreSQL 16 container, optional AES-256-encrypted 7-day artifact. |

Known documentation and isolated evaluation changes skip Android setup/Gradle but still report the check. Changes to Android change detection, unknown paths, initial pushes and manual runs use the full job. Change-detection tests always run. Do not add workflow-level path filters that leave required checks pending.

Android, Android instrumented, Device evidence and Evaluation contract checks accept every PR base and rerun on edits, including retargeting. They compare with the event's actual base commit. Revalidate children when a parent changes; old green checks do not validate the new combination. Backend changes still run the conservative full Android job. Stack support does not change protection, release or Telegram triggers.

Backend execution skips only known documentation-only diffs; the stable result still reports. Failed detection, missing outputs and failed/cancelled required validation fail the result. Only `main` pushes save uv caches. Available JUnit, coverage XML/JSON, baseline reports and summaries are retained for seven days.

Coverage includes every service Python file. The worker (`services/worker`), job engine (`services/jobs`) and auth (`services/api/auth`) each have a 90% line floor, aggregated by line counts across the module; overall coverage may fall by at most one percentage point against remeasured `main`. All thresholds use exact counts, not rounded display values. Main pushes compare the preceding commit. Baseline tests and dependencies run in an owned disposable worktree with the current coverage version/configuration. Missing pre-bootstrap baselines and the absent `services/contracts` path's future floor are reported explicitly. See the [backend coverage commands](backend/README.md#ci-and-coverage). Android coverage is described below.

Android coverage comes from **Android instrumented checks**: JaCoCo line coverage of unit and instrumented tests, with at least 90% in `capture/`, `share/` and `contract/` and at most a one-point overall drop against the last `main` measurement, which `main` pushes store in the Actions cache. A missing baseline is reported as unavailable. Exclusions (generated code, Compose preview files and gallery fixtures) are listed in `android/app/build.gradle.kts` and reviewed like code.

Instrumented hangs fail fast: each test has a 5-minute timeout, all Gradle runs share a 14-minute budget and the test step a 25-minute limit; a timed-out run is killed, logcat and process state are uploaded with the reports, and the test that was running is named. Instrumented flake policy: failed tests are retried once in the same job and flakes are named in the job summary. A test that flakes twice in 48 hours, or twice on one PR, is quarantined with `@Ignore` and an issue on the same day. Never re-run a job to get a green result.

`setup-gradle` validates wrapper JARs and owns the only Gradle cache: dependencies, wrapper distributions, build scripts, transforms and local outputs via `--build-cache`. Only `main` writes caches; PRs read them. SDK packages are reused or installed if missing; the full SDK is not cached. Configuration caching, Build Scans and provider-secret use are disabled. The enhanced cache provider is proprietary and [free for public repositories](https://github.com/gradle/actions/blob/v6.3.0/DISTRIBUTION.md); review its terms before making the repository private.

Available test/lint reports, including failed-run reports, are retained for seven days. Superseded PR runs are cancelled. Actions are commit-pinned. Dependabot proposes weekly Action, Android and backend uv updates with grouped backend minor/patch releases; it does not auto-merge.

Evaluation contract checks do not complete RES-01 or replace the future RES-03 regression check.

#### Shared quality gates

From the repository root:

```sh
uv sync --project backend --frozen --group quality
uv run --project backend --frozen --group quality pre-commit run --all-files
```

CI runs the same command, including on documentation-only PRs. Hooks are check-only,
run full scopes and fail if tools are missing. Set up JDK 21 and the Android SDK
as in the Android README; vulnerability checks need network access. Optional hook
installation is `uv run --project backend --frozen --group quality pre-commit install`.
Setup never installs hooks automatically. Only `main` pushes save caches.

The optional `quality` group pins pre-commit 4.6.2, actionlint-py 1.7.12.25,
zizmor 1.30.1 and pip-audit 2.10.1. The actionlint wrapper verifies the upstream
binary's SHA-256. Ruff enables security and coded-ignore rules; strict MyPy rejects
uncoded ignores. Zizmor analyzes workflows/composite actions offline. ShellCheck
and Pyflakes integrations remain outside these gates.

Two tested exceptions remain: actionlint's exact `queue: max` parser error is ignored only in `telegram-apk.yml`, while tests enforce publish ordering; the metadata-only notifier permits `workflow_run`, checks out `github.workflow_sha` and never downloads triggering-run artifacts. Remove the parser exception when supported. No production trigger or protection is changed.

Android runs pinned detekt/Compose rules and ktlint against reviewed baselines;
Gradle verifies downloaded dependencies. Baselines cover existing findings only
and are never regenerated by CI. See the [Android quality setup](android/README.md#quality-and-dependencies).

pip-audit checks a frozen, hashed export of every backend dependency group.
OSV-Scanner 2.6.0 checks supported tracked lockfiles across the repository,
including Android verification metadata and the universal uv lockfile. Only
dependency inputs are passed to OSV: no source, ignored/private files, Git
history or call analysis. Vulnerability services receive package coordinates,
not source code. `.github/osv-scanner.json` pins executable hashes; cached
binaries are checked again before execution.

To run OSV separately:

```sh
uv run --project backend --frozen --group quality python .github/scripts/dependency_audit.py osv
```

Findings, scanner/service failures and missing dependency-file or ecosystem
results fail the gate.
`osv-scanner.toml` currently has no exceptions. Any future exception requires an
advisory ID, owner, reason and expiry reviewed in the PR. Native fixtures verify
new Kotlin findings and checksum tampering fail. These gates do not replace
device evidence, integration tests or review.

Secret scanning, push protection and Dependabot alerts were verified enabled on
29 September 2026. Automatic security-update PRs are a separate setting and were
not enabled by this work.

#### Secret scanning

Shared hooks run Gitleaks 8.30.1 against all reachable history of fetched refs, including merge-resolution patches, the index and unstaged tracked changes. CI fetches full history; shallow clones fail. Untracked/ignored files are checked only when staged. Pattern matching cannot guarantee detection of every secret type or binary payload.

To scan directly:

```sh
uv run --project backend --frozen --group quality python .github/scripts/secret_scan.py
```

`.github/gitleaks.json` pins release archive hashes for Linux/WSL, macOS and Windows x64/arm64. Downloads use the official release; every cache reuse is checksum-verified. Only the executable is extracted into temporary storage. The helper requires 2 GiB free, retains its ignored `.local/quality-tools/` cache and never prunes data.

Native output is fully redacted and withheld; the wrapper prints file, line and rule only. Temporary reports are deleted, never uploaded. Findings, missing reports, invalid checksums and tool/Git failures fail the gate. An independent guard rejects `voxide.local.properties` anywhere in the index/history, including empty or later-deleted files; ignored local copies are allowed.

`.gitleaks.toml` extends defaults without current exceptions. Review any future exception by rule, exact value and path; never baseline real keys or whole directories. Inline allows and environment configuration overrides are disabled; `.gitleaksignore` is rejected. Real credentials require owner-coordinated rotation/remediation, not automatic history rewriting. Synthetic tests cover history, merge additions, index/working differences, redaction and failure paths.

GitHub scanning, push protection, alerts and dependency audits are separate settings/tools; these hooks do not configure them.

### Telegram notifications

Notification logic is tested Python in `.github/scripts/` (`telegram_*.py`); YAML supplies configuration. API failures fail the notification job visibly without affecting **Android checks**.

| Workflow | Behavior |
| --- | --- |
| `telegram-notify.yml` | Loud posts for Android CI failure/timeout and published releases (first notes line). PR failures are removed after a later pass; `main` failures remain. Cancelled runs are ignored. One silent PR card is updated through draft/review/merge/close with linked `Closes #N` issues; Dependabot PRs are skipped. Each PR is announced once: the card recorded under its number in `TELEGRAM_NOTIFY_STATE` is the announcement, so `synchronize` and `reopened` post the card only when none is recorded (standing in for a delayed or dropped `opened` webhook), and an hourly catch-up job (also `workflow_dispatch`) lists open PRs with the built-in token and announces any without a recorded card. A webhook that arrives after the catch-up finds the card and edits it instead of posting again. A malformed state variable fails the job without posting; a missing one fails only the catch-up, `synchronize` and `reopened` paths, since those have no other duplicate guard. |
| `telegram-board.yml` | Every 15 minutes, edits one pinned message for Ready, In progress, In review and `blocked` items. Posts silent summaries of status/priority/area changes, additions and removals. Ignores draft items. The first run establishes a baseline and posts "Board tracking started". Manual runs are supported. |
| `telegram-apk.yml` | Manual build, owner-approved signing and delivery. Follow [release signing](docs/release-signing.md) for requests, approval, redelivery and setup. |

The notification workflows' `workflow_run` and `schedule` triggers use `main`; their changes take effect after merge. Fork PRs cannot read secrets and send no messages. GitHub disables schedules after 60 days without repository activity; re-enable them under Actions.

#### Owner setup

1. Create a bot with [@BotFather](https://t.me/BotFather). Add it to the group with administrator rights to pin, edit and delete messages.
2. After posting in the group, read its chat ID from `https://api.telegram.org/bot<token>/getUpdates`; supergroup IDs start with `-100`.
3. Create an expiring classic PAT with `read:project`. Fine-grained tokens cannot read user-owned Projects. Rotate it before expiry; the board job fails visibly when it expires.
4. Create a fine-grained PAT for this repository with **Variables: read and write**. The built-in `GITHUB_TOKEN` cannot persist repository variables.
5. Set secrets `TELEGRAM_BOT_TOKEN`, `PROJECTS_READ_TOKEN` and `STATE_TOKEN`, plus variable `TELEGRAM_CHAT_ID`.
6. Run **Telegram board** manually and confirm the pinned message.

For layout trials, temporarily point `TELEGRAM_CHAT_ID` at a private chat and open a draft PR; its `pull_request` events use the branch's workflow.

The workflows maintain `TELEGRAM_NOTIFY_STATE` and `TELEGRAM_BOARD_STATE` for message IDs and board snapshots. Do not edit them manually. Deleting them resets state: the board re-baselines and PR cards start fresh from the next `opened`, `ready_for_review` or `closed` event, while the notifier's catch-up, `synchronize` and `reopened` paths fail closed until a card has been recorded again. Runs that share a state variable are not serialised, so a PR opened during the few seconds a catch-up run is posting could receive two cards.

PR titles, commit subjects, release notes and board titles are sent to Telegram; keep them free of private content. `.github/scripts/telegram_api.py` maps GitHub logins to team names in `DISPLAY_NAMES`.

## 5. Open and review a PR

Use a draft for unfinished work. Follow [the PR template](.github/PULL_REQUEST_TEMPLATE.md): issue/change/reason, actual checks, device evidence, screenshots, limitations and documentation checklist. Keep every section; write "Not applicable" where appropriate. Include before/after screenshots for visible UI changes.

Use a Conventional Commit PR title; it becomes the squash title. `.github/CODEOWNERS` routes review requests.

Capture, overlay, voice and manifest paths trigger **Device evidence** and the `needs-device-evidence` label. Fill at least one device table row with model, Android version, route, checks and result. "Not applicable" does not pass for those changes. CI cannot supply the physical-device evidence.

Every PR, including documentation, needs another developer's approval. Authors cannot approve their own work. Wait if no reviewer is available; resolve feedback and request another review after material changes.

Contract changes (anything under `packages/contracts/`) need one Android reviewer and one backend reviewer, because both sides build from the same schemas and fixtures. `.github/CODEOWNERS` routes the directory to both; the owner is the Android reviewer today. A breaking contract change bumps `packages/contracts/VERSION` in the same PR (**Contract checks** fails otherwise), and a backend-only contract PR does not close #15 (see the [contracts README](packages/contracts/README.md#versioning)).

## 6. Merge

Merge only when the PR is ready, another developer has approved, required checks pass, device evidence is present where required, and review discussions are resolved. Do not bypass these requirements for urgent changes.

Inspect the final squash author and full message, including automatically collected trailers. Apply the authorship rule in section 3; do not copy old commit messages wholesale or rewrite existing branch history without the branch owner's explicit approval.

Squash into `main`, delete the merged branch and close linked issues with `Closes #123` where appropriate.

The active `main` ruleset requires PRs, linear history, one approving review, stale-approval dismissal on push, resolved threads and up-to-date **Android checks**, **Device evidence**, **Backend checks**, **Backend recovery**, **Contract checks** and **Quality checks**. Direct/force pushes and bypass actors are prohibited.

## 7. Document and release

Update documentation when behavior or setup changes. Put meaningful user-visible changes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md); routine internal cleanup needs no entry.

Only the project owner authorizes releases and public APK distribution. Merge does not publish an APK. At release time, assign the version/date, document limitations and publish only approved artifacts. A pushed `v*` tag runs **Release**, which checks that version/date and copies the known limitations into the release notes; see [tagged demo releases](docs/release-signing.md#tagged-demo-releases).

Follow [release signing](docs/release-signing.md) for the production key, restored-backup verification, protected approval and version ledger. Never put passwords, keystores or private keys in Git, chat, logs or session artifacts.

Before claiming production delivery is fully verified, demonstrate two consecutive production-signed updates on an authorized device, preserving seeded test data, with launch, overlay, capture and share checks recorded in the PR.
