"""Immutable version-code ledger for production builds, stored as annotated tags.

Namespace layout (all tags are annotated; the tag message is a JSON record):

    refs/tags/release-ledger/bootstrap        -> owner-created baseline; required before any allocation
    refs/tags/release-ledger/reserve/<code>   -> a build reservation, bound to one run attempt
    refs/tags/release-ledger/issue/<code>     -> a signed, verified build that may be exposed

Two facts are kept apart on purpose: a reservation says "this code was handed to a build"; an
issuance says "these exact signed bytes exist under this code". The published high-water mark is
the largest issued code. Nothing here ever updates or deletes a tag.
"""

import json
import re
from dataclasses import dataclass, field

from telegram_api import GitHubApiError

NAMESPACE = "release-ledger"
SCHEMA = "ovrly-release-ledger/v1"
MIN_CODE = 1
MAX_CODE = 2_100_000_000  # Google Play's inclusive ceiling
BOOTSTRAP_FLOOR = 1       # the app shipped versionCode 1 locally; the first CI code is 2
RESERVE_RE = re.compile(rf"^refs/tags/{NAMESPACE}/reserve/(\d+)$")
ISSUE_RE = re.compile(rf"^refs/tags/{NAMESPACE}/issue/(\d+)$")
BOOTSTRAP_REF = f"refs/tags/{NAMESPACE}/bootstrap"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class LedgerError(RuntimeError):
    """The ledger is missing, malformed or would be violated. Never recoverable by retrying."""


class ReferenceExists(LedgerError):
    """Create-ref lost a race; the caller may recompute and retry."""


@dataclass
class Ledger:
    floor: int
    bootstrap: dict
    reservations: dict = field(default_factory=dict)   # code -> record
    issuances: dict = field(default_factory=dict)      # code -> record

    @property
    def high_water_mark(self):
        return max(self.issuances, default=self.floor)

    def next_code(self):
        used = set(self.reservations) | set(self.issuances)
        code = max(used, default=self.floor) + 1
        if code > MAX_CODE:
            raise LedgerError(f"Version code space exhausted: next code {code} exceeds {MAX_CODE}")
        return code


def validate_code(value, what="version code"):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise LedgerError(f"{what} must be an integer, got {value!r}")
    if isinstance(value, str) and not value.isdigit():
        raise LedgerError(f"{what} must be an integer, got {value!r}")
    code = int(value)
    if not MIN_CODE <= code <= MAX_CODE:
        raise LedgerError(f"{what} must be within {MIN_CODE}..{MAX_CODE}, got {code}")
    return code


def _require(record, key, predicate, message):
    if key not in record or not predicate(record[key]):
        raise LedgerError(f"Ledger record {record.get('ref', '?')} is invalid: {message}")


def parse_record(ref, tag_object, expected_kind, expected_code=None):
    """Parse and strictly validate one annotated tag's JSON message."""
    try:
        record = json.loads(tag_object.get("message", ""))
    except ValueError:
        raise LedgerError(f"Ledger record {ref} does not contain valid JSON")
    if not isinstance(record, dict):
        raise LedgerError(f"Ledger record {ref} is not a JSON object")
    record = dict(record, ref=ref, tag_target=(tag_object.get("object") or {}).get("sha"))
    _require(record, "schema", lambda v: v == SCHEMA, f"schema must be {SCHEMA}")
    _require(record, "kind", lambda v: v == expected_kind, f"kind must be {expected_kind}")
    _require(record, "tag_target", lambda v: isinstance(v, str) and SHA_RE.match(v), "tag must target a commit")
    if expected_kind == "bootstrap":
        _require(record, "floor", lambda v: v == BOOTSTRAP_FLOOR, f"floor must be {BOOTSTRAP_FLOOR}")
        _require(record, "created_by", lambda v: isinstance(v, str) and v, "created_by is required")
        return record
    _require(record, "code", lambda v: isinstance(v, int) and v == expected_code, f"code must be {expected_code}")
    _require(record, "source_sha", lambda v: isinstance(v, str) and SHA_RE.match(v), "source_sha must be a full SHA")
    if record["source_sha"] != record["tag_target"]:
        raise LedgerError(f"Ledger record {ref} targets {record['tag_target']} but claims source {record['source_sha']}")
    _require(record, "run_id", lambda v: isinstance(v, int) and v > 0, "run_id must be a positive integer")
    _require(record, "run_attempt", lambda v: isinstance(v, int) and v > 0, "run_attempt must be a positive integer")
    _require(record, "workflow_sha", lambda v: isinstance(v, str) and SHA_RE.match(v), "workflow_sha must be a full SHA")
    if expected_kind == "issue":
        for key in ("unsigned_sha256", "signed_sha256", "cert_sha256"):
            _require(record, key, lambda v: isinstance(v, str) and HEX64_RE.match(v), f"{key} must be a lowercase hex SHA-256")
        _require(record, "package", lambda v: isinstance(v, str) and v, "package is required")
    return record


def load(github, owner_login):
    """Read the whole namespace. Fails closed on a missing bootstrap or any malformed record."""
    refs = list(github.paginate(f"git/matching-refs/tags/{NAMESPACE}/"))
    by_ref = {}
    for entry in refs:
        ref = entry.get("ref", "")
        obj = entry.get("object") or {}
        if obj.get("type") != "tag":
            raise LedgerError(
                f"{ref} is a lightweight tag; ledger records must be annotated tags with a JSON message"
            )
        by_ref[ref] = obj.get("sha")

    if BOOTSTRAP_REF not in by_ref:
        raise LedgerError(
            "Ledger is not initialised: refs/tags/release-ledger/bootstrap is missing. The owner must "
            "create the bootstrap record and the protecting tag ruleset (docs/release-signing.md) "
            "before any production build. CI never initialises the ledger itself."
        )
    bootstrap = parse_record(BOOTSTRAP_REF, github.get(f"git/tags/{by_ref[BOOTSTRAP_REF]}"), "bootstrap")
    if bootstrap["created_by"].lower() != owner_login.lower():
        raise LedgerError(f"Ledger bootstrap was created by {bootstrap['created_by']}, expected {owner_login}")

    ledger = Ledger(floor=bootstrap["floor"], bootstrap=bootstrap)
    for ref, tag_sha in by_ref.items():
        if ref == BOOTSTRAP_REF:
            continue
        reserve, issue = RESERVE_RE.match(ref), ISSUE_RE.match(ref)
        if not (reserve or issue):
            raise LedgerError(f"Unexpected ref in ledger namespace: {ref}")
        code = validate_code((reserve or issue).group(1), f"code in {ref}")
        kind = "reserve" if reserve else "issue"
        record = parse_record(ref, github.get(f"git/tags/{tag_sha}"), kind, code)
        (ledger.reservations if reserve else ledger.issuances)[code] = record
    for code in ledger.issuances:
        if code not in ledger.reservations:
            raise LedgerError(f"Issued code {code} has no reservation; ledger history is incomplete")
    return ledger


def _create_annotated_tag(github, ref_name, target_sha, record, tagger):
    """POST git/tags then POST git/refs. Distinguishes a genuine ref collision from other failures."""
    tag = github.request("POST", "git/tags", {
        "tag": ref_name.removeprefix("refs/tags/"),
        "message": json.dumps(record, sort_keys=True),
        "object": target_sha,
        "type": "commit",
        "tagger": tagger,
    })
    try:
        github.request("POST", "git/refs", {"ref": ref_name, "sha": tag["sha"]})
    except GitHubApiError as error:
        if error.status == 422 and "Reference already exists" in error.message:
            raise ReferenceExists(ref_name) from None
        raise LedgerError(f"Could not create {ref_name}: {error}") from None
    return tag["sha"]


def base_record(kind, code, source_sha, run):
    return {
        "schema": SCHEMA,
        "kind": kind,
        "code": code,
        "source_sha": source_sha,
        "run_id": int(run["run_id"]),
        "run_attempt": int(run["run_attempt"]),
        "workflow_sha": run["workflow_sha"],
        "requested_by": run["actor"],
    }


def reserve(github, ledger, source_sha, run, tagger, attempts=5):
    """Allocate the next code for one build attempt. Same SHA built again gets a new code."""
    for _ in range(attempts):
        code = ledger.next_code()
        record = base_record("reserve", code, source_sha, run)
        try:
            _create_annotated_tag(github, f"refs/tags/{NAMESPACE}/reserve/{code}", source_sha, record, tagger)
        except ReferenceExists:
            # Someone else took this code between our read and write; refresh and go again.
            ledger.reservations[code] = {"code": code, "placeholder": True}
            continue
        ledger.reservations[code] = record
        return code, record
    raise LedgerError(f"Could not reserve a version code after {attempts} collisions")


def issue(github, ledger, code, reservation, publish_run, evidence, tagger):
    """Record a signed, verified build. Must be called before the signed bytes are exposed anywhere."""
    if code in ledger.issuances:
        raise LedgerError(f"Code {code} is already issued; it cannot be issued twice")
    if code <= ledger.high_water_mark:
        raise LedgerError(
            f"Code {code} is not above the issued high-water mark {ledger.high_water_mark}; "
            "this build was superseded during approval. Dispatch a new build for a fresh code."
        )
    record = base_record("issue", code, reservation["source_sha"], publish_run)
    record["reservation"] = {k: reservation[k] for k in ("run_id", "run_attempt", "workflow_sha", "requested_by")}
    record.update(evidence)
    _create_annotated_tag(github, f"refs/tags/{NAMESPACE}/issue/{code}", reservation["source_sha"], record, tagger)
    ledger.issuances[code] = record
    return record
