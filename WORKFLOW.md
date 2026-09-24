# Workflow

Our shared contribution process. See the [Android README](android/README.md)
and [backend README](backend/README.md) for setup, and [AGENTS.md](AGENTS.md)
for coding-agent instructions.

The CI workflow is in [`.github/workflows/android.yml`](.github/workflows/android.yml)
and runs on every pull request and push to `main`. The public
[ovrly development Project](https://github.com/users/natnael-solomon/projects/3)
holds all task issues; its README is the team briefing. The `main` protection
ruleset is prepared but still disabled; enabling it is tracked as `REPO-02`.
This document does not enforce those rules.

## 1. Pick up work

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
unit test and lint together. Known documentation-only changes skip Android
setup and Gradle, but still return the same required check. Initial pushes,
manual runs and unknown paths run the full checks. Change-detection tests
run on every invocation.

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
  for review to merged or closed, with linked `Closes #N` issues. Becoming
  ready for review also posts a short silent ping. Dependabot PRs are skipped.
- Published releases: a loud post with the notes in an expandable quote.
- Manual dispatch: a silent test message, the only way to verify the setup
  before a real event.

**Telegram board** (`telegram-board.yml`) polls the **ovrly development**
Project every 15 minutes. It keeps one pinned message listing Ready, In
progress, In review and `blocked`-labelled items, edited in place, and posts a
silent summary of Status, Priority and Area changes plus additions and
removals. Draft items are ignored. The first run records a baseline and posts
"Board tracking started" instead of listing everything.

Both workflows only run from `main`: `workflow_run` and `schedule` triggers do
not fire for other branches, so changes to them take effect after merging.
Fork PRs cannot read secrets and produce no messages. GitHub disables scheduled
workflows after 60 days without repository activity; re-enable it under Actions.

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
4. Add repository secrets `TELEGRAM_BOT_TOKEN` and `PROJECTS_READ_TOKEN`, and
   the repository variable `TELEGRAM_CHAT_ID`.
5. Run **Telegram notifications** manually to confirm a message arrives, then
   **Telegram board** to create the pinned message.

The workflows create and maintain the variables `TELEGRAM_NOTIFY_STATE` and
`TELEGRAM_BOARD_STATE` (message IDs and the last board snapshot). Deleting a
state variable resets that workflow: the board re-baselines and PR cards start
fresh. Never edit them by hand. PR titles, commit subjects, release notes and
board item titles are sent to Telegram, so keep them free of anything private.

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

`main` is protected by a ruleset (enabled once the **Device evidence**
workflow exists on `main`): no direct pushes, no force pushes, linear
history, one approving review with stale approvals dismissed on push, all
review threads resolved, and the **Android checks** and **Device evidence**
status checks required and up to date with `main`. Do not add workflow-level
path filters: docs-only PRs must still report the required checks rather than
leave them pending. Add **Backend checks** (REPO-04) and **Contract checks**
(BE-03) to the required list when those workflows exist.

## 7. Document and release

Update relevant documentation when behavior or setup changes. Add meaningful
user-visible changes to [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`.
Routine internal cleanup does not need a changelog entry.

Merging does not automatically create a release or publish an APK. For now,
only the project owner authorizes releases and public builds. At release
time, assign the version and date, document known limitations and publish
only the explicitly approved artifacts.
