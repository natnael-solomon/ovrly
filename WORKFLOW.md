# Workflow

Use the [ovrly development Project](https://github.com/users/natnael-solomon/projects/3) for tasks. Setup is in the [Android](android/README.md) and [backend](backend/README.md) READMEs; agent rules are in [AGENTS.md](AGENTS.md).

## 1. Pick up work

Read every `.md` file in the repository before modifying code, committing, pushing, or creating/updating a PR. Re-read files added or changed while working.

Features and bugs need an issue with one owner, an outcome and a completion checklist. Small documentation or cleanup changes may go directly to a PR. Coordinate overlapping changes and keep one main task in progress per developer.

Board stages are Backlog, Ready, In progress, In review and Done. Ready means the outcome is clear and dependencies are available. Use Area (Android, Backend, Research, Repo/tooling) and Priority (Now, Next, Later). Mark blockers with `blocked` and explain what is needed.

The issue is the task card; link its PR without creating a duplicate card. Move it to Done after merge. Close cancelled work as not planned and archive it.

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
| Backend | Frozen uv install, Ruff lint/format, strict MyPy, PostgreSQL tests, migrations and coverage checks in the [backend README](backend/README.md#local-checks). Android/evaluation checks do not replace Backend CI. |
| Recording, permissions, overlay or voice behavior | Applicable automated checks and checks on an authorized physical device. |
| Documentation only | Relevant documentation checks; no Android build required. |

Record commands and outcomes, remaining limitations and, when applicable, device model/Android version. Use approved content, never personal media or device serial numbers. Work requiring device evidence waits until that evidence exists.

### Continuous integration

| Workflow | What it checks |
| --- | --- |
| **Android checks** | On PRs to any branch, pushes to `main` and manual runs: Ubuntu 24.04, JDK 21, wrapper-based debug build/tests/lint, detekt/Compose/ktlint, unsigned release build with a ledger-style code, and real `apksigner`/`aapt2` verification using a temporary fixture key. Dependencies are checksum-verified; no production key or APK upload. |
| **Evaluation contract checks** | On PRs, pushes to `main` and manual runs: standard-library validator tests, synthetic examples and frozen metadata if `evaluation/corpus/` exists. No media downloads, provider keys or pipeline scoring. See [evaluation](evaluation/README.md). |
| **Backend checks** | On PRs to any branch, edits/retargeting, `main` pushes and manual runs: change-detection tests, frozen Python 3.11/uv environment, Ruff, strict MyPy, PostgreSQL 16, Alembic upgrade/drift checks and service coverage. Validation has a 15-minute timeout. |
| **Quality checks** | On every PR target/edit, `main` push and manual run: shared pre-commit Android/backend gates, workflow analysis, secret and dependency scanning, and negative fixtures. Includes JDK/SDK setup; no path skips, PostgreSQL or product-provider calls. Thirty-minute timeout. |

Known documentation and isolated evaluation changes skip Android setup/Gradle but still report the check. Changes to Android change detection, unknown paths, initial pushes and manual runs use the full job. Change-detection tests always run. Do not add workflow-level path filters that leave required checks pending.

Android, Device evidence and Evaluation contract checks accept every PR base and rerun on edits, including retargeting. They compare with the event's actual base commit. Revalidate children when a parent changes; old green checks do not validate the new combination. Backend changes still run the conservative full Android job. Stack support does not change protection, release or Telegram triggers.

Backend execution skips only known documentation-only diffs; the stable result still reports. Failed detection, missing outputs and failed/cancelled required validation fail the result. Only `main` pushes save uv caches. Available JUnit, coverage XML/JSON, baseline reports and summaries are retained for seven days.

Coverage includes every service Python file. The current worker floor is 90%; overall coverage may fall by at most one percentage point against remeasured `main`. Main pushes compare the preceding commit. Baseline tests and dependencies run in an owned disposable worktree with the current coverage version/configuration. Missing pre-bootstrap baselines and unimplemented auth/contracts are reported explicitly. See the [backend coverage commands](backend/README.md#ci-and-coverage). Android coverage, future-module coverage and required-check activation remain #13.

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
| `telegram-notify.yml` | Loud posts for Android CI failure/timeout and published releases (first notes line). PR failures are removed after a later pass; `main` failures remain. Cancelled runs are ignored. One silent PR card is updated through draft/review/merge/close with linked `Closes #N` issues; Dependabot PRs are skipped. |
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

The workflows maintain `TELEGRAM_NOTIFY_STATE` and `TELEGRAM_BOARD_STATE` for message IDs and board snapshots. Do not edit them manually. Deleting them resets state: the board re-baselines and PR cards start fresh.

PR titles, commit subjects, release notes and board titles are sent to Telegram; keep them free of private content. `.github/scripts/telegram_api.py` maps GitHub logins to team names in `DISPLAY_NAMES`.

## 5. Open and review a PR

Use a draft for unfinished work. Follow [the PR template](.github/PULL_REQUEST_TEMPLATE.md): issue/change/reason, actual checks, device evidence, screenshots, limitations and documentation checklist. Keep every section; write "Not applicable" where appropriate. Include before/after screenshots for visible UI changes.

Use a Conventional Commit PR title; it becomes the squash title. `.github/CODEOWNERS` routes review requests.

Capture, overlay, voice and manifest paths trigger **Device evidence** and the `needs-device-evidence` label. Fill at least one device table row with model, Android version, route, checks and result. "Not applicable" does not pass for those changes. CI cannot supply the physical-device evidence.

Every PR, including documentation, needs another developer's approval. Authors cannot approve their own work. Wait if no reviewer is available; resolve feedback and request another review after material changes.

## 6. Merge

Merge only when the PR is ready, another developer has approved, required checks pass, device evidence is present where required, and review discussions are resolved. Do not bypass these requirements for urgent changes.

Inspect the final squash author and full message, including automatically collected trailers. Apply the authorship rule in section 3; do not copy old commit messages wholesale or rewrite existing branch history without the branch owner's explicit approval.

Squash into `main`, delete the merged branch and close linked issues with `Closes #123` where appropriate.

The active `main` ruleset requires PRs, linear history, one approving review, stale-approval dismissal on push, resolved threads and up-to-date **Android checks** and **Device evidence**. Direct/force pushes and bypass actors are prohibited. Require **Backend checks** (REPO-04) and **Contract checks** (BE-03) when those workflows exist.

## 7. Document and release

Update documentation when behavior or setup changes. Put meaningful user-visible changes under `[Unreleased]` in [CHANGELOG.md](CHANGELOG.md); routine internal cleanup needs no entry.

Only the project owner authorizes releases and public APK distribution. Merge does not publish an APK. At release time, assign the version/date, document limitations and publish only approved artifacts.

Follow [release signing](docs/release-signing.md) for the production key, restored-backup verification, protected approval and version ledger. Never put passwords, keystores or private keys in Git, chat, logs or session artifacts.

Before claiming production delivery is fully verified, demonstrate two consecutive production-signed updates on an authorized device, preserving seeded test data, with launch, overlay, capture and share checks recorded in the PR.
