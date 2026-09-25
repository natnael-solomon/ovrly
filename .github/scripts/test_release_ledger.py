import json
import unittest

import release_ledger as ledger_mod
from release_ledger import (
    BOOTSTRAP_REF, LedgerError, MAX_CODE, SCHEMA, issue, load, parse_record, reserve, validate_code,
)
from telegram_api import GitHubApiError

SHA_A = "a" * 40
SHA_B = "b" * 40
WF = "c" * 40
HEX = "0" * 64
RUN = {"run_id": 10, "run_attempt": 1, "workflow_sha": WF, "actor": "dev"}
TAGGER = {"name": "ci", "email": "ci@example.invalid", "date": "2026-01-01T00:00:00Z"}


def rec(kind, code=None, **extra):
    base = {"schema": SCHEMA, "kind": kind}
    if kind == "bootstrap":
        base.update(floor=1, created_by="natnael-solomon")
    else:
        base.update(code=code, source_sha=SHA_A, run_id=10, run_attempt=1, workflow_sha=WF, requested_by="dev")
    if kind == "issue":
        base.update(unsigned_sha256=HEX, signed_sha256=HEX, cert_sha256=HEX, package="app.ovrly")
    base.update(extra)
    return base


class FakeGitHub:
    """Serves a namespace of annotated tags and records create calls; collides on demand."""

    def __init__(self, tags=None, lightweight=(), collide_once=()):
        self.tags = dict(tags or {})              # ref -> (target_sha, record dict)
        self.lightweight = set(lightweight)
        self.collide_once = set(collide_once)
        self.created = []
        self._objects = {}

    def paginate(self, path):
        assert path.startswith("git/matching-refs/tags/release-ledger/")
        entries = []
        for i, (ref, (target, record)) in enumerate(sorted(self.tags.items())):
            tag_sha = f"tag{i:036d}"
            self._objects[tag_sha] = {"message": json.dumps(record) if isinstance(record, dict) else record,
                                      "object": {"sha": target, "type": "commit"}}
            entries.append({"ref": ref, "object": {"sha": tag_sha, "type": "tag"}})
        for ref in self.lightweight:
            entries.append({"ref": ref, "object": {"sha": SHA_B, "type": "commit"}})
        # Emulate pagination by yielding one at a time.
        yield from entries

    def get(self, path):
        assert path.startswith("git/tags/")
        return self._objects[path.split("/")[-1]]

    def request(self, method, path, payload=None, ok=(200, 201)):
        assert method == "POST"
        if path == "git/tags":
            self.created.append(("tag", payload))
            return {"sha": "newtag" + str(len(self.created))}
        if path == "git/refs":
            if payload["ref"] in self.collide_once:
                self.collide_once.discard(payload["ref"])
                raise GitHubApiError(422, "Reference already exists", "POST", path)
            self.created.append(("ref", payload))
            return {"ref": payload["ref"]}
        raise AssertionError(path)


def bootstrap_only():
    return {BOOTSTRAP_REF: (SHA_A, rec("bootstrap"))}


class ValidationTest(unittest.TestCase):
    def test_bounds_inclusive(self):
        self.assertEqual(validate_code(1), 1)
        self.assertEqual(validate_code(MAX_CODE), MAX_CODE)
        for bad in (0, MAX_CODE + 1, "x", None, 1.5):
            with self.subTest(bad=bad), self.assertRaises(LedgerError):
                validate_code(bad)

    def test_parse_rejects_schema_kind_target_mismatch(self):
        good = {"message": json.dumps(rec("reserve", 2)), "object": {"sha": SHA_A}}
        self.assertEqual(parse_record("r", good, "reserve", 2)["code"], 2)
        bad_cases = [
            ({"message": "not json", "object": {"sha": SHA_A}}, "reserve", 2),
            ({"message": json.dumps(rec("reserve", 2, schema="other")), "object": {"sha": SHA_A}}, "reserve", 2),
            ({"message": json.dumps(rec("reserve", 3)), "object": {"sha": SHA_A}}, "reserve", 2),
            ({"message": json.dumps(rec("reserve", 2)), "object": {"sha": SHA_B}}, "reserve", 2),
            ({"message": json.dumps(rec("issue", 2, cert_sha256="short")), "object": {"sha": SHA_A}}, "issue", 2),
            ({"message": json.dumps(rec("bootstrap", floor=1000)), "object": {"sha": SHA_A}}, "bootstrap", None),
        ]
        for obj, kind, code in bad_cases:
            with self.subTest(obj=obj), self.assertRaises(LedgerError):
                parse_record("r", obj, kind, code)


class LoadTest(unittest.TestCase):
    def test_wholly_missing_namespace_fails_with_recovery_text(self):
        with self.assertRaises(LedgerError) as raised:
            load(FakeGitHub(), "natnael-solomon")
        self.assertIn("bootstrap is missing", str(raised.exception))
        self.assertIn("docs/release-signing.md", str(raised.exception))

    def test_missing_bootstrap_with_history_fails(self):
        gh = FakeGitHub({"refs/tags/release-ledger/reserve/2": (SHA_A, rec("reserve", 2))})
        with self.assertRaises(LedgerError):
            load(gh, "natnael-solomon")

    def test_bootstrap_owner_and_floor_validated(self):
        with self.assertRaises(LedgerError):
            load(FakeGitHub({BOOTSTRAP_REF: (SHA_A, rec("bootstrap", created_by="someone-else"))}), "natnael-solomon")
        ledger = load(FakeGitHub(bootstrap_only()), "Natnael-Solomon")
        self.assertEqual(ledger.floor, 1)
        self.assertEqual(ledger.next_code(), 2)
        self.assertEqual(ledger.high_water_mark, 1)

    def test_lightweight_or_foreign_refs_fail(self):
        gh = FakeGitHub(bootstrap_only(), lightweight={"refs/tags/release-ledger/reserve/5"})
        with self.assertRaises(LedgerError):
            load(gh, "natnael-solomon")
        gh = FakeGitHub({**bootstrap_only(), "refs/tags/release-ledger/notes": (SHA_A, rec("reserve", 2))})
        with self.assertRaises(LedgerError):
            load(gh, "natnael-solomon")

    def test_partial_history_issue_without_reservation_fails(self):
        gh = FakeGitHub({**bootstrap_only(), "refs/tags/release-ledger/issue/2": (SHA_A, rec("issue", 2))})
        with self.assertRaises(LedgerError) as raised:
            load(gh, "natnael-solomon")
        self.assertIn("no reservation", str(raised.exception))

    def test_high_water_mark_and_next_code(self):
        gh = FakeGitHub({
            **bootstrap_only(),
            "refs/tags/release-ledger/reserve/2": (SHA_A, rec("reserve", 2)),
            "refs/tags/release-ledger/issue/2": (SHA_A, rec("issue", 2)),
            "refs/tags/release-ledger/reserve/3": (SHA_B, rec("reserve", 3, source_sha=SHA_B)),
            "refs/tags/release-ledger/reserve/7": (SHA_B, rec("reserve", 7, source_sha=SHA_B)),
        })
        ledger = load(gh, "natnael-solomon")
        self.assertEqual(ledger.high_water_mark, 2)
        self.assertEqual(ledger.next_code(), 8)  # gaps are fine; never reuse


class ReserveTest(unittest.TestCase):
    def test_same_sha_gets_fresh_codes(self):
        gh = FakeGitHub(bootstrap_only())
        ledger = load(gh, "natnael-solomon")
        c1, _ = reserve(gh, ledger, SHA_A, RUN, TAGGER)
        c2, _ = reserve(gh, ledger, SHA_A, dict(RUN, run_id=11), TAGGER)
        self.assertEqual((c1, c2), (2, 3))
        refs = [p["ref"] for k, p in gh.created if k == "ref"]
        self.assertEqual(refs, ["refs/tags/release-ledger/reserve/2", "refs/tags/release-ledger/reserve/3"])
        tag_payload = gh.created[0][1]
        self.assertEqual(tag_payload["object"], SHA_A)
        self.assertEqual(json.loads(tag_payload["message"])["run_attempt"], 1)

    def test_collision_recomputes(self):
        gh = FakeGitHub(bootstrap_only(), collide_once={"refs/tags/release-ledger/reserve/2"})
        ledger = load(gh, "natnael-solomon")
        code, _ = reserve(gh, ledger, SHA_A, RUN, TAGGER)
        self.assertEqual(code, 3)

    def test_non_collision_422_is_not_retried(self):
        class Other(FakeGitHub):
            def request(self, method, path, payload=None, ok=(200, 201)):
                if path == "git/refs":
                    raise GitHubApiError(422, "Validation Failed", "POST", path)
                return super().request(method, path, payload, ok)
        gh = Other(bootstrap_only())
        ledger = load(gh, "natnael-solomon")
        with self.assertRaises(LedgerError) as raised:
            reserve(gh, ledger, SHA_A, RUN, TAGGER)
        self.assertIn("Validation Failed", str(raised.exception))
        self.assertEqual(sum(1 for k, _ in gh.created if k == "tag"), 1)

    def test_exhaustion(self):
        gh = FakeGitHub({**bootstrap_only(), f"refs/tags/release-ledger/reserve/{MAX_CODE}": (SHA_A, rec("reserve", MAX_CODE))})
        ledger = load(gh, "natnael-solomon")
        with self.assertRaises(LedgerError):
            reserve(gh, ledger, SHA_A, RUN, TAGGER)


class IssueTest(unittest.TestCase):
    def evidence(self):
        return {"unsigned_sha256": HEX, "signed_sha256": HEX, "cert_sha256": HEX, "package": "app.ovrly"}

    def test_issue_records_publish_run_and_reservation(self):
        gh = FakeGitHub({**bootstrap_only(), "refs/tags/release-ledger/reserve/2": (SHA_A, rec("reserve", 2))})
        ledger = load(gh, "natnael-solomon")
        publish_run = {"run_id": 99, "run_attempt": 1, "workflow_sha": WF, "actor": "natnael-solomon"}
        record = issue(gh, ledger, 2, ledger.reservations[2], publish_run, self.evidence(), TAGGER)
        self.assertEqual(record["run_id"], 99)
        self.assertEqual(record["reservation"]["run_id"], 10)
        self.assertEqual(ledger.high_water_mark, 2)
        self.assertEqual(gh.created[-1][1]["ref"], "refs/tags/release-ledger/issue/2")

    def test_old_build_after_newer_issuance_is_refused(self):
        gh = FakeGitHub({
            **bootstrap_only(),
            "refs/tags/release-ledger/reserve/2": (SHA_A, rec("reserve", 2)),
            "refs/tags/release-ledger/reserve/3": (SHA_B, rec("reserve", 3, source_sha=SHA_B)),
            "refs/tags/release-ledger/issue/3": (SHA_B, rec("issue", 3, source_sha=SHA_B)),
        })
        ledger = load(gh, "natnael-solomon")
        with self.assertRaises(LedgerError) as raised:
            issue(gh, ledger, 2, ledger.reservations[2], RUN, self.evidence(), TAGGER)
        self.assertIn("superseded", str(raised.exception))
        self.assertEqual(gh.created, [])

    def test_double_issue_refused(self):
        gh = FakeGitHub({
            **bootstrap_only(),
            "refs/tags/release-ledger/reserve/2": (SHA_A, rec("reserve", 2)),
            "refs/tags/release-ledger/issue/2": (SHA_A, rec("issue", 2)),
        })
        ledger = load(gh, "natnael-solomon")
        with self.assertRaises(LedgerError):
            issue(gh, ledger, 2, ledger.reservations[2], RUN, self.evidence(), TAGGER)


if __name__ == "__main__":
    unittest.main()
