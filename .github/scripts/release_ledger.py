"""Immutable version-code ledger for production builds, stored as annotated tags.

Namespace layout (all tags are annotated; the tag message is a JSON record):

    refs/tags/release-ledger/bootstrap        -> owner-created baseline; required before any allocation
    refs/tags/release-ledger/reserve/<code>   -> a build reservation, bound to one run attempt
    refs/tags/release-ledger/issue/<code>     -> a signed, verified build that may be exposed

Two facts are kept apart on purpose: a reservation says "this code was handed to a build"; an
issuance says "these exact signed bytes exist under this code". The published high-water mark is
the largest issued code. Nothing here ever updates or deletes a tag.

Tag targets: GITHUB_TOKEN with contents:write can only create refs that point at the repository's
current HEAD (community evidence: discussion 121022); other commits need the `workflows` permission,
which the token cannot hold. CI therefore anchors each ledger tag to the main HEAD observed at write
time and records that anchor, the source commit and the writing workflow revision as explicit JSON
fields. Source provenance lives in the record, not in the tag target. Live ledger writes are
intentionally untested until the owner has provisioned the bootstrap and protections.
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
CANONICAL_PACKAGE = "app.ovrly"
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


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_sha(value):
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _is_hex64(value):
    return isinstance(value, str) and HEX64_RE.fullmatch(value) is not None


def validate_code(value, what="version code"):
    """Accept an int or its canonical decimal string; reject bools, signs, leading zeros and whitespace."""
    if _is_int(value):
        code = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and str(int(value)) == value:
        code = int(value)
    else:
        raise LedgerError(f"{what} must be a canonical decimal integer, got {value!r}")
    if not MIN_CODE <= code <= MAX_CODE:
        raise LedgerError(f"{what} must be within {MIN_CODE}..{MAX_CODE}, got {code}")
    return code


def _require(record, key, predicate, message):
    if key not in record or not predicate(record[key]):
        raise LedgerError(f"Ledger record {record.get('ref', '?')} is invalid: {message}")


def parse_record(ref, tag_object, expected_kind, expected_code=None):
    """Parse and strictly validate one annotated tag's JSON message."""
    target = tag_object.get("object") or {}
    if target.get("type") != "commit":
        raise LedgerError(f"Ledger record {ref} must tag a commit, not a {target.get('type')!r}")
    try:
        record = json.loads(tag_object.get("message", ""))
    except ValueError:
        raise LedgerError(f"Ledger record {ref} does not contain valid JSON")
    if not isinstance(record, dict):
        raise LedgerError(f"Ledger record {ref} is not a JSON object")
    record = dict(record, ref=ref, tag_target=target.get("sha"))
    _require(record, "schema", lambda v: v == SCHEMA, f"schema must be {SCHEMA}")
    _require(record, "kind", lambda v: v == expected_kind, f"kind must be {expected_kind}")
    _require(record, "tag_target", _is_sha, "tag must target a commit")
    if expected_kind == "bootstrap":
        _require(record, "floor", lambda v: _is_int(v) and v == BOOTSTRAP_FLOOR, f"floor must be {BOOTSTRAP_FLOOR}")
        _require(record, "created_by", lambda v: isinstance(v, str) and v.strip() == v and v, "created_by is required")
        return record
    _require(record, "code", lambda v: _is_int(v) and v == expected_code, f"code must be {expected_code}")
    _require(record, "source_sha", _is_sha, "source_sha must be a full lowercase SHA")
    _require(record, "run_id", lambda v: _is_int(v) and v > 0, "run_id must be a positive integer")
    _require(record, "run_attempt", lambda v: _is_int(v) and v > 0, "run_attempt must be a positive integer")
    _require(record, "workflow_sha", _is_sha, "workflow_sha must be a full lowercase SHA")
    # The tag anchors to whatever main HEAD was when the record was written; the record says which.
    _require(record, "anchor_sha", _is_sha, "anchor_sha must be a full lowercase SHA")
    if record["anchor_sha"] != record["tag_target"]:
        raise LedgerError(
            f"Ledger record {ref} targets {record['tag_target']} but claims anchor {record['anchor_sha']}"
        )
    if expected_kind == "issue":
        for key in ("unsigned_sha256", "signed_sha256", "cert_sha256"):
            _require(record, key, _is_hex64, f"{key} must be a lowercase hex SHA-256")
        _require(record, "package", lambda v: v == CANONICAL_PACKAGE, f"package must be {CANONICAL_PACKAGE}")
        _require(record, "reservation", lambda v: isinstance(v, dict), "reservation tuple is required")
        for key in ("run_id", "run_attempt"):
            if not _is_int(record["reservation"].get(key)):
                raise LedgerError(f"Ledger record {ref} reservation.{key} must be an integer")
        if not _is_sha(record["reservation"].get("workflow_sha")):
            raise LedgerError(f"Ledger record {ref} reservation.workflow_sha must be a full SHA")
    return record


def _check_issuance_matches_reservation(code, issued, reservation):
    """Preflight, build and publish all belong to one run, so both tuples must equal the reservation."""
    keys = ("run_id", "run_attempt", "workflow_sha")
    expected = {k: reservation[k] for k in keys}
    nested = {k: issued["reservation"].get(k) for k in keys}
    own = {k: issued.get(k) for k in keys}
    if nested != expected or own != expected or issued["source_sha"] != reservation["source_sha"]:
        raise LedgerError(
            f"Issued code {code} does not match its reservation (source, run, attempt or workflow differ)"
        )


def load(github, owner_login, bootstrap_tag_sha):
    """Read the whole namespace. Fails closed on a missing bootstrap or any malformed record.

    `bootstrap_tag_sha` pins the exact annotated tag object the owner created (public configuration,
    like the certificate pin). It is required: the tag ruleset keeps the record immutable, but only
    the pin ties it to the owner's deliberate act rather than to a self-asserted `created_by`.
    """
    by_ref = {}
    for entry in github.paginate(f"git/matching-refs/tags/{NAMESPACE}/"):
        ref = entry.get("ref", "")
        obj = entry.get("object") or {}
        if obj.get("type") != "tag":
            raise LedgerError(
                f"{ref} is a lightweight tag; ledger records must be annotated tags with a JSON message"
            )
        if ref in by_ref:
            raise LedgerError(f"Duplicate ref {ref} returned by the API")
        by_ref[ref] = obj.get("sha")

    if BOOTSTRAP_REF not in by_ref:
        raise LedgerError(
            "Ledger is not initialised: refs/tags/release-ledger/bootstrap is missing. The owner must "
            "create the bootstrap record and the protecting tag ruleset (docs/release-signing.md) "
            "before any production build. CI never initialises the ledger itself."
        )
    pin = (bootstrap_tag_sha or "").strip().lower()
    if not _is_sha(pin):
        raise LedgerError(
            "LEDGER_BOOTSTRAP_TAG_SHA repository variable is missing or malformed. The owner must pin the "
            "annotated bootstrap tag object SHA (docs/release-signing.md §6); a created_by field inside "
            "the record is a self-assertion and does not prove who created it."
        )
    if by_ref[BOOTSTRAP_REF] != pin:
        raise LedgerError(
            f"Bootstrap tag object {by_ref[BOOTSTRAP_REF]} does not match the pinned LEDGER_BOOTSTRAP_TAG_SHA {pin}"
        )
    bootstrap = parse_record(BOOTSTRAP_REF, github.get(f"git/tags/{pin}"), "bootstrap")
    if bootstrap["created_by"].lower() != owner_login.lower():
        raise LedgerError(f"Ledger bootstrap names {bootstrap['created_by']}, expected {owner_login}")

    ledger = Ledger(floor=bootstrap["floor"], bootstrap=bootstrap)
    for ref, tag_sha in by_ref.items():
        if ref == BOOTSTRAP_REF:
            continue
        reserve, issue = RESERVE_RE.fullmatch(ref), ISSUE_RE.fullmatch(ref)
        if not (reserve or issue):
            raise LedgerError(f"Unexpected ref in ledger namespace: {ref}")
        code = validate_code((reserve or issue).group(1), f"code in {ref}")
        if code <= ledger.floor:
            raise LedgerError(f"{ref} uses code {code}, which is not above the bootstrap floor {ledger.floor}")
        kind = "reserve" if reserve else "issue"
        record = parse_record(ref, github.get(f"git/tags/{tag_sha}"), kind, code)
        bucket = ledger.reservations if reserve else ledger.issuances
        if code in bucket:
            raise LedgerError(f"Code {code} appears twice as {kind}")
        bucket[code] = record
    for code, issued in ledger.issuances.items():
        reservation = ledger.reservations.get(code)
        if reservation is None:
            raise LedgerError(f"Issued code {code} has no reservation; ledger history is incomplete")
        _check_issuance_matches_reservation(code, issued, reservation)
    certs = {rec["cert_sha256"] for rec in ledger.issuances.values()}
    if len(certs) > 1:
        raise LedgerError(f"Ledger issuances carry {len(certs)} different signing certificates; expected one identity")
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
        if error.status in (403, 404) and "not accessible by integration" in error.message.lower():
            raise AnchorMoved(ref_name) from None
        raise LedgerError(f"Could not create {ref_name}: {error}") from None
    return tag["sha"]


class AnchorMoved(LedgerError):
    """Create-ref was refused for the anchor commit; main HEAD may have moved since we read it."""


def main_head(github):
    sha = ((github.get("git/ref/heads/main") or {}).get("object") or {}).get("sha", "")
    if not _is_sha(sha):
        raise LedgerError("Could not read the current main HEAD to anchor the ledger record")
    return sha


def base_record(kind, code, source_sha, run, anchor_sha):
    return {
        "schema": SCHEMA,
        "kind": kind,
        "code": code,
        "source_sha": source_sha,
        "anchor_sha": anchor_sha,
        "run_id": int(run["run_id"]),
        "run_attempt": int(run["run_attempt"]),
        "workflow_sha": run["workflow_sha"],
        "requested_by": run["actor"],
    }


def _write_anchored(github, ref_name, build_record, tagger, attempts=3):
    """Anchor to main HEAD observed now; retry only when HEAD verifiably moved between read and write."""
    anchor = main_head(github)
    for _ in range(attempts):
        record = build_record(anchor)
        try:
            return _create_annotated_tag(github, ref_name, anchor, record, tagger), record
        except AnchorMoved:
            fresh = main_head(github)
            if fresh == anchor:
                raise LedgerError(
                    f"Could not create {ref_name} anchored to main HEAD {anchor}; the token was refused "
                    "although HEAD did not move. Check the workflow's contents:write permission."
                )
            anchor = fresh
    raise LedgerError(f"main HEAD kept moving while writing {ref_name}; giving up after {attempts} attempts")


def reserve(github, ledger, source_sha, run, tagger, owner_login, bootstrap_tag_sha, attempts=5):
    """Allocate the next code for one build attempt. Same SHA built again gets a new code.

    On a ref collision the whole ledger is reloaded so contention that jumped far ahead does not
    burn every retry guessing occupied codes one by one.
    """
    for _ in range(attempts):
        code = ledger.next_code()
        ref_name = f"refs/tags/{NAMESPACE}/reserve/{code}"
        try:
            _, record = _write_anchored(
                github, ref_name, lambda anchor: base_record("reserve", code, source_sha, run, anchor), tagger
            )
        except ReferenceExists:
            fresh = load(github, owner_login, bootstrap_tag_sha)
            ledger.reservations, ledger.issuances = fresh.reservations, fresh.issuances
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

    def build(anchor):
        record = base_record("issue", code, reservation["source_sha"], publish_run, anchor)
        record["reservation"] = {k: reservation[k] for k in ("run_id", "run_attempt", "workflow_sha", "requested_by")}
        record.update(evidence)
        return record

    try:
        _, record = _write_anchored(github, f"refs/tags/{NAMESPACE}/issue/{code}", build, tagger)
    except ReferenceExists:
        raise LedgerError(f"Issue record for code {code} already exists; refusing to overwrite") from None
    ledger.issuances[code] = record
    return record
