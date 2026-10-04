# Release signing

**Telegram APK** builds, signs and delivers a release after owner approval. It does not create a GitHub Release. Never share signing keys or passwords through Git, chat or logs.

## Request, approve or redeliver

1. After fetching, run `scripts/request-build.ps1` or `sh scripts/request-build.sh`. Alternatively, dispatch **Telegram APK** from `main` with a merged commit's full 40-character `source_sha`.
2. Before approving `production-signing`, the owner checks the source SHA, green `Android checks`, unsigned APK hash against provenance, and whether a newer build superseded it. Reruns and older codes are rejected.
3. If delivery failed after artifact upload, dispatch with `redeliver_run_id`. Approval is required again. Only the current issued artifact can be resent, unchanged and without rebuilding or signing. Missing/expired artifacts require a new build.

Artifacts are public and retained for 90 days. Telegram timeouts can cause duplicate deliveries. Full device/update acceptance follows [WORKFLOW.md](../WORKFLOW.md#7-document-and-release).

## One-time owner setup

For a configured repository, do not repeat key generation or bootstrap creation. Preflight verifies setup on every run; never weaken protection to pass it.

## 1. Generate the production key (offline)

Create one JKS outside the repository, with different store/key passwords:

```bash
keytool -genkeypair -v -storetype JKS -keystore ovrly-release.jks -alias ovrly -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=ovrly, O=ovrly, C=ET"
```

Record its certificate fingerprint:

```bash
keytool -list -v -storetype JKS -keystore ovrly-release.jks -alias ovrly
```

Keep this identity for `app.ovrly` updates. Never substitute a debug or temporary key.

## 2. Back up and verify the backup before first use

Keep an encrypted offline backup separate from its passwords. Restore to scratch storage, unlock the private key and match the fingerprint:

```bash
keytool -certreq -storetype JKS -keystore restored.jks -alias ovrly -file restore-check.csr
keytool -list -v -storetype JKS -keystore restored.jks -alias ovrly
```

The wrong key password must fail; `keytool -list` alone does not test it. Delete the restored key and CSR afterwards. Stop on any failure. Never test-sign an APK outside the ledger.

## 3. Protect `main`

Require PRs, one approval, stale-approval dismissal, `Android checks`, `Device evidence`, `Backend checks`, `Backend recovery`, `Contract checks`, `Quality checks`, linear history and resolved threads. Block deletion/force pushes and allow no bypass actors.

## 4. Create the `production-signing` environment

Reviewer: `natnael-solomon` only. Allow self-review; restrict deployment to branch `main`; disable administrator bypass; set no wait timer. Verify custom branch policy and `can_admins_bypass: false` in the API as well as the UI.

| Environment secret | Value |
| --- | --- |
| `RELEASE_KEYSTORE_B64` | Base64-encoded JKS |
| `RELEASE_KEYSTORE_PASSWORD` | Store password |
| `RELEASE_KEY_ALIAS` | `ovrly` |
| `RELEASE_KEY_PASSWORD` | Key password |

| Repository variable | Value |
| --- | --- |
| `EXPECTED_SIGNING_CERT_SHA256` | Certificate fingerprint: 64 lowercase hex characters, no colons |
| `LEDGER_BOOTSTRAP_TAG_SHA` | Annotated tag-object SHA from section 6 |

The signer must match the pin and existing issuance history.

## 5. Protect the ledger namespace

Use an active tag ruleset with exactly these UI patterns:

- `release-ledger/bootstrap`
- `release-ledger/reserve/*`
- `release-ledger/issue/*`

Block updates, deletion and force pushes. No exclusions or bypass actors. The API prefixes patterns with `refs/tags/`; do not enter that prefix in the UI. Preflight requires `current_user_can_bypass: never` and an empty `bypass_actors` list when returned.

## 6. Initialise the ledger (once)

Only for a new ledger, from an up-to-date clone, as owner:

```bash
git tag -a release-ledger/bootstrap origin/main -m '{"schema":"ovrly-release-ledger/v1","kind":"bootstrap","floor":1,"created_by":"natnael-solomon","reason":"initial ledger baseline; no production releases exist"}'
git push origin refs/tags/release-ledger/bootstrap
git rev-parse refs/tags/release-ledger/bootstrap     # the annotated TAG OBJECT sha, not the commit
```

Pin that tag-object SHA in `LEDGER_BOOTSTRAP_TAG_SHA`. Local builds default to code 1; ledger builds start at 2 and advance beyond every reserved or issued code, within `1..2100000000`. Failed builds leave gaps. Never reset or reuse codes.

Read `source_sha` inside ledger records for the build commit. Their tag target is the `main` HEAD at write time.

## Recovery

Keep an owner-controlled mirror (`git clone --mirror`) updated after releases. Restore missing ledger history and the original key from backups; never recreate the bootstrap over lost history. Stop if recovery fails.

For Google Play, retain the signing identity and ledger, use a separate upload key and choose a code above all reservations/issuances.
