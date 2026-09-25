# Workflow

Our shared contribution process. See the [Android README](android/README.md)
and [backend README](backend/README.md) for setup, and [AGENTS.md](AGENTS.md)
for coding-agent instructions.

The CI workflow is in [`.github/workflows/android.yml`](.github/workflows/android.yml)
and runs on every pull request and push to `main`. The public
[ovrly development Project](https://github.com/users/natnael-solomon/projects/3)
holds all task issues; its README is the team briefing. The `main` protection
ruleset is active (see §6), and the production-signing environment, ledger
and key described in §4 are provisioned, with the key's offline backup
verified by the owner; the first production build has not been dispatched.
This document does not enforce those rules.

## 1. Pick up work

Read every `.md` file in the repository before modifying code, committing,
pushing, or creating/updating a pull request. Follow the documented requirements
and re-read any Markdown files added or changed while working.

Use one GitHub Project, **ovrly development**, as the shared task board.

- Features and bugs need a repository issue on the board with one owner,
  an intended outcome and a short completion checklist.
- Tiny documentation or cleanup changes may go directly to a PR.
- Coordinate changes that overlap another developer's work.

Board stages: Backlog -> Ready -> In progress -> In review -> Done.
Ready means the outcome is clear and dependencies are available. Use Area
(Android, Backend, Research, Repo/tooling) and Priority (Now, Next, Later)
alongside the assignee. Keep one main task in progress per developer.

The issue stays the task card; link its PR rather than creating a duplicate
card. Mark blocked work with a `blocked` label and explain what it needs.
Move completed work to Done after its PR is merged, not merely opened.
Close canceled tasks as not planned and archive them instead of marking
them as delivered.

## 2. Branch and implement

Start from an up-to-date `main`. Use short-lived task branches:
`feat/share-intake`, `fix/capture-stop`, `docs/setup-guide` or
`chore/build-config`.

Keep each PR focused on one change. Avoid unrelated refactoring, dependency
upgrades or formatting churn. Do not use permanent branches per developer.

No routine direct pushes or force pushes to `main`. The initial repository
import is a one-time, owner-authorized exception.

## 3. Commit

Review the diff and staged files before committing. Prefer small, coherent
commits with descriptive messages. Use `type(scope): description`, with an
optional scope:

- `feat(android): add share intake`
- `fix(capture): release projection on stop`
- `docs: clarify setup`

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

AI assistants and coding agents must not be listed as commit authors or
co-authors. AI `Co-authored-by` trailers are prohibited. Use the authorized
human contributor's configured identity and inspect the complete message
before committing; do not invent contributor identities.

Never commit credentials, signing keys, personal media, machine-local
configuration, generated build output or development-session artifacts.
Preserve applicable attribution.

## 4. Validate

- Android code and build changes require the debug build, unit tests and
  lint. Use the [Android build instructions](android/README.md#setup)
  for your operating system.
- Add or update tests when changing behavior.
- Documentation-only changes do not require an Android build.
- Recording, permission and floating-overlay behavior changes also require
  relevant checks on an authorized physical device before merging.

Record commands and outcomes, device model and Android version where relevant,
and remaining limitations in the PR. Passing CI does not replace device
evidence. A change requiring device checks waits until that evidence exists.
Use approved test content; do not expose personal media or device identifiers
such as serial numbers.

### Continuous integration

**Android checks** runs on PRs to `main`, pushes to `main` and manual dispatch.
One Ubuntu 24.04 job uses JDK 21 and the project's Gradle wrapper to build,
unit test and lint together. When Android work runs it also assembles the
unsigned release APK with a ledger-style version code and exercises the
release helpers' real `apksigner`/`aapt2` path against it using an ephemeral
fixture key generated in a temporary directory and deleted afterwards; no
production key is involved and no APK is uploaded. Known documentation and
isolated `evaluation/` changes (including its dedicated workflow) skip Android
setup and Gradle, but still return the same required check. Changes to Android
change detection itself still require the full job. Initial pushes, manual runs
and unknown paths run the full checks. Change-detection tests run on every
invocation.

**Evaluation contract checks** runs separately on every PR, push to `main` and
manual dispatch, without workflow-level path filters. It tests the Python
standard-library validator and synthetic examples; if a future
`evaluation/corpus/` is present it also checks frozen metadata, not media bytes
or pipeline performance. It uses no provider keys or downloads. This check is
not the future RES-03 evaluation-regression gate and does not imply RES-01 is
complete. See the [evaluation workflow](evaluation/README.md) for local commands
and human-review requirements.

`setup-gradle` validates wrapper JARs and owns the only Gradle cache:
dependencies, wrapper distributions, compiled build scripts, transforms and
local task outputs (`--build-cache`). Its enhanced provider is proprietary
and [free for public repositories](https://github.com/gradle/actions/blob/v6.3.0/DISTRIBUTION.md);
revisit its terms if the repository becomes private. Only `main` runs write
caches; PRs read them. The job reuses the runner's SDK packages and installs
only missing ones. It does not cache the whole SDK or enable configuration
caching, publish Build Scans, use provider secrets or upload APKs.

Test and lint reports are retained for seven days, including available reports
from failed builds. Superseded PR runs are canceled. Actions are commit-pinned;
Dependabot proposes weekly action and Android dependency updates for review,
without automatic merging.

### Telegram notifications

Two workflows mirror repository activity into the team Telegram group. Their
logic lives in tested Python under `.github/scripts/` (`telegram_*.py`); the
YAML only supplies configuration. Failures never affect **Android checks**, but
a rejected Telegram or GitHub API call fails the notification job so it shows
red under Actions.

**Telegram notifications** (`telegram-notify.yml`) reacts to events:

- **Android CI** `failure` or `timed_out`: a loud post linking the run, commit
  and author. Posts for PR runs are deleted once a later run on that PR passes;
  posts for `main` stay. Cancelled (superseded) runs are ignored.
- Pull requests: one silent card per PR, edited in place from draft to ready
  for review to merged or closed, with linked `Closes #N` issues. Dependabot
  PRs are skipped.
- Published releases: a loud post with the first line of the notes.

**Telegram board** (`telegram-board.yml`) polls the **ovrly development**
Project every 15 minutes. It keeps one pinned message listing Ready, In
progress, In review and `blocked`-labelled items, edited in place, and posts a
silent summary of Status, Priority and Area changes plus additions and
removals. Draft items are ignored. The first run records a baseline and posts
"Board tracking started" instead of listing everything. It can be run
manually from Actions.

**Telegram APK** (`telegram-apk.yml`) is the gated pre-launch build. Any
developer may request one after fetching by running
`scripts/request-build.ps1` (Windows) or `sh scripts/request-build.sh`
(Linux, WSL, Git Bash): it resolves `origin/main` to its exact commit, shows
the SHA and subject, asks once, and dispatches the workflow. The Actions UI
works too if you paste the full 40-character SHA yourself; a personal shortcut
is `gh alias set ovrly-build '!sh scripts/request-build.sh --yes'`. There is
deliberately no automatic trigger (push, label, comment or schedule): a
request must be a person choosing a commit, because every dispatch reserves a
version code and queues an owner approval. The workflow refuses branch names,
unmerged commits, commits without a successful **Android checks** run from the
real Android workflow, reruns of an earlier attempt, and any repository whose
protections are not in place (see the prerequisites below). Three jobs with
distinct trust:

1. **Preflight** runs helper code from the workflow's own revision, checks
   every protection, and reserves the next version code in the immutable
   ledger. It is bound to this source commit and this run attempt; building
   the same commit again gets a new code.
2. **Build** checks out the requested commit with no credentials and no
   secrets and produces an unsigned, minified, non-debuggable APK plus a
   provenance file. The unsigned artifact is uploaded to the run; on a public
   repository anyone can download it.
3. **Sign, issue and deliver** runs in the `production-signing` environment,
   so its secrets exist only after the owner approves this completed build.
   Under a repository-wide mutex it re-checks protections, verifies the
   artifact against the build provenance and the ledger, signs with the
   production key, checks the signer against the pinned certificate, records
   the issuance in the ledger, and only then uploads the signed artifact and
   sends it to the existing Telegram destination. A build whose code is not
   above the issued high-water mark when it reaches this job is refused, so an
   older build approved late cannot be shipped after a newer one.

Before approving, the owner confirms in the run: the source commit is the one
requested, `Android checks` is green on it, the unsigned artifact's SHA-256 in
the log matches the build provenance, and the build was not superseded.

**Redelivery.** If Telegram fails after the signed artifact was uploaded,
dispatch the workflow with `redeliver_run_id` set to that run. Preflight
locates the issued artifact by id, checks it is the current issued version and
that its bytes and certificate match the ledger, and the publish job resends
exactly those bytes after owner approval. Nothing is built, allocated or
signed. Older issued builds cannot be redelivered; dispatch a new build
instead. A missing or expired artifact also requires a new build. Telegram
does not offer exactly-once delivery: a timeout after Telegram accepted the
upload can produce a duplicate; the caption carries the build number and the
first 16 hex digits of the APK's SHA-256 so duplicates are recognisable.

**Version codes.** The ledger lives in annotated tags under
`refs/tags/release-ledger/`: `bootstrap` (owner-created baseline),
`reserve/<code>` (a build reservation) and `issue/<code>` (a signed, verified
build). Codes are `max(existing) + 1`, never reused; failed or cancelled runs
leave gaps, which is fine. The first CI code is 2, above the app's local
default of 1. Google Play accepts 1..2100000000 inclusive; both the helper and
the Gradle build reject anything else, and local builds without the property
stay at 1. When the app moves to Google Play, its first upload must carry a
version code above the highest code in the ledger, reserved or issued, enrol
this same signing key as the app-signing key, and the ledger remains the only
counter; never start a second one. Builds sent to Telegram before this ledger
existed are unverified historical artifacts with no recoverable provenance.

**Evidence.** Each run keeps the unsigned and signed artifacts for 90 days,
the ledger tags permanently, and both provenance files (source SHA, version
code, digests, certificate, and the approval records GitHub returns). Failure
after issuance but before upload leaves an issued, undistributed code; the
next build simply takes the following code.

**Prerequisites** (owner setup, verified by preflight on every run and
described step by step in [docs/release-signing.md](docs/release-signing.md)):
the active `main` ruleset requiring pull request review and `Android checks`
(already in place); an active tag ruleset whose include list is exactly
`refs/tags/release-ledger/bootstrap`, `refs/tags/release-ledger/reserve/*` and
`refs/tags/release-ledger/issue/*`, blocking update, deletion and
non-fast-forward, with no exclusions and no bypass actors; the
`production-signing` environment with the owner as its sole required reviewer,
self-review allowed, a custom branch policy of exactly `main`, and
administrator bypass off (the API reports this as `can_admins_bypass`, which is
outside the published schema, so it is observed and must be exactly `false`;
confirm it in the UI too); the four signing secrets in that environment; the
`EXPECTED_SIGNING_CERT_SHA256` and `LEDGER_BOOTSTRAP_TAG_SHA` repository
variables; and the owner-created ledger bootstrap tag those variables pin.
Until every one is present the workflow stops at preflight with a message
naming what is missing. Ledger tags anchor to the `main` HEAD current when CI
wrote them (the only target the workflow token may create) and carry the source
commit inside their record; live ledger writes remain unexercised until this
setup exists. It does not create a GitHub Release; §7 still governs releases.

Both workflows only run from `main`: `workflow_run` and `schedule` triggers do
not fire for other branches, so changes to them take effect after merging.
Fork PRs cannot read secrets and produce no messages. GitHub disables scheduled
workflows after 60 days without repository activity; re-enable it under Actions.
To trial layout changes before merging, temporarily point `TELEGRAM_CHAT_ID`
at a private chat and open a draft PR; its `pull_request` events run the
branch's own workflow.

Setup, performed once by the project owner:

1. Create a bot with [@BotFather](https://t.me/BotFather) and add it to the
   group as an administrator with pin, edit and delete rights. Editing older
   messages and pinning require admin status.
2. Read the group's chat ID from `https://api.telegram.org/bot<token>/getUpdates`
   after posting in the group; supergroup IDs start with `-100`.
3. Create a classic personal access token with only the `read:project` scope
   on the project owner's account. Fine-grained tokens cannot read user-owned
   Projects. Set an expiration; rotating the token is the owner's
   responsibility, and the board job fails visibly when it lapses.
4. Create a fine-grained personal access token restricted to this repository
   with only the **Variables: read and write** permission. The workflows'
   built-in `GITHUB_TOKEN` cannot manage repository variables, so this token
   persists their state. It expires like the project token.
5. Add repository secrets `TELEGRAM_BOT_TOKEN`, `PROJECTS_READ_TOKEN` and
   `STATE_TOKEN`, and the repository variable `TELEGRAM_CHAT_ID`.
6. Run **Telegram board** manually to create the pinned message and confirm
   the bot, chat ID and tokens work.

The workflows create and maintain the variables `TELEGRAM_NOTIFY_STATE` and
`TELEGRAM_BOARD_STATE` (message IDs and the last board snapshot). Deleting a
state variable resets that workflow: the board re-baselines and PR cards start
fresh. Never edit them by hand. PR titles, commit subjects, release notes and
board item titles are sent to Telegram, so keep them free of anything private.
GitHub logins appear as the short team names in `DISPLAY_NAMES`
(`.github/scripts/telegram_api.py`); add new team members there.

## 5. Open and review a PR

Open a draft for unfinished work or early feedback. The pull request template
(`.github/PULL_REQUEST_TEMPLATE.md`) is the required structure: what and why
with the linked issue, how it was checked with the commands actually run, a
device-evidence table, screenshots for visible UI changes, limitations, and
the docs/changelog checklist. Fill every section or write why it does not
apply; do not delete sections.

Use a Conventional Commit PR title because it becomes the squash commit
message.

Changes under `android/app/src/main/java/app/ovrly/{capture,overlay,voice}`
or to the Android manifest cannot be exercised by CI. The **Device evidence**
workflow labels such PRs `needs-device-evidence` and fails until the device
evidence table has at least one filled row (model, Android version, route,
what was verified, result). Writing "Not applicable" there does not pass.

Review requests route through `.github/CODEOWNERS`. Every PR needs approval
from another developer, including documentation PRs. Authors do not approve
their own changes. If no reviewer is available, the PR waits. Resolve feedback
before merging and request another review when material changes are made
after approval.

## 6. Merge

Merge only when the PR is no longer a draft, another developer has approved,
required automated checks pass, required device evidence is present and
review discussions are resolved.

Squash merge into `main`, then delete the merged branch. Link completed
issues with `Closes #123` where appropriate. Do not bypass the requirements
for urgent changes.

Before confirming a squash merge, inspect the final author and full message.
Use a Conventional Commit title and remove any AI authorship/co-authorship
attribution, including trailers GitHub may assemble from branch commits.
Do not copy historical commit messages wholesale. Existing branch history must
not be rewritten without explicit approval; the final squash commit must comply
with the authorship rule above.

`main` is protected by an active ruleset: no direct pushes, no force pushes,
linear history, one approving review with stale approvals dismissed on push,
all review threads resolved, no bypass actors, and the **Android checks** and
**Device evidence** status checks required and up to date with `main`. Do not
add workflow-level path filters: docs-only PRs must still report the required
checks rather than leave them pending. Add **Backend checks** (REPO-04) and
**Contract checks** (BE-03) to the required list when those workflows exist.

## 7. Document and release

Update relevant documentation when behavior or setup changes. Add meaningful
user-visible changes to [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`.
Routine internal cleanup does not need a changelog entry.

Merging does not automatically create a release or publish an APK. For now,
only the project owner authorizes releases and public builds. At release
time, assign the version and date, document known limitations and publish
only the explicitly approved artifacts.

Production signing uses one key that the owner generates and keeps: an
encrypted offline backup with separately stored recovery credentials, plus a
protected working copy in the `production-signing` environment for CI. Verify
that the backup restores before the key signs anything. Passwords, keystores
and private keys never go into Git, chat, logs or session artifacts. The setup
and its verification steps are in
[docs/release-signing.md](docs/release-signing.md). Before production delivery
is considered verified, two consecutive production-signed builds must update
in place on an authorised device, preserving seeded test data, with launch,
overlay, capture and share checks recorded in the PR.
