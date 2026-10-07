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

## Tagged demo releases

**Release** (`.github/workflows/release.yml`) answers "which build did we demo": a pushed `v*` tag builds one APK with SBOMs and GitHub build provenance. It never uses the production key, never reserves ledger codes and publishes nothing; the owner still authorizes any release (WORKFLOW.md section 7).

### Cut a release (owner)

1. On a branch, assign the version and date: rename `## [Unreleased]` in `CHANGELOG.md` to `## [X.Y.Z] - YYYY-MM-DD`, keep its `### Known limitations` list current, add a new empty `## [Unreleased]`, and set `appVersionName` in `android/app/build.gradle.kts` to `X.Y.Z`. Merge it through a PR.
2. Tag that merged commit: `git tag -a vX.Y.Z <sha> -m "ovrly X.Y.Z"` and `git push origin vX.Y.Z`.
3. The **Release gate** fails unless the tag is `v<appVersionName>`, the commit is in `main` with green `Android checks`, the changelog section has a date and known limitations, and the `release` environment is protected as below. It writes the release notes with the known limitations first.
4. Two unsigned release builds run on separate runners in different checkout paths without caches; **Compare the two builds** must pass. Provenance is then attested for the unsigned APK, `mapping.txt` and both SBOMs.
5. Approve the `release` deployment. The signing job signs, attests the signed APK and uploads `ovrly-X.Y.Z` (retained 90 days): the signed and unsigned APKs, `mapping.txt`, `ovrly-android.cdx.json`, `ovrly-backend.cdx.json`, `backend-requirements.txt`, `release-notes.md` and `release-provenance.json`.

Check a downloaded file with `gh attestation verify <file> --repo natnael-solomon/ovrly`.

Demo APKs use versionCode 1 and a different signer from Telegram builds, so a device must uninstall one before installing the other.

### SBOMs

- Android: `:app:cyclonedxDirectBom` (CycloneDX Gradle plugin) for the release runtime classpath. See the [Android README](../android/README.md#quality-and-dependencies).
- Backend: `uv export --frozen --no-default-groups --no-emit-project` (runtime requirements with hashes), then `cyclonedx-py requirements` from the locked `release` dependency group.

### Signing

The `sign` job signs with apksigner after approval; application code never sees the secrets. With all four `release` environment secrets set it uses the demo key; with none it creates a throwaway debug key for that run and the notes say so; a partial set fails.

| `release` environment secret | Value |
| --- | --- |
| `DEMO_KEYSTORE_B64` | Base64-encoded demo keystore (JKS or PKCS12) |
| `DEMO_KEYSTORE_PASSWORD` | Store password |
| `DEMO_KEY_ALIAS` | Key alias |
| `DEMO_KEY_PASSWORD` | Key password |

Optional repository variable `DEMO_SIGNING_CERT_SHA256` (64 lowercase hex characters) pins the demo certificate; when it is set, a missing key or a different certificate fails. Generate the demo key like the production key in section 1 but with its own alias and passwords, and never reuse the production keystore here: its codes and issuances belong to the ledger.

### One-time setup

Create environment `release`: required reviewer `natnael-solomon` only, self-review allowed, administrator bypass off, deployment branches and tags set to **Selected** with exactly one tag rule `v*`. The gate checks these through the API. Optionally add the secrets and variable above; without them releases are debug-signed.

### Reproducibility

Pull requests that change the workflow or its helpers, and manual runs from the Actions tab, run both builds and the comparison without the gate, attestation or signing. `apk_reproducibility.py` compares the unsigned APKs and R8 mappings byte for byte and then every ZIP entry and header; `apkanalyzer apk compare` adds a size view. Reviewed exceptions live in `KNOWN_NONDETERMINISM` with their reason, and anything else fails.

Measured result (6 October 2026, PR #123, commit `34c888d`): the two builds produced byte-for-byte identical unsigned APKs (SHA-256 `73997d6243f3e038d4475ef64046b8213ea8c0cca1edd33e3ae0b930b4c5f2a7`) and R8 mappings, so `KNOWN_NONDETERMINISM` is empty. AGP writes fixed ZIP timestamps and entry order, R8 output is deterministic for the same inputs, and the checkout path does not reach the APK. Remaining sources of difference are outside the APK bytes compared here: signatures (a debug key is new on every run, and v2/v3 signing is deterministic only for the same key), the SBOMs' CI run link, and toolchain changes between runs (JDK, SDK build-tools or Gradle dependencies), which the pinned versions and verification metadata keep fixed.
