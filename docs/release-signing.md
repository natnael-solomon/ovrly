# Release signing and distribution setup

Operator runbook for the gated **Telegram APK** workflow described in
[WORKFLOW.md §4](../WORKFLOW.md#telegram-notifications). Everything here is performed by the
project owner (`natnael-solomon`) by hand. Nothing in this document is executed by CI, and the
workflow refuses to run until every prerequisite is verifiably in place.

Read this whole page before doing any step. Do not paste keystores, passwords, recovery phrases or
private keys into chat, issues, pull requests, logs, session transcripts or this repository.

## What is being protected

- **The production signing identity** for `app.ovrly`. Android and Google Play treat the signing
  certificate as the app's identity: a different key means a different app, and installed users
  cannot update across keys. The key generated here is the one later enrolled with Play App
  Signing as the *app-signing key*; Play then recommends a separate *upload key* for day-to-day
  uploads. Do not sign anything with a debug, default, personal or throwaway key.
- **The version-code ledger**. Google Play requires strictly increasing version codes within
  `1..2100000000` and never accepts a code again. The ledger is the single counter for this app;
  it must never be reset or forked.
- **The approval boundary**. Signing secrets exist only in the `production-signing` environment,
  and GitHub injects them into a job only after the owner approves that specific run.

## 1. Generate the production key (offline)

On a machine that is not the CI runner, with the keystore written to removable or encrypted storage
(Bash; on Windows use Git Bash, or run the same `keytool` arguments on one line in PowerShell):

```bash
keytool -genkeypair -v -storetype JKS -keystore ovrly-release.jks -alias ovrly -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=ovrly, O=ovrly, C=ET"
```

- `-storetype JKS` matters: modern `keytool` defaults to PKCS12 regardless of the `.jks` suffix, and
  PKCS12 ignores a key password that differs from the store password. Use two different, long
  passwords for the store and the key. Store them in a password manager entry that is **separate**
  from wherever the keystore backup lives.
- Record the certificate fingerprint; you will need it in step 4:

  ```bash
  keytool -list -v -storetype JKS -keystore ovrly-release.jks -alias ovrly
  ```

  Take the `SHA256:` line and convert it to 64 lowercase hex characters without colons.

## 2. Back up and verify the backup before first use

Two copies matter and they are not the same thing:

| Copy | Where | Purpose |
|---|---|---|
| **Offline backup** | Encrypted archive on media the owner controls, stored apart from the credentials | Recovery. This is the only copy that must survive everything. |
| **CI working copy** | Base64 in the `production-signing` environment secret | Signing. GitHub holds it; it is not offline. |

Before the key signs anything: restore the offline backup to a scratch location and prove the
**private key** unlocks, not just the store. `keytool -list` proves the store password and shows the
certificate; it does not touch the private key. Generate a certificate signing request instead,
which requires the key password and produces nothing installable:

```bash
keytool -certreq -storetype JKS -keystore restored.jks -alias ovrly -file restore-check.csr
keytool -list -v -storetype JKS -keystore restored.jks -alias ovrly
```

`certreq` prompts for the store password and then the key password; a wrong key password is
rejected. The listed `SHA256:` fingerprint must match step 1. Then delete `restored.jks` and
`restore-check.csr`. If the restore or the request fails, do not proceed. Do not sign an APK with
the production key as a test: every production-signed APK must come from the ledger.

## 3. Protect `main`

The **main protection** ruleset (`23894508`) is **active**. As verified when this runbook was
written it requires: pull requests with at least one approving review and stale approvals
dismissed, the required status checks `Android checks` and `Device evidence` from their GitHub
Actions workflows, linear history, resolved review threads, no deletion, no force pushes, and **no
bypass actors**. Preflight reads `GET /repos/{repo}/rules/branches/main` and refuses to run if the
pull request review or the `Android checks` requirement is missing. Do not relax the ruleset to
make a build pass.

## 4. Create the `production-signing` environment

Settings → Environments → New environment → `production-signing`. Configure exactly:

| Setting | Value | Why |
|---|---|---|
| Required reviewers | `natnael-solomon` **only** | Owner approval of each completed build. |
| Prevent self-review | **off** | The owner may request a build and then approve it in a separate step. |
| Deployment branches | **Selected branches and tags** → add branch `main` only | The publish job may only run from `main`. |
| Allow administrators to bypass configured protection rules | **off** | Otherwise the review gate is optional for admins. |
| Wait timer | 0 | Not needed; the mutex serializes publishes. |

Then confirm via the API (`gh api repos/natnael-solomon/ovrly/environments/production-signing`)
that the response shows one `required_reviewers` rule listing only the owner with
`prevent_self_review: false`, `deployment_branch_policy.custom_branch_policies: true`, and
`can_admins_bypass: false`. `can_admins_bypass` is returned by the live API but is not part of
GitHub's published schema; preflight requires it to be observed as exactly `false` and stops with
"cannot verify" if it is absent. Verify the UI setting as well.

Add these **environment** secrets (not repository secrets):

| Secret | Content |
|---|---|
| `RELEASE_KEYSTORE_B64` | `base64 -w0 ovrly-release.jks` (Bash; PowerShell: `[Convert]::ToBase64String([IO.File]::ReadAllBytes("ovrly-release.jks"))`) |
| `RELEASE_KEYSTORE_PASSWORD` | store password |
| `RELEASE_KEY_ALIAS` | `ovrly` |
| `RELEASE_KEY_PASSWORD` | key password |

Add two **repository variables** (public, non-secret):

| Variable | Content |
|---|---|
| `EXPECTED_SIGNING_CERT_SHA256` | the 64-hex fingerprint from step 1 |
| `LEDGER_BOOTSTRAP_TAG_SHA` | the annotated tag object SHA from step 6 |

The publish job refuses to sign if the signer certificate differs from this pin, and, once the
ledger has any issuance, if the pin differs from the certificate already recorded there.

## 5. Protect the ledger namespace

Create a **tag** ruleset (Settings → Rules → New ruleset → tag) named `release ledger`:

- Enforcement: **Active**. Bypass list: **empty**.
- Target tags, include by name, exactly these three patterns as typed in the UI:
  `release-ledger/bootstrap`, `release-ledger/reserve/*`, `release-ledger/issue/*`. No exclusions.
  (The REST API reports them with a `refs/tags/` prefix; do not type that prefix in the UI.)
- Rules: **Restrict deletions**, **Restrict updates**, **Block force pushes**.

Preflight reads the repository's rulesets (`GET /repos/{repo}/rulesets`, then each tag ruleset's
detail) and requires an active tag ruleset whose include list contains exactly
`refs/tags/release-ledger/bootstrap`, `refs/tags/release-ledger/reserve/*` and
`refs/tags/release-ledger/issue/*`, with no excludes, `current_user_can_bypass: never` for the
workflow's own token, an empty bypass list whenever the API returns one (the workflow's
installation token does not receive `bypass_actors`; a user token does), and `deletion`, `update`
and `non_fast_forward` rules. It does not use `rules/branches/{ref}`, which GitHub documents as
branch-only, and it does not attempt to evaluate glob semantics itself; the three explicit patterns
are the contract.

## 6. Initialise the ledger (once)

The ledger starts from the app's shipped local `versionCode` of **1**; the first CI build gets
**2**. CI never creates this record and never guesses a starting point. From an up-to-date clone
of `main`, as the owner:

```bash
git tag -a release-ledger/bootstrap origin/main -m '{"schema":"ovrly-release-ledger/v1","kind":"bootstrap","floor":1,"created_by":"natnael-solomon","reason":"initial ledger baseline; no production releases exist"}'
git push origin refs/tags/release-ledger/bootstrap
git rev-parse refs/tags/release-ledger/bootstrap     # the annotated TAG OBJECT sha, not the commit
```

Put that tag-object SHA in the `LEDGER_BOOTSTRAP_TAG_SHA` repository variable (step 4). Preflight
requires the variable and refuses to run unless the live `bootstrap` ref resolves to exactly that
object; the `created_by` field inside the record is only a label, the pin is what ties the baseline
to your deliberate act. Preflight also validates that the tag is annotated, targets a commit, and
carries exactly this schema, kind and floor. Once the tag ruleset from step 5 is active the record
cannot be changed.

**How CI tags are anchored.** GitHub's built-in workflow token has been reported to create refs
only for the repository's current HEAD; pointing a ref at an older commit appears to need the
`workflows` permission, which that token cannot hold (community discussion 121022; the REST
documentation lists both permission sets without explaining the split). To stay clear of that
limitation each `reserve/<code>` and `issue/<code>` tag targets whatever `main` HEAD was when CI
wrote it and records that as `anchor_sha` in its JSON, alongside the real `source_sha` of the build
and the `workflow_sha` of the helper code. If the write is refused and HEAD has moved, CI re-reads
HEAD and retries once; if HEAD has not moved it fails naming the permission. Do not read the tag
target as the source commit; read the record. Live ledger writes are intentionally unexercised
until this setup exists.

**Backup.** The ledger is Git data; keep at least one full clone (`git clone --mirror`) of the
repository somewhere the owner controls, refreshed after releases. If the namespace is ever lost
or damaged, restore it from that clone. Do not recreate a bootstrap over lost history: the
workflow would then reissue codes Play may already have seen.

## 7. Two-step request and approval

1. Any developer (including the owner) dispatches **Telegram APK** from `main` with `source_sha`
   set to the full SHA of a commit already merged into `main`.
2. Preflight and build run without secrets. When the build finishes, GitHub pauses the publish job
   and notifies the required reviewer.
3. The owner opens the run and checks: the requested SHA, the green `Android checks` on it, the
   unsigned artifact's SHA-256 printed in the build log, and that no newer build has been issued
   meanwhile. Then approves the deployment.
4. The publish job signs, records the issuance, uploads the signed artifact and sends it.

Publishes are serialized by a fixed repository-wide concurrency group; later approvals wait in a
queue of up to 100 and are never cancelled by newer ones. Human approval order does not decide
the outcome: whichever job runs first under the mutex sets the high-water mark, and a build whose
code is not above it is refused at that point.

## 8. Redelivery

If the run fails after the signed artifact was uploaded (for example Telegram was unreachable),
dispatch the workflow with `redeliver_run_id` set to that run's id. Preflight accepts completed
runs that ended in success, failure, cancellation or timeout, as long as the ledger has an
issuance for that code, the code is the current high-water mark, and the signed artifact still
exists and matches the issued digest and certificate. Approval is required again. Nothing is
rebuilt, re-signed or allocated. An older issued build cannot be redelivered; a missing or expired
artifact needs a new build.

## 9. Moving to Google Play later

- Enrol in Play App Signing with **this** key as the app-signing key (upload the keystore through
  Play's encryption tool), then generate and register a separate upload key.
- The first Play version code must be **greater than the highest code in the ledger**, issued or
  merely reserved. Keep issuing codes from this ledger; do not let Play or any other tool start a
  second sequence.
- Telegram-distributed installs signed with this key can update to Play builds. Anything installed
  from a debug build or from the unverified builds that predate the ledger must be uninstalled
  first; that is a deliberate, separately authorised step per device.

## 10. What CI does not do

- It does not create keys, secrets, environments, rulesets or the bootstrap tag.
- It does not securely erase anything. Signing material is written to a `0700` temporary
  directory with `0600` files, deleted when signing ends, on an ephemeral hosted runner.
- It does not promise exactly-once Telegram delivery.
- It does not detect a maliciously rewound ledger. The tag ruleset and the owner's mirror backup
  are the controls for that.
