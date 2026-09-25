"""Trusted preflight for production builds: guards, protections, eligibility, reservation.

Runs with the workflow's own revision checked out and never executes application code. It is the
only place that reserves version codes. Every check fails closed with an actionable message.
"""

import json
import os
import re
import sys
from pathlib import Path

import release_ledger
from telegram_api import GitHubApiError, GitHubClient, require_env

ENVIRONMENT = "production-signing"
ANDROID_WORKFLOW_PATH = ".github/workflows/android.yml"
ANDROID_CHECK_NAME = "Android checks"
RELEASE_WORKFLOW_PATH = ".github/workflows/telegram-apk.yml"
MAIN_REF = "refs/heads/main"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LEDGER_RULESET_INCLUDES = {
    "refs/tags/release-ledger/bootstrap",
    "refs/tags/release-ledger/reserve/*",
    "refs/tags/release-ledger/issue/*",
}
REQUIRED_TAG_RULES = {"deletion", "update", "non_fast_forward"}
BUILD_PROVENANCE_SCHEMA = "ovrly-build-provenance/v1"


class PreflightError(RuntimeError):
    pass


def fail(message):
    raise PreflightError(message)


# --- Context guards -------------------------------------------------------------

def decide_mode(source_sha, redeliver_run_id):
    source_sha = (source_sha or "").strip().lower()
    redeliver = (redeliver_run_id or "").strip()
    if bool(source_sha) == bool(redeliver):
        fail("Provide exactly one of source_sha (a full 40-hex commit) or redeliver_run_id")
    if source_sha:
        if not SHA_RE.match(source_sha):
            fail("source_sha must be a full 40-character lowercase commit SHA, not a branch or tag")
        return "build", source_sha
    if not redeliver.isdigit():
        fail("redeliver_run_id must be a numeric workflow run id")
    return "redeliver", int(redeliver)


def check_execution_context(ctx):
    if ctx["ref"] != MAIN_REF:
        fail(f"Production builds must be dispatched from {MAIN_REF}; this run uses {ctx['ref']}")
    if not ctx["workflow_ref"].endswith(f"@{MAIN_REF}"):
        fail(f"The workflow file must come from {MAIN_REF}; got {ctx['workflow_ref']}")
    if str(ctx["run_attempt"]) != "1":
        fail(
            "Re-running this workflow is not allowed: a rerun cannot reuse a version-code reservation. "
            "Dispatch a new build for a fresh code, or use redeliver_run_id to resend an issued artifact."
        )


# --- Protections ------------------------------------------------------------------

def check_main_ruleset(github):
    rules = github.get("rules/branches/main")
    if not rules:
        fail(
            "main is unprotected (no active rules). Enable the main ruleset requiring pull request "
            "review and the 'Android checks' status before production builds (WORKFLOW.md §4)."
        )
    by_type = {rule["type"]: rule for rule in rules}
    pr = by_type.get("pull_request", {}).get("parameters", {})
    if pr.get("required_approving_review_count", 0) < 1:
        fail("main ruleset must require at least one approving pull request review")
    checks = by_type.get("required_status_checks", {}).get("parameters", {}).get("required_status_checks", [])
    if not any(check.get("context") == ANDROID_CHECK_NAME for check in checks):
        fail(f"main ruleset must require the '{ANDROID_CHECK_NAME}' status check")


def check_ledger_protection(github):
    """The ledger namespace must be immutable for every ref shape we write, with no bypass.

    `rules/branches/{ref}` is documented as branch-only and cannot prove tag protection, so this
    reads the repository's rulesets and requires an ACTIVE tag ruleset whose include list names
    each ledger pattern exactly, with no excludes and no bypass actors.
    """
    for summary in github.paginate("rulesets"):
        if summary.get("target") != "tag" or summary.get("enforcement") != "active":
            continue
        ruleset = github.get(f"rulesets/{summary['id']}")
        # The list view can be stale; the full record is what counts.
        if ruleset.get("target") != "tag" or ruleset.get("enforcement") != "active":
            continue
        ref_name = (ruleset.get("conditions") or {}).get("ref_name") or {}
        includes = set(ref_name.get("include") or [])
        if not LEDGER_RULESET_INCLUDES <= includes:
            continue
        if ref_name.get("exclude"):
            fail(f"Tag ruleset {ruleset['id']} protecting the ledger must have no exclusions")
        # GitHub omits `bypass_actors` from this response for the workflow's installation token
        # (a user token sees it). `current_user_can_bypass` is returned to both and must be "never"
        # for the identity that will write the ledger. When the list is present it must be empty.
        bypass_actors = ruleset.get("bypass_actors")
        can_bypass = ruleset.get("current_user_can_bypass")
        if bypass_actors is not None and bypass_actors != []:
            fail(f"Tag ruleset {ruleset['id']} protecting the ledger must have no bypass actors")
        if can_bypass != "never":
            fail(
                f"Cannot confirm tag ruleset {ruleset['id']} binds this workflow: current_user_can_bypass is "
                f"{can_bypass!r}, expected 'never'"
            )
        if bypass_actors is None:
            print(f"::notice::Tag ruleset {ruleset['id']}: bypass_actors not visible to the workflow token; "
                  "verified via current_user_can_bypass=never")
        present = {rule.get("type") for rule in ruleset.get("rules") or []}
        missing = REQUIRED_TAG_RULES - present
        if missing:
            fail(f"Tag ruleset {ruleset['id']} protecting the ledger lacks {sorted(missing)} rules")
        return
    fail(
        "No active tag ruleset protects the release ledger. Create one whose include list is exactly "
        f"{sorted(LEDGER_RULESET_INCLUDES)} with deletion, update and non-fast-forward blocked and "
        "no bypass actors (docs/release-signing.md §5)."
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
        fail(f"Environment '{ENVIRONMENT}' must use custom branch policies limited to main")
    policies = github.get_optional(f"environments/{ENVIRONMENT}/deployment-branch-policies")
    entries = [(p.get("name"), p.get("type")) for p in (policies or {}).get("branch_policies", [])]
    if entries != [("main", "branch")]:
        fail(f"Environment '{ENVIRONMENT}' branch policies must be exactly [main/branch]; found {entries}")
    # can_admins_bypass is returned by the live API but is not in the published schema. It must be
    # observed as exactly false; an absent or non-boolean value means protection cannot be verified.
    bypass = env.get("can_admins_bypass", "unobserved")
    if bypass is not False:
        fail(
            f"Cannot verify environment '{ENVIRONMENT}' protection: can_admins_bypass is {bypass!r}. "
            "Turn off 'Allow administrators to bypass configured protection rules' and confirm the API reports false."
        )


def check_protections(github, owner):
    check_main_ruleset(github)
    check_ledger_protection(github)
    check_environment(github, owner)


# --- Eligibility (build mode) ------------------------------------------------------

def check_merged_into_main(github, source_sha):
    comparison = github.get(f"compare/main...{source_sha}")
    if comparison.get("status") not in ("identical", "behind"):
        fail(f"Commit {source_sha[:12]} is not merged into main (compare status: {comparison.get('status')})")


def check_android_ci(github, source_sha):
    """The check must be the real Android workflow, identified by app and workflow file, not by name alone."""
    runs = github.get(f"commits/{source_sha}/check-runs?check_name={ANDROID_CHECK_NAME.replace(' ', '%20')}&per_page=50")
    candidates = [
        run for run in runs.get("check_runs", [])
        if (run.get("app") or {}).get("slug") == "github-actions" and run.get("status") == "completed"
    ]
    for check in sorted(candidates, key=lambda c: c.get("completed_at") or "", reverse=True):
        run_id = _run_id_from_details(check.get("details_url", ""))
        if run_id is None:
            continue
        workflow_run = github.get(f"actions/runs/{run_id}")
        if workflow_run.get("path") != ANDROID_WORKFLOW_PATH:
            continue
        if check.get("conclusion") == "success":
            return
        fail(f"'{ANDROID_CHECK_NAME}' on {source_sha[:12]} concluded {check.get('conclusion')}, not success")
    fail(f"No completed '{ANDROID_CHECK_NAME}' from {ANDROID_WORKFLOW_PATH} found on {source_sha[:12]}")


def _run_id_from_details(url):
    match = re.search(r"/actions/runs/(\d+)", url or "")
    return int(match.group(1)) if match else None


# --- Redelivery resolution ---------------------------------------------------------

def resolve_redelivery(github, ledger, run_id, repository, expected_cert):
    """Find the issued artifact a previous publish produced. Failed/cancelled source runs are eligible.

    The run must be this workflow, from main, and the ledger issuance must have been written by that
    exact run and attempt; the artifact is then addressed by id, never by name alone.
    """
    run = github.get_optional(f"actions/runs/{run_id}")
    if run is None:
        fail(f"Workflow run {run_id} was not found in {repository}")
    if (run.get("repository") or {}).get("full_name", "").lower() != repository.lower():
        fail(f"Run {run_id} belongs to {run.get('repository', {}).get('full_name')}, not {repository}")
    if run.get("path") != RELEASE_WORKFLOW_PATH:
        fail(f"Run {run_id} is {run.get('path')}, not the release workflow")
    release_workflow = github.get(f"actions/workflows/{RELEASE_WORKFLOW_PATH.rsplit('/', 1)[1]}")
    if run.get("workflow_id") != release_workflow.get("id"):
        fail(f"Run {run_id} has workflow id {run.get('workflow_id')}, but the release workflow is {release_workflow.get('id')}")
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main":
        fail(f"Run {run_id} was not a workflow_dispatch from main")
    if run.get("status") != "completed":
        fail(f"Run {run_id} is still {run.get('status')}; wait for it to finish before redelivering")
    if run.get("conclusion") not in ("success", "failure", "cancelled", "timed_out"):
        fail(f"Run {run_id} concluded {run.get('conclusion')}; not eligible for redelivery")

    artifacts = [a for a in github.paginate(f"actions/runs/{run_id}/artifacts", key="artifacts")
                 if a.get("name", "").startswith("ovrly-signed-") and not a.get("expired")]
    if len(artifacts) != 1:
        fail(f"Run {run_id} has {len(artifacts)} live signed artifacts; expected exactly one "
             "(expired or missing artifacts cannot be redelivered — dispatch a new build)")
    artifact = artifacts[0]
    match = re.fullmatch(r"ovrly-signed-(\d+)", artifact["name"])
    code = release_ledger.validate_code(match.group(1) if match else None, "artifact code")
    digest = artifact.get("digest") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        fail(f"Artifact {artifact['id']} has no canonical sha256 digest; cannot verify it end to end")
    producer = artifact.get("workflow_run") or {}
    if producer.get("id") != int(run_id) or producer.get("head_sha") != run.get("head_sha") or producer.get("head_branch") != "main":
        fail(f"Artifact {artifact['id']} does not record run {run_id} on main at {run.get('head_sha')} as its producer")
    issued = ledger.issuances.get(code)
    if issued is None:
        fail(f"Code {code} from run {run_id} was never issued in the ledger; nothing to redeliver")
    if issued["run_id"] != int(run_id) or issued["run_attempt"] != int(run.get("run_attempt") or 0):
        fail(f"Ledger says code {code} was issued by run {issued['run_id']} attempt {issued['run_attempt']}, "
             f"not run {run_id} attempt {run.get('run_attempt')}")
    if issued["workflow_sha"] != run.get("head_sha"):
        fail(f"Run {run_id} executed workflow revision {run.get('head_sha')}, but the ledger recorded {issued['workflow_sha']}")
    if code != ledger.high_water_mark:
        fail(
            f"Code {code} is superseded: the current issued version is {ledger.high_water_mark}. "
            "Older releases cannot be redelivered; dispatch a new build if a newer one is needed."
        )
    if issued["cert_sha256"] != expected_cert:
        fail("The issued certificate does not match EXPECTED_SIGNING_CERT_SHA256; the owner must review the key configuration")
    return {
        "code": code,
        "artifact_id": artifact["id"],
        "artifact_digest": digest,
        "source_sha": issued["source_sha"],
        "signed_sha256": issued["signed_sha256"],
        "cert_sha256": issued["cert_sha256"],
        "package": issued["package"],
    }


# --- Entry point -------------------------------------------------------------------

def write_outputs(values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as out:
        for key, value in values.items():
            out.write(f"{key}={value}\n")


def main():
    token, repository, owner = require_env("GITHUB_TOKEN", "GITHUB_REPOSITORY", "RELEASE_OWNER")
    ctx = {
        "ref": os.environ.get("GITHUB_REF", ""),
        "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", ""),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "workflow_sha": os.environ.get("GITHUB_WORKFLOW_SHA", ""),
        "actor": os.environ.get("GITHUB_ACTOR", ""),
    }
    github = GitHubClient(token, repository)
    try:
        mode, target = decide_mode(os.environ.get("INPUT_SOURCE_SHA"), os.environ.get("INPUT_REDELIVER_RUN_ID"))
        check_execution_context(ctx)
        check_protections(github, owner)
        expected_cert = validate_cert_pin(os.environ.get("EXPECTED_SIGNING_CERT_SHA256"))
        bootstrap_pin = os.environ.get("LEDGER_BOOTSTRAP_TAG_SHA")
        ledger = release_ledger.load(github, owner, bootstrap_pin)
        if ledger.issuances and any(rec["cert_sha256"] != expected_cert for rec in ledger.issuances.values()):
            fail("EXPECTED_SIGNING_CERT_SHA256 differs from the certificate already recorded in the ledger")
        if mode == "build":
            check_merged_into_main(github, target)
            check_android_ci(github, target)
            tagger = {"name": "ovrly release ledger", "email": "release-ledger@users.noreply.github.com"}
            code, _ = release_ledger.reserve(github, ledger, target, ctx, tagger, owner, bootstrap_pin)
            write_outputs({"mode": mode, "code": code, "source_sha": target})
            print(f"Reserved version code {code} for {target[:12]}")
        else:
            resolved = resolve_redelivery(github, ledger, target, repository, expected_cert)
            write_outputs({"mode": mode, **resolved, "expected": json.dumps(resolved, sort_keys=True)})
            print(f"Redelivery of code {resolved['code']} from run {target} is eligible")
    except (PreflightError, release_ledger.LedgerError, GitHubApiError) as error:
        print(f"::error::{error}")
        sys.exit(1)


def validate_cert_pin(value):
    pin = (value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        fail("EXPECTED_SIGNING_CERT_SHA256 repository variable must be a 64-hex SHA-256 (docs/release-signing.md §4)")
    return pin


if __name__ == "__main__":
    main()
