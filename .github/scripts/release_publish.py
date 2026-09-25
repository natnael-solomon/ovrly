"""Protected publish step: verify an unsigned build, sign it, record issuance, expose and deliver.

Runs only inside the `production-signing` environment after the owner approves. It checks out the
workflow revision, never the application source, and never runs Gradle. Order of operations is the
security property: verify → sign → verify signature → ISSUE (durable ledger record) → upload → send.
Nothing is exposed before the ledger says it exists.

Redelivery mode resends the exact bytes of the current issued artifact and never signs or allocates.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import release_ledger
from release_preflight import PreflightError, check_execution_context, check_protections
from telegram_api import GitHubApiError, GitHubClient, TelegramClient, display_name, first_line, require_env, truncate
from telegram_notify import GAP, QUOTE_LIMIT, compose, field, link, quote

EXPECTED_PACKAGE = "app.ovrly"
BUILD_PROVENANCE_SCHEMA = "ovrly-build-provenance/v1"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024      # Telegram bot sendDocument limit
CAPTION_LIMIT = 1024                     # Telegram caption limit


class PublishError(RuntimeError):
    pass


def fail(message):
    raise PublishError(message)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- Tool wrappers (injectable for tests) ---------------------------------------------

def run_tool(args, runner=subprocess.run):
    result = runner(args, capture_output=True, text=True)
    if result.returncode != 0:
        # Never echo args: the signing invocation carries password file paths.
        fail(f"{Path(args[0]).name} failed with exit {result.returncode}: {result.stderr.strip()[:500]}")
    return result.stdout


def badging(aapt2, apk, runner=subprocess.run):
    """Package, versionCode and debuggable flag from `aapt2 dump badging`."""
    out = run_tool([aapt2, "dump", "badging", str(apk)], runner)
    match = re.search(r"package: name='([^']+)' versionCode='(\d+)'", out)
    if not match:
        fail("Could not read package/versionCode from aapt2 badging output")
    return {
        "package": match.group(1),
        "version_code": int(match.group(2)),
        "debuggable": "application-debuggable" in out,
    }


def verify_badging(info, expected_code):
    if info["package"] != EXPECTED_PACKAGE:
        fail(f"APK package is {info['package']}, expected {EXPECTED_PACKAGE}")
    if info["version_code"] != expected_code:
        fail(f"APK versionCode is {info['version_code']}, expected reserved code {expected_code}")
    if info["debuggable"]:
        fail("APK is debuggable; production builds must not be")


def signer_cert_sha256(apksigner, apk, runner=subprocess.run):
    out = run_tool([apksigner, "verify", "--print-certs", str(apk)], runner)
    match = re.search(r"Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})", out)
    if not match:
        fail("apksigner verify did not report a signer certificate; the APK may be unsigned")
    return match.group(1)


def sign_apk(apksigner, unsigned, signed, keystore, alias, store_pass_file, key_pass_file, runner=subprocess.run):
    run_tool([
        apksigner, "sign",
        "--ks", str(keystore), "--ks-key-alias", alias,
        "--ks-pass", f"file:{store_pass_file}", "--key-pass", f"file:{key_pass_file}",
        "--v1-signing-enabled", "false", "--v2-signing-enabled", "true", "--v3-signing-enabled", "true",
        "--out", str(signed), str(unsigned),
    ], runner)


# --- Secrets handling ----------------------------------------------------------------

class SigningMaterial:
    """Decodes the keystore and passwords into a private temp dir for the lifetime of the block.

    Controls are restrictive permissions, cleanup and the ephemeral hosted runner; this is not a
    secure-erase and does not claim to be. Any failure while entering removes the directory.
    """

    def __init__(self, keystore_b64, store_password, key_password):
        self._b64 = keystore_b64
        self._store = store_password
        self._key = key_password
        self.dir = None

    def __enter__(self):
        try:
            raw = base64.b64decode(self._b64, validate=True)
        except (ValueError, TypeError):
            fail("RELEASE_KEYSTORE_B64 is not valid base64")
        self.dir = Path(tempfile.mkdtemp(prefix="ovrly-sign-"))
        try:
            os.chmod(self.dir, stat.S_IRWXU)
            self.keystore = self._write("release.jks", raw)
            self.store_pass = self._write("store.pass", self._store.encode())
            self.key_pass = self._write("key.pass", self._key.encode())
        except BaseException:
            self._cleanup()
            raise
        return self

    def _write(self, name, data):
        path = self.dir / name
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(data)
        return path

    def _cleanup(self):
        if self.dir and self.dir.exists():
            shutil.rmtree(self.dir)   # a failure here is worth knowing about; do not swallow it
        self.dir = None

    def __exit__(self, *exc):
        self._cleanup()
        return False


# --- Approval metadata ----------------------------------------------------------------

def approval_metadata(github, run_id, observed_at):
    """Real fields only. GitHub does not return approval timestamps here, so we record when we looked.

    If the audit endpoint is unavailable the record says so explicitly rather than presenting an
    empty list as if nobody had approved.
    """
    try:
        approvals = github.get(f"actions/runs/{run_id}/approvals")
        available = True
    except GitHubApiError as error:
        approvals, available = [], False
        print(f"::warning::Approval audit unavailable (HTTP {error.status}); the native environment gate remains the control")
    return {
        "approvals": [
            {"user": (a.get("user") or {}).get("login"), "state": a.get("state"), "comment": a.get("comment") or ""}
            for a in approvals
        ],
        "approvals_audit_available": available,
        "approvals_observed_at": observed_at,
    }


# --- Caption --------------------------------------------------------------------------

def render_caption(code, version_name, source_sha, subject, actor, repository_url, signed_sha256, redelivery=False):
    headline = link(f"{repository_url}/commit/{source_sha}", f"ovrly {truncate(version_name, 40)} · build {code}")
    text = compose(
        headline,
        quote(truncate(first_line(subject), QUOTE_LIMIT)) if subject else "",
        GAP,
        field("Commit", f"<code>{source_sha[:7]}</code>"),
        field("SHA-256", f"<code>{signed_sha256[:16]}…</code>"),
        field("By", f"<i>{display_name(actor)}</i>"),
        field("Note", "redelivery of an issued build") if redelivery else "",
    )
    if len(text) > CAPTION_LIMIT:
        fail(f"Caption is {len(text)} characters; Telegram allows {CAPTION_LIMIT}")
    return text


# --- Flows ----------------------------------------------------------------------------

def verify_redelivery(ledger, expected, downloaded_apk, tools, expected_cert, runner=subprocess.run):
    """Redeliver mode: the bytes must be exactly what the ledger issued under the current HWM."""
    code = release_ledger.validate_code(expected["code"])
    issued = ledger.issuances.get(code)
    if issued is None:
        fail(f"Code {code} is not issued")
    if code != ledger.high_water_mark:
        fail(f"Code {code} is superseded by issued version {ledger.high_water_mark}; older releases cannot be redelivered")
    expected_cert = (expected_cert or "").lower()
    if issued["cert_sha256"] != expected_cert or expected.get("cert_sha256") != expected_cert:
        fail("Issued certificate, preflight evidence and EXPECTED_SIGNING_CERT_SHA256 do not all agree")
    actual_sha = sha256_of(downloaded_apk)
    if actual_sha != issued["signed_sha256"] or actual_sha != expected["signed_sha256"]:
        fail("Downloaded artifact bytes do not match the ledger's issued digest")
    if signer_cert_sha256(tools["apksigner"], downloaded_apk, runner) != expected_cert:
        fail("Downloaded artifact is not signed by the pinned certificate")
    verify_badging(badging(tools["aapt2"], downloaded_apk, runner), code)
    return {"code": code, "signed_sha256": actual_sha, "cert_sha256": expected_cert, "record": issued}

def publish_new_build(github, ledger, cfg, tools, runner=subprocess.run):
    """Build mode. `cfg` carries paths, expected values and secrets; `tools` the aapt2/apksigner paths."""
    code = release_ledger.validate_code(cfg["code"])
    reservation = ledger.reservations.get(code)
    if reservation is None or reservation.get("placeholder"):
        fail(f"No ledger reservation for code {code}")
    context = {"source_sha": cfg["source_sha"], "run_id": int(cfg["run_id"]),
               "run_attempt": int(cfg["run_attempt"]), "workflow_sha": cfg["workflow_sha"]}
    if any(reservation[k] != v for k, v in context.items()):
        fail(f"Reservation for code {code} was made for a different source, run, attempt or workflow revision")
    if code <= ledger.high_water_mark:
        fail(
            f"Code {code} is not above the issued high-water mark {ledger.high_water_mark}; "
            "this build was superseded during approval. Dispatch a new build."
        )

    unsigned = Path(cfg["unsigned_apk"])
    if not unsigned.is_file():
        fail(f"Unsigned APK not found at {unsigned}")
    size = unsigned.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        fail(f"Unsigned APK is {size / 1_048_576:.1f} MB; Telegram bots can upload at most 50 MB, so this build cannot be delivered")
    unsigned_sha = sha256_of(unsigned)
    provenance = json.loads(Path(cfg["build_provenance"]).read_text(encoding="utf-8"))
    if provenance.get("schema") != BUILD_PROVENANCE_SCHEMA:
        fail(f"Build provenance schema is {provenance.get('schema')!r}, expected {BUILD_PROVENANCE_SCHEMA}")
    if provenance.get("unsigned_sha256") != unsigned_sha or cfg["build_output_sha256"] != unsigned_sha:
        fail("Unsigned artifact digest does not match the build provenance and job output")
    expected_provenance = {"code": code, **context}
    if any(provenance.get(k) != v for k, v in expected_provenance.items()):
        fail("Build provenance does not describe this code, source commit, run, attempt and workflow revision")
    if provenance.get("package") != EXPECTED_PACKAGE:
        fail(f"Build provenance package is {provenance.get('package')!r}")
    verify_badging(badging(tools["aapt2"], unsigned, runner), code)

    expected_cert = cfg["expected_cert_sha256"].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_cert):
        fail("EXPECTED_SIGNING_CERT_SHA256 must be a 64-hex lowercase SHA-256")
    established = {rec["cert_sha256"] for rec in ledger.issuances.values()}
    if established and expected_cert not in established:
        fail(
            "EXPECTED_SIGNING_CERT_SHA256 differs from the certificate recorded in the ledger's issuances. "
            "The production identity cannot change silently; the owner must review the key configuration."
        )

    signed = Path(cfg["signed_apk"])
    with SigningMaterial(cfg["keystore_b64"], cfg["store_password"], cfg["key_password"]) as material:
        sign_apk(tools["apksigner"], unsigned, signed, material.keystore, cfg["key_alias"],
                 material.store_pass, material.key_pass, runner)
    actual_cert = signer_cert_sha256(tools["apksigner"], signed, runner)
    if actual_cert != expected_cert:
        signed.unlink(missing_ok=True)
        fail(f"Signer certificate {actual_cert} does not match EXPECTED_SIGNING_CERT_SHA256")
    verify_badging(badging(tools["aapt2"], signed, runner), code)
    signed_size = signed.stat().st_size
    if signed_size > MAX_UPLOAD_BYTES:
        signed.unlink(missing_ok=True)
        fail(f"Signed APK is {signed_size / 1_048_576:.1f} MB; Telegram bots can upload at most 50 MB")
    signed_sha = sha256_of(signed)

    evidence = {
        "unsigned_sha256": unsigned_sha,
        "signed_sha256": signed_sha,
        "cert_sha256": actual_cert,
        "package": EXPECTED_PACKAGE,
        "version_name": cfg["version_name"],
        **approval_metadata(github, cfg["run_id"], cfg["observed_at"]),
    }
    publish_run = {"run_id": cfg["run_id"], "run_attempt": cfg["run_attempt"], "workflow_sha": cfg["workflow_sha"], "actor": cfg["actor"]}
    record = release_ledger.issue(github, ledger, code, reservation, publish_run, evidence, cfg["tagger"])
    Path(cfg["signed_provenance"]).write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return {"code": code, "signed_sha256": signed_sha, "cert_sha256": actual_cert, "record": record}


def deliver(telegram, apk_path, caption):
    telegram.send_document(apk_path, caption, silent=False)


# --- Entry point ---------------------------------------------------------------------

def tools_from_env():
    """Resolve aapt2 and apksigner from the PINNED build-tools version only; never the newest installed."""
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not home:
        fail("ANDROID_HOME is not set; apksigner and aapt2 are required")
    version = os.environ.get("BUILD_TOOLS_VERSION", "36.0.0")
    tools = Path(home) / "build-tools" / version
    suffixes = (".bat", ".exe", "") if os.name == "nt" else ("",)

    def locate(name):
        for suffix in suffixes:
            candidate = tools / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
        fail(f"{name} not found in build-tools {version} under {tools}")

    return {"apksigner": locate("apksigner"), "aapt2": locate("aapt2"), "version": version}


def execution_context_from_env(env=os.environ):
    return {
        "ref": env.get("GITHUB_REF", ""),
        "workflow_ref": env.get("GITHUB_WORKFLOW_REF", ""),
        "run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
    }


def guard_execution_context(env=os.environ):
    """Refuse partial reruns and non-main contexts before touching network, tools or secrets.

    'Re-run failed jobs' can restart only this job after preflight already passed, bypassing
    preflight's attempt guard; both modes must therefore re-check here.
    """
    check_execution_context(execution_context_from_env(env))


def main():
    mode = os.environ.get("MODE")
    if mode not in ("build", "redeliver"):
        fail_and_exit("MODE must be build or redeliver")
    try:
        guard_execution_context()
    except PreflightError as error:
        fail_and_exit(str(error))
    token, repository, owner, bot_token, chat_id = require_env(
        "GITHUB_TOKEN", "GITHUB_REPOSITORY", "RELEASE_OWNER", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
    )
    github = GitHubClient(token, repository)
    telegram = TelegramClient(bot_token, chat_id)
    repository_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}"
    try:
        # Protections may have changed while waiting for approval; check again under the publish mutex.
        check_protections(github, owner)
        ledger = release_ledger.load(github, owner, os.environ.get("LEDGER_BOOTSTRAP_TAG_SHA"))
        tools = tools_from_env()
        if mode == "build":
            (keystore_b64, store_password, key_alias, key_password, expected_cert) = require_env(
                "RELEASE_KEYSTORE_B64", "RELEASE_KEYSTORE_PASSWORD", "RELEASE_KEY_ALIAS",
                "RELEASE_KEY_PASSWORD", "EXPECTED_SIGNING_CERT_SHA256",
            )
            cfg = {
                "code": os.environ["CODE"], "source_sha": os.environ["SOURCE_SHA"],
                "run_id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
                "workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"], "actor": os.environ["GITHUB_ACTOR"],
                "unsigned_apk": os.environ["UNSIGNED_APK"], "signed_apk": os.environ["SIGNED_APK"],
                "build_provenance": os.environ["BUILD_PROVENANCE"], "signed_provenance": os.environ["SIGNED_PROVENANCE"],
                "build_output_sha256": os.environ["BUILD_OUTPUT_SHA256"], "version_name": os.environ.get("VERSION_NAME", ""),
                "keystore_b64": keystore_b64, "store_password": store_password, "key_alias": key_alias,
                "key_password": key_password, "expected_cert_sha256": expected_cert,
                "observed_at": os.environ["OBSERVED_AT"],
                "tagger": {"name": "ovrly release ledger", "email": "release-ledger@users.noreply.github.com"},
            }
            result = publish_new_build(github, ledger, cfg, tools)
            # The workflow uploads the signed artifact between this script and `--deliver`; see the YAML.
            write_outputs({"code": result["code"], "signed_sha256": result["signed_sha256"]})
            print(f"Issued code {result['code']} ({result['signed_sha256'][:16]}…); ready to expose")
            return
        expected = json.loads(os.environ["EXPECTED"])
        apk = os.environ["DOWNLOADED_APK"]
        (expected_cert,) = require_env("EXPECTED_SIGNING_CERT_SHA256")
        result = verify_redelivery(ledger, expected, apk, tools, expected_cert)
        caption = render_caption(result["code"], result["record"].get("version_name", ""), result["record"]["source_sha"],
                                 os.environ.get("COMMIT_SUBJECT", ""), result["record"].get("requested_by", ""),
                                 repository_url, result["signed_sha256"], redelivery=True)
        deliver(telegram, apk, caption)
        print(f"Redelivered code {result['code']}")
    except (PublishError, PreflightError, release_ledger.LedgerError, GitHubApiError) as error:
        fail_and_exit(str(error))


def deliver_main():
    """Second entry point, run after the signed artifact is uploaded: send the same bytes."""
    try:
        guard_execution_context()
    except PreflightError as error:
        fail_and_exit(str(error))
    bot_token, chat_id, repository = require_env("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GITHUB_REPOSITORY")
    record = json.loads(Path(os.environ["SIGNED_PROVENANCE"]).read_text(encoding="utf-8"))
    apk = Path(os.environ["SIGNED_APK"])
    if sha256_of(apk) != record["signed_sha256"]:
        fail_and_exit("Signed APK changed between issuance and delivery")
    repository_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}"
    caption = render_caption(record["code"], record.get("version_name", ""), record["source_sha"],
                             os.environ.get("COMMIT_SUBJECT", ""), record.get("requested_by", ""),
                             repository_url, record["signed_sha256"])
    deliver(TelegramClient(bot_token, chat_id), apk, caption)
    print(f"Delivered code {record['code']}")


def write_outputs(values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        for key, value in values.items():
            out.write(f"{key}={value}\n")


def fail_and_exit(message):
    print(f"::error::{message}")
    sys.exit(1)


if __name__ == "__main__":
    deliver_main() if "--deliver" in sys.argv else main()
