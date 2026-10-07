"""Tagged demo release (release.yml): gate a `v*` tag, write release notes, sign the built APK.

`gate` runs before anything is signed: the tag must name a version assigned in CHANGELOG.md with a
date and known limitations, match the app's versionName, point at a commit merged into main with
green Android checks, and the `release` approval environment must be protected.

`sign` runs only inside the approved `release` environment. It signs with the demo signing secrets
when all of them are set, or with a throwaway debug key when none are, and never with the
production key: production signing belongs to the Telegram APK ledger (docs/release-signing.md).
"""

import argparse
import base64
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from release_preflight import PreflightError, check_android_ci, check_merged_into_main
from release_publish import (
    PublishError, SigningMaterial, badging, sha256_of, sign_apk, signer_cert_sha256, tools_from_env,
    verify_badging,
)
from telegram_api import GitHubApiError, GitHubClient, require_env

ROOT = Path(__file__).resolve().parents[2]
ENVIRONMENT = "release"
TAG_POLICY = [("v*", "tag")]
TAG_RE = re.compile(r"^refs/tags/v(\d+\.\d+\.\d+)$")
DEMO_VERSION_CODE = 1
SECRET_NAMES = (
    "DEMO_KEYSTORE_B64", "DEMO_KEYSTORE_PASSWORD", "DEMO_KEY_ALIAS", "DEMO_KEY_PASSWORD",
)
DEBUG_PASSWORD = "android"   # the Android SDK's well-known debug keystore password, not a secret
DEBUG_ALIAS = "androiddebugkey"
PROVENANCE_SCHEMA = "ovrly-demo-release/v1"


class ReleaseError(RuntimeError):
    pass


def fail(message):
    raise ReleaseError(message)


# --- Gate ------------------------------------------------------------------------------

def tag_version(ref):
    match = TAG_RE.match(ref or "")
    if not match:
        fail(f"Release tags must look like v1.2.3; got {ref!r}")
    return match.group(1)


def app_version_name(gradle_text):
    match = re.search(r'^val appVersionName = "([^"]+)"$', gradle_text, re.MULTILINE)
    if not match:
        fail("android/app/build.gradle.kts does not declare val appVersionName")
    return match.group(1)


def changelog_release(text, version):
    """Return (date, limitations, other_body) for `## [version] - YYYY-MM-DD`."""
    headings = list(re.finditer(r"^## \[([^\]]+)\](.*)$", text, re.MULTILINE))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        suffix = heading.group(2).strip()
        date_match = re.fullmatch(r"- (\d{4}-\d{2}-\d{2})", suffix)
        if not date_match:
            fail(f"CHANGELOG.md heading for {version} must be '## [{version}] - YYYY-MM-DD'")
        try:
            date = datetime.date.fromisoformat(date_match.group(1))
        except ValueError:
            fail(f"CHANGELOG.md date for {version} is not a valid date: {date_match.group(1)}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[heading.end():end].strip("\n")
        limitations, other = split_limitations(body)
        if not re.search(r"^- \S", limitations, re.MULTILINE):
            fail(f"CHANGELOG.md {version} needs a '### Known limitations' list for the release notes")
        return date.isoformat(), limitations, other
    fail(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section; assign the version and date first")


def split_limitations(body):
    sections = re.split(r"(?=^### )", body, flags=re.MULTILINE)
    limitations = [s for s in sections if s.startswith("### Known limitations")]
    other = [s for s in sections if not s.startswith("### Known limitations")]
    text = "".join(limitations)
    return text.partition("\n")[2].strip("\n"), "".join(other).strip("\n")


def release_notes(version, date, limitations, other, source_sha):
    return (
        f"# ovrly {version} ({date})\n\n"
        f"Demo build of `{source_sha}` from `main`, built by the Release workflow from tag `v{version}`. "
        "It is not published automatically; the owner authorizes any release (WORKFLOW.md section 7).\n\n"
        f"## Known limitations\n\n{limitations}\n\n"
        f"## Changes\n\n{other}\n"
    )


def check_environment(github, owner):
    env = github.get_optional(f"environments/{ENVIRONMENT}")
    if env is None:
        fail(f"Environment '{ENVIRONMENT}' does not exist; create it per docs/release-signing.md")
    reviewers = [r for r in env.get("protection_rules", []) if r.get("type") == "required_reviewers"]
    if len(reviewers) != 1:
        fail(f"Environment '{ENVIRONMENT}' must have exactly one required_reviewers rule")
    logins = [(r.get("reviewer") or {}).get("login", "").lower() for r in reviewers[0].get("reviewers", [])]
    if logins != [owner.lower()]:
        fail(f"Environment '{ENVIRONMENT}' must list {owner} as the sole required reviewer; found {logins}")
    if reviewers[0].get("prevent_self_review") is not False:
        fail(
            f"Environment '{ENVIRONMENT}' must allow self-review (prevent_self_review=false) so the owner "
            "can request a build and then approve it"
        )
    policy = env.get("deployment_branch_policy") or {}
    if policy.get("custom_branch_policies") is not True:
        fail(f"Environment '{ENVIRONMENT}' must use custom deployment policies limited to tag v*")
    policies = github.get_optional(f"environments/{ENVIRONMENT}/deployment-branch-policies")
    entries = [(p.get("name"), p.get("type")) for p in (policies or {}).get("branch_policies", [])]
    if entries != TAG_POLICY:
        fail(f"Environment '{ENVIRONMENT}' deployment policies must be exactly [v*/tag]; found {entries}")
    if env.get("can_admins_bypass", "unobserved") is not False:
        fail(f"Environment '{ENVIRONMENT}' must not let administrators bypass its protection rules")


def gate(github, env, root, owner):
    if env.get("GITHUB_EVENT_NAME") != "push":
        fail("Releases run only from a pushed v* tag")
    version = tag_version(env.get("GITHUB_REF"))
    source_sha = env.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        fail("GITHUB_SHA is not a full commit SHA")
    gradle_version = app_version_name((root / "android/app/build.gradle.kts").read_text(encoding="utf-8"))
    if gradle_version != version:
        fail(f"Tag v{version} does not match the app versionName {gradle_version}")
    date, limitations, other = changelog_release((root / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    check_environment(github, owner)
    check_merged_into_main(github, source_sha)
    check_android_ci(github, source_sha)
    return version, release_notes(version, date, limitations, other, source_sha)


# --- Sign ------------------------------------------------------------------------------

def signing_mode(env):
    present = [name for name in SECRET_NAMES if env.get(name)]
    if not present:
        return "debug"
    if len(present) != len(SECRET_NAMES):
        missing = sorted(set(SECRET_NAMES) - set(present))
        fail(f"Demo signing secrets are partly set; also set {missing} or remove all of them")
    return "demo"


def debug_keystore(directory, runner=subprocess.run):
    """A fresh debug key for this run only; every debug-signed release has a different signer."""
    keystore = Path(directory) / "debug.p12"
    result = runner([
        "keytool", "-genkeypair", "-noprompt", "-storetype", "PKCS12", "-keystore", str(keystore),
        "-storepass", DEBUG_PASSWORD, "-keypass", DEBUG_PASSWORD, "-alias", DEBUG_ALIAS,
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000", "-dname", "CN=Android Debug,O=Android,C=US",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"keytool could not create the debug keystore: {result.stderr.strip()[:500]}")
    return base64.b64encode(keystore.read_bytes()).decode("ascii")


def sign(env, tools, unsigned, out_dir, notes, runner=subprocess.run):
    version = tag_version(env.get("GITHUB_REF"))
    source_sha = env["GITHUB_SHA"]
    mode = signing_mode(env)
    pin = (env.get("DEMO_SIGNING_CERT_SHA256") or "").strip().lower()
    if pin and mode != "demo":
        fail("DEMO_SIGNING_CERT_SHA256 is set but the demo signing secrets are not; refusing a debug signature")
    info = badging(tools["aapt2"], unsigned, runner)
    verify_badging(info, DEMO_VERSION_CODE)
    out_dir.mkdir(parents=True, exist_ok=True)
    signed = out_dir / f"ovrly-{version}.apk"
    with tempfile.TemporaryDirectory(prefix="ovrly-debug-key-") as scratch:
        if mode == "demo":
            keystore_b64, alias = env["DEMO_KEYSTORE_B64"], env["DEMO_KEY_ALIAS"]
            store_password, key_password = env["DEMO_KEYSTORE_PASSWORD"], env["DEMO_KEY_PASSWORD"]
        else:
            keystore_b64, alias = debug_keystore(scratch, runner), DEBUG_ALIAS
            store_password = key_password = DEBUG_PASSWORD
        with SigningMaterial(keystore_b64, store_password, key_password) as material:
            sign_apk(tools["apksigner"], unsigned, signed, material.keystore, alias,
                     material.store_pass, material.key_pass, runner)
    cert = signer_cert_sha256(tools["apksigner"], signed, runner)
    if pin and cert != pin:
        fail("The demo signing certificate does not match DEMO_SIGNING_CERT_SHA256")
    provenance = {
        "schema": PROVENANCE_SCHEMA, "package": "app.ovrly", "version_name": version,
        "version_code": DEMO_VERSION_CODE, "source_sha": source_sha, "tag": f"v{version}",
        "signing": mode, "signer_cert_sha256": cert,
        "unsigned_sha256": sha256_of(unsigned), "signed_sha256": sha256_of(signed),
        "run_id": int(env["GITHUB_RUN_ID"]), "run_attempt": int(env["GITHUB_RUN_ATTEMPT"]),
        "workflow_sha": env["GITHUB_WORKFLOW_SHA"],
    }
    (out_dir / "release-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n",
                                                     encoding="utf-8")
    signer = ("demo signing key" if mode == "demo"
              else "a throwaway debug key (demo signing secrets are not configured)")
    build = (
        f"\n## Build\n\n"
        f"- APK: `ovrly-{version}.apk`, versionCode {DEMO_VERSION_CODE}, signed with {signer}\n"
        f"- Signed SHA-256: `{provenance['signed_sha256']}`\n"
        f"- Unsigned SHA-256: `{provenance['unsigned_sha256']}`\n"
        f"- Signer certificate SHA-256: `{cert}`\n"
        f"- Build provenance is attested by GitHub for the unsigned APK, mapping, SBOMs and signed APK.\n"
    )
    (out_dir / "release-notes.md").write_text(notes.rstrip("\n") + "\n" + build, encoding="utf-8")
    return provenance


# --- Entry point -------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate_parser = sub.add_parser("gate")
    gate_parser.add_argument("--notes", type=Path, required=True)
    sign_parser = sub.add_parser("sign")
    sign_parser.add_argument("--unsigned", type=Path, required=True)
    sign_parser.add_argument("--notes", type=Path, required=True)
    sign_parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "gate":
            token, repository, owner = require_env("GITHUB_TOKEN", "GITHUB_REPOSITORY", "RELEASE_OWNER")
            version, notes = gate(GitHubClient(token, repository), os.environ, ROOT, owner)
            args.notes.parent.mkdir(parents=True, exist_ok=True)
            args.notes.write_text(notes, encoding="utf-8")
            with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
                output.write(f"version={version}\n")
            print(f"Release v{version} of {os.environ['GITHUB_SHA'][:12]} passed the gate")
        else:
            provenance = sign(os.environ, tools_from_env(), args.unsigned, args.out,
                              args.notes.read_text(encoding="utf-8"))
            if provenance["signing"] == "debug":
                print("::warning::Demo signing secrets are not configured; the APK is debug-signed")
            print(f"Signed ovrly {provenance['version_name']}: {provenance['signed_sha256']}")
    except (ReleaseError, PreflightError, PublishError, GitHubApiError) as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
