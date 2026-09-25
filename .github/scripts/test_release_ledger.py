import json
import unittest

from release_ledger import (
    BOOTSTRAP_REF, LedgerError, MAX_CODE, SCHEMA, issue, load, parse_record, reserve, validate_code,
)
from telegram_api import GitHubApiError

SHA_A = "a" * 40
SHA_B = "b" * 40
WF = "c" * 40
HEAD = "d" * 40
HEAD2 = "e" * 40
HEX = "0" * 64
OWNER = "natnael-solomon"
BOOT_TAG = "f" * 40
RUN = {"run_id": 10, "run_attempt": 1, "workflow_sha": WF, "actor": "dev"}
TAGGER = {"name": "ci", "email": "ci@example.invalid"}
RESERVATION_TUPLE = {"run_id": 10, "run_attempt": 1, "workflow_sha": WF, "requested_by": "dev"}


def rec(kind, code=None, anchor=HEAD, **extra):
    base = {"schema": SCHEMA, "kind": kind}
    if kind == "bootstrap":
        base.update(floor=1, created_by=OWNER)
    else:
        base.update(code=code, source_sha=SHA_A, anchor_sha=anchor, run_id=10, run_attempt=1,
                    workflow_sha=WF, requested_by="dev")
    if kind == "issue":
        base.update(unsigned_sha256=HEX, signed_sha256=HEX, cert_sha256=HEX, package="app.ovrly",
                    reservation=dict(RESERVATION_TUPLE))
    base.update(extra)
    return base


class FakeGitHub:
    """Serves annotated tags with explicit tag-object SHAs, a moving main HEAD, and scripted failures."""

    def __init__(self, tags=None, lightweight=(), collide_once=(), head=HEAD, refuse_anchor_once=False):
        self.tags = dict(tags or {})          # ref -> (tag_sha, target_sha, target_type, record)
        self.lightweight = set(lightweight)
        self.collide_once = set(collide_once)
        self.head = head
        self.refuse_anchor_once = refuse_anchor_once
        self.created = []
        self.loads = 0

    def paginate(self, path, key=None):
        assert path.startswith("git/matching-refs/tags/release-ledger/")
        self.loads += 1
        for ref, (tag_sha, *_rest) in sorted(self.tags.items()):
            yield {"ref": ref, "object": {"sha": tag_sha, "type": "tag"}}
        for ref in self.lightweight:
            yield {"ref": ref, "object": {"sha": SHA_B, "type": "commit"}}

    def get(self, path):
        if path == "git/ref/heads/main":
            return {"object": {"sha": self.head, "type": "commit"}}
        assert path.startswith("git/tags/")
        wanted = path.split("/")[-1]
        for tag_sha, target, target_type, record in self.tags.values():
            if tag_sha == wanted:
                message = json.dumps(record) if isinstance(record, dict) else record
                return {"message": message, "object": {"sha": target, "type": target_type}}
        raise GitHubApiError(404, "Not Found", "GET", path)

    def request(self, method, path, payload=None, ok=(200, 201)):
        assert method == "POST"
        if path == "git/tags":
            self.created.append(("tag", payload))
            return {"sha": f"newtag{len(self.created):034d}"}
        if path == "git/refs":
            if payload["ref"] in self.collide_once:
                self.collide_once.discard(payload["ref"])
                raise GitHubApiError(422, "Reference already exists", "POST", path)
            if self.refuse_anchor_once:
                self.refuse_anchor_once = False
                raise GitHubApiError(403, "Resource not accessible by integration", "POST", path)
            self.created.append(("ref", payload))
            return {"ref": payload["ref"]}
        raise AssertionError(path)


def bootstrap_only(target_type="commit", record=None):
    return {BOOTSTRAP_REF: (BOOT_TAG, SHA_A, target_type, record or rec("bootstrap"))}


def with_tags(*entries):
    tags = bootstrap_only()
    for i, (ref, record) in enumerate(entries):
        tags[ref] = (f"tag{i:037d}", record["anchor_sha"], "commit", record)
    return tags


def R(code, **kw):
    return f"refs/tags/release-ledger/reserve/{code}", rec("reserve", code, **kw)


def I(code, **kw):
    return f"refs/tags/release-ledger/issue/{code}", rec("issue", code, **kw)


class ValidationTest(unittest.TestCase):
    def test_bounds_inclusive_and_canonical(self):
        self.assertEqual(validate_code(1), 1)
        self.assertEqual(validate_code(MAX_CODE), MAX_CODE)
        self.assertEqual(validate_code("42"), 42)
        for bad in (0, MAX_CODE + 1, "x", None, 1.5, True, "042", "+3", " 3", "3\n", "３"):
            with self.subTest(bad=bad), self.assertRaises(LedgerError):
                validate_code(bad)

    def test_parse_rejects_malformed_records(self):
        good = {"message": json.dumps(rec("reserve", 2)), "object": {"sha": HEAD, "type": "commit"}}
        self.assertEqual(parse_record("r", good, "reserve", 2)["code"], 2)
        commit = {"sha": HEAD, "type": "commit"}
        cases = {
            "not json": ({"message": "nope", "object": commit}, "reserve", 2),
            "wrong schema": ({"message": json.dumps(rec("reserve", 2, schema="x")), "object": commit}, "reserve", 2),
            "wrong code": ({"message": json.dumps(rec("reserve", 3)), "object": commit}, "reserve", 2),
            "anchor mismatch": ({"message": json.dumps(rec("reserve", 2, anchor=HEAD2)), "object": commit}, "reserve", 2),
            "blob target": ({"message": json.dumps(rec("bootstrap")), "object": {"sha": SHA_A, "type": "blob"}}, "bootstrap", None),
            "bool floor": ({"message": json.dumps(rec("bootstrap", floor=True)), "object": {"sha": SHA_A, "type": "commit"}}, "bootstrap", None),
            "bool run_id": ({"message": json.dumps(rec("reserve", 2, run_id=True)), "object": commit}, "reserve", 2),
            "newline sha": ({"message": json.dumps(rec("reserve", 2, workflow_sha=WF + "\n")), "object": commit}, "reserve", 2),
            "uppercase sha": ({"message": json.dumps(rec("reserve", 2, source_sha=SHA_A.upper())), "object": commit}, "reserve", 2),
            "short cert": ({"message": json.dumps(rec("issue", 2, cert_sha256="short")), "object": commit}, "issue", 2),
            "wrong package": ({"message": json.dumps(rec("issue", 2, package="com.other")), "object": commit}, "issue", 2),
            "missing reservation": ({"message": json.dumps({k: v for k, v in rec("issue", 2).items() if k != "reservation"}), "object": commit}, "issue", 2),
            "bad floor": ({"message": json.dumps(rec("bootstrap", floor=1000)), "object": {"sha": SHA_A, "type": "commit"}}, "bootstrap", None),
        }
        for name, (obj, kind, code) in cases.items():
            with self.subTest(name=name), self.assertRaises(LedgerError):
                parse_record("r", obj, kind, code)


class LoadTest(unittest.TestCase):
    def test_wholly_missing_namespace_fails_with_recovery_text(self):
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(), OWNER, BOOT_TAG)
        self.assertIn("bootstrap is missing", str(raised.exception))
        self.assertIn("docs/release-signing.md", str(raised.exception))

    def test_missing_bootstrap_with_history_fails(self):
        ref, record = R(2)
        with self.assertRaises(LedgerError):
            load(FakeGitHub({ref: ("t", HEAD, "commit", record)}), OWNER, BOOT_TAG)

    def test_bootstrap_pin_is_required_and_exact(self):
        for pin in (None, "", "abc", BOOT_TAG.upper()[:39] + "G", "1" * 40):
            with self.subTest(pin=pin), self.assertRaises(LedgerError) as raised:
                load(FakeGitHub(bootstrap_only()), OWNER, pin)
            self.assertIn("LEDGER_BOOTSTRAP_TAG_SHA", str(raised.exception))
        ledger = load(FakeGitHub(bootstrap_only()), "Natnael-Solomon", BOOT_TAG)
        self.assertEqual((ledger.floor, ledger.next_code(), ledger.high_water_mark), (1, 2, 1))

    def test_bootstrap_owner_and_target_type(self):
        with self.assertRaises(LedgerError):
            load(FakeGitHub(bootstrap_only(record=rec("bootstrap", created_by="someone-else"))), OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError):
            load(FakeGitHub(bootstrap_only(target_type="blob")), OWNER, BOOT_TAG)

    def test_lightweight_foreign_alias_and_below_floor_refs_fail(self):
        with self.assertRaises(LedgerError):
            load(FakeGitHub(bootstrap_only(), lightweight={"refs/tags/release-ledger/reserve/5"}), OWNER, BOOT_TAG)
        tags = bootstrap_only(); tags["refs/tags/release-ledger/notes"] = ("t", HEAD, "commit", rec("reserve", 2))
        with self.assertRaises(LedgerError):
            load(FakeGitHub(tags), OWNER, BOOT_TAG)
        tags = bootstrap_only(); tags["refs/tags/release-ledger/reserve/1"] = ("t", HEAD, "commit", rec("reserve", 1))
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(tags), OWNER, BOOT_TAG)
        self.assertIn("floor", str(raised.exception))
        tags = bootstrap_only(); tags["refs/tags/release-ledger/reserve/02"] = ("t", HEAD, "commit", rec("reserve", 2))
        with self.assertRaises(LedgerError):
            load(FakeGitHub(tags), OWNER, BOOT_TAG)

    def test_partial_history_and_tuple_mismatch_fail(self):
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(with_tags(I(2))), OWNER, BOOT_TAG)
        self.assertIn("no reservation", str(raised.exception))
        bad = I(2); bad[1]["reservation"]["workflow_sha"] = SHA_B
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(with_tags(R(2), bad)), OWNER, BOOT_TAG)
        self.assertIn("does not match its reservation", str(raised.exception))
        # The issue record's OWN producer tuple must also equal the reservation (all one run).
        with self.assertRaises(LedgerError):
            load(FakeGitHub(with_tags(R(2), I(2, run_id=99))), OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError):
            load(FakeGitHub(with_tags(R(2), I(2, workflow_sha=SHA_B))), OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError):
            load(FakeGitHub(with_tags(R(2), I(2, source_sha=SHA_B))), OWNER, BOOT_TAG)

    def test_single_certificate_identity(self):
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(with_tags(R(2), I(2), R(3), I(3, cert_sha256="9" * 64))), OWNER, BOOT_TAG)
        self.assertIn("different signing certificates", str(raised.exception))

    def test_high_water_mark_and_next_code(self):
        ledger = load(FakeGitHub(with_tags(R(2), I(2), R(3), R(7))), OWNER, BOOT_TAG)
        self.assertEqual(ledger.high_water_mark, 2)
        self.assertEqual(ledger.next_code(), 8)  # gaps are fine; never reuse


class ReserveTest(unittest.TestCase):
    def reserve(self, gh, ledger, run=RUN):
        return reserve(gh, ledger, SHA_A, run, TAGGER, OWNER, BOOT_TAG)

    def test_same_sha_gets_fresh_codes_anchored_to_main_head(self):
        gh = FakeGitHub(bootstrap_only())
        ledger = load(gh, OWNER, BOOT_TAG)
        c1, r1 = self.reserve(gh, ledger)
        c2, _ = self.reserve(gh, ledger, dict(RUN, run_id=11))
        self.assertEqual((c1, c2), (2, 3))
        tag_payload = gh.created[0][1]
        self.assertEqual(tag_payload["object"], HEAD)              # anchored to main HEAD, not source
        self.assertEqual(r1["source_sha"], SHA_A)                  # source preserved in the record
        self.assertEqual(json.loads(tag_payload["message"])["anchor_sha"], HEAD)

    def test_collision_reloads_ledger_instead_of_probing(self):
        gh = FakeGitHub(bootstrap_only(), collide_once={"refs/tags/release-ledger/reserve/2"})
        ledger = load(gh, OWNER, BOOT_TAG)
        for c in range(2, 10):   # contention jumped far ahead while we were about to write 2
            ref, record = R(c)
            gh.tags[ref] = (f"x{c:038d}", HEAD, "commit", record)
        loads_before = gh.loads
        code, _ = self.reserve(gh, ledger)
        self.assertEqual(code, 10)
        self.assertEqual(gh.loads, loads_before + 1)
        self.assertEqual(sum(1 for k, _ in gh.created if k == "ref"), 1)

    def test_anchor_moved_retries_with_new_head(self):
        gh = FakeGitHub(bootstrap_only(), refuse_anchor_once=True)
        ledger = load(gh, OWNER, BOOT_TAG)
        original = gh.request

        def moving(method, path, payload=None, ok=(200, 201)):
            try:
                return original(method, path, payload, ok)
            except GitHubApiError:
                gh.head = HEAD2    # main advanced between our read and the refused write
                raise
        gh.request = moving
        code, record = self.reserve(gh, ledger)
        self.assertEqual((code, record["anchor_sha"]), (2, HEAD2))
        self.assertEqual(gh.created[-1][1]["ref"], "refs/tags/release-ledger/reserve/2")

    def test_refusal_without_head_change_is_a_permission_error(self):
        gh = FakeGitHub(bootstrap_only(), refuse_anchor_once=True)
        ledger = load(gh, OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError) as raised:
            self.reserve(gh, ledger)
        self.assertIn("contents:write", str(raised.exception))

    def test_non_collision_422_is_not_retried(self):
        class Other(FakeGitHub):
            def request(self, method, path, payload=None, ok=(200, 201)):
                if path == "git/refs":
                    raise GitHubApiError(422, "Validation Failed", "POST", path)
                return super().request(method, path, payload, ok)
        gh = Other(bootstrap_only())
        ledger = load(gh, OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError) as raised:
            self.reserve(gh, ledger)
        self.assertIn("Validation Failed", str(raised.exception))

    def test_exhaustion(self):
        gh = FakeGitHub(with_tags(R(MAX_CODE)))
        ledger = load(gh, OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError):
            self.reserve(gh, ledger)


class IssueTest(unittest.TestCase):
    EVIDENCE = {"unsigned_sha256": HEX, "signed_sha256": HEX, "cert_sha256": HEX, "package": "app.ovrly"}
    PUBLISH = {"run_id": 99, "run_attempt": 1, "workflow_sha": WF, "actor": OWNER}

    def test_issue_records_publish_run_reservation_and_anchor(self):
        gh = FakeGitHub(with_tags(R(2)), head=HEAD2)
        ledger = load(gh, OWNER, BOOT_TAG)
        record = issue(gh, ledger, 2, ledger.reservations[2], self.PUBLISH, self.EVIDENCE, TAGGER)
        self.assertEqual((record["run_id"], record["reservation"]["run_id"], record["anchor_sha"]), (99, 10, HEAD2))
        self.assertEqual(gh.created[0][1]["object"], HEAD2)
        self.assertEqual(ledger.high_water_mark, 2)

    def test_old_build_after_newer_issuance_is_refused(self):
        gh = FakeGitHub(with_tags(R(2), R(3, source_sha=SHA_B), I(3, source_sha=SHA_B)))
        ledger = load(gh, OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError) as raised:
            issue(gh, ledger, 2, ledger.reservations[2], self.PUBLISH, self.EVIDENCE, TAGGER)
        self.assertIn("superseded", str(raised.exception))
        self.assertEqual(gh.created, [])

    def test_double_issue_refused(self):
        gh = FakeGitHub(with_tags(R(2), I(2)))
        ledger = load(gh, OWNER, BOOT_TAG)
        with self.assertRaises(LedgerError):
            issue(gh, ledger, 2, ledger.reservations[2], self.PUBLISH, self.EVIDENCE, TAGGER)


if __name__ == "__main__":
    unittest.main()
