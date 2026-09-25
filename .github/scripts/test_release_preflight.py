import copy
import unittest

import release_ledger
from release_preflight import (
    PreflightError, check_android_ci, check_environment, check_execution_context, check_ledger_protection,
    check_main_ruleset, check_merged_into_main, decide_mode, resolve_redelivery,
)
from telegram_api import GitHubApiError

SHA = "a" * 40
OWNER = "natnael-solomon"
GOOD_CTX = {"ref": "refs/heads/main", "workflow_ref": "o/r/.github/workflows/telegram-apk.yml@refs/heads/main", "run_attempt": "1"}
LEDGER_RULESET = {
    "id": 7, "target": "tag", "enforcement": "active", "bypass_actors": [], "current_user_can_bypass": "never",
    "conditions": {"ref_name": {"include": ["refs/tags/release-ledger/bootstrap", "refs/tags/release-ledger/reserve/*", "refs/tags/release-ledger/issue/*"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "non_fast_forward"}],
}


def good_responses():
    return {
        "rules/branches/main": [
            {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
            {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Android checks"}]}},
        ],
        "rulesets/7": copy.deepcopy(LEDGER_RULESET),
        "environments/production-signing": {
            "protection_rules": [{"type": "required_reviewers", "prevent_self_review": False,
                                  "reviewers": [{"type": "User", "reviewer": {"login": OWNER}}]}],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            "can_admins_bypass": False,
        },
        "environments/production-signing/deployment-branch-policies": {"branch_policies": [{"name": "main", "type": "branch"}]},
        f"compare/main...{SHA}": {"status": "behind"},
        f"commits/{SHA}/check-runs?check_name=Android%20checks&per_page=50": {"check_runs": [
            {"app": {"slug": "github-actions"}, "status": "completed", "conclusion": "success",
             "completed_at": "2026-01-01T00:00:00Z", "details_url": "https://github.com/o/r/actions/runs/500"},
        ]},
        "actions/runs/500": {"path": ".github/workflows/android.yml"},
    }


class FakeGitHub:
    def __init__(self, responses, pages=None):
        self.responses = responses
        self.pages = pages or {}
        if "rulesets" not in self.pages:
            self.pages["rulesets"] = [{"id": 7, "target": "tag", "enforcement": "active"}]

    def get(self, path):
        if path not in self.responses:
            raise GitHubApiError(404, "Not Found", "GET", path)
        return copy.deepcopy(self.responses[path])

    def get_optional(self, path):
        try:
            return self.get(path)
        except GitHubApiError as error:
            if error.status == 404:
                return None
            raise

    def paginate(self, path, key=None):
        yield from self.pages.get(path, [])


class ModeAndContextTest(unittest.TestCase):
    def test_exactly_one_input(self):
        self.assertEqual(decide_mode(SHA, ""), ("build", SHA))
        self.assertEqual(decide_mode("", "42"), ("redeliver", 42))
        for a, b in (("", ""), (SHA, "42"), ("main", ""), ("abc", ""), ("", "x")):
            with self.subTest(a=a, b=b), self.assertRaises(PreflightError):
                decide_mode(a, b)

    def test_context_guards(self):
        check_execution_context(GOOD_CTX)
        for bad in ({"ref": "refs/heads/feat"}, {"workflow_ref": "o/r/.github/workflows/telegram-apk.yml@refs/heads/feat"}, {"run_attempt": "2"}):
            with self.subTest(bad=bad), self.assertRaises(PreflightError) as raised:
                check_execution_context({**GOOD_CTX, **bad})
            if "run_attempt" in bad:
                self.assertIn("redeliver_run_id", str(raised.exception))


class ProtectionTest(unittest.TestCase):
    def test_main_ruleset_variants(self):
        check_main_ruleset(FakeGitHub(good_responses()))
        r = good_responses(); r["rules/branches/main"] = []
        with self.assertRaises(PreflightError) as raised:
            check_main_ruleset(FakeGitHub(r))
        self.assertIn("unprotected", str(raised.exception))
        r = good_responses(); r["rules/branches/main"][0]["parameters"]["required_approving_review_count"] = 0
        with self.assertRaises(PreflightError):
            check_main_ruleset(FakeGitHub(r))
        r = good_responses(); r["rules/branches/main"][1]["parameters"]["required_status_checks"] = [{"context": "Other"}]
        with self.assertRaises(PreflightError):
            check_main_ruleset(FakeGitHub(r))

    def test_ledger_protection_via_tag_rulesets(self):
        check_ledger_protection(FakeGitHub(good_responses()))
        cases = {
            "no rulesets": lambda r, p: p.__setitem__("rulesets", []),
            "disabled": lambda r, p: p.__setitem__("rulesets", [{"id": 7, "target": "tag", "enforcement": "disabled"}]),
            "branch target": lambda r, p: p.__setitem__("rulesets", [{"id": 7, "target": "branch", "enforcement": "active"}]),
            "full record disabled": lambda r, p: r["rulesets/7"].__setitem__("enforcement", "disabled"),
            "full record branch": lambda r, p: r["rulesets/7"].__setitem__("target", "branch"),
            "missing include": lambda r, p: r["rulesets/7"]["conditions"]["ref_name"]["include"].remove("refs/tags/release-ledger/issue/*"),
            "double-star only": lambda r, p: r["rulesets/7"]["conditions"]["ref_name"].__setitem__("include", ["refs/tags/release-ledger/**"]),
            "has exclude": lambda r, p: r["rulesets/7"]["conditions"]["ref_name"].__setitem__("exclude", ["refs/tags/release-ledger/issue/9"]),
            "bypass actor": lambda r, p: r["rulesets/7"].__setitem__("bypass_actors", [{"actor_id": 1}]),
            "can bypass always": lambda r, p: r["rulesets/7"].__setitem__("current_user_can_bypass", "always"),
            "can bypass pull_request": lambda r, p: r["rulesets/7"].__setitem__("current_user_can_bypass", "pull_requests_only"),
            "can bypass missing": lambda r, p: r["rulesets/7"].pop("current_user_can_bypass"),
            "missing update rule": lambda r, p: r["rulesets/7"].__setitem__("rules", [{"type": "deletion"}, {"type": "non_fast_forward"}]),
        }
        for name, mutate in cases.items():
            r, p = good_responses(), {}
            gh = FakeGitHub(r, p)
            mutate(r, gh.pages)
            with self.subTest(name=name), self.assertRaises(PreflightError) as raised:
                check_ledger_protection(gh)
            if name.startswith("can bypass"):
                self.assertIn("current_user_can_bypass", str(raised.exception))

    def test_ledger_protection_accepts_installation_token_view(self):
        # The workflow token does not receive bypass_actors; current_user_can_bypass=never suffices.
        r = good_responses(); r["rulesets/7"].pop("bypass_actors")
        check_ledger_protection(FakeGitHub(r))

    def test_environment_variants(self):
        check_environment(FakeGitHub(good_responses()), OWNER)
        env_key = "environments/production-signing"
        cases = {
            "missing": lambda r: r.pop(env_key),
            "two reviewers": lambda r: r[env_key]["protection_rules"][0]["reviewers"].append({"type": "User", "reviewer": {"login": "other"}}),
            "wrong reviewer": lambda r: r[env_key]["protection_rules"][0]["reviewers"].__setitem__(0, {"type": "User", "reviewer": {"login": "other"}}),
            "self review blocked": lambda r: r[env_key]["protection_rules"][0].__setitem__("prevent_self_review", True),
            "no custom policy": lambda r: r[env_key].__setitem__("deployment_branch_policy", {"protected_branches": True, "custom_branch_policies": False}),
            "extra branch": lambda r: r[env_key + "/deployment-branch-policies"]["branch_policies"].append({"name": "dev", "type": "branch"}),
            "tag policy": lambda r: r[env_key + "/deployment-branch-policies"].__setitem__("branch_policies", [{"name": "main", "type": "tag"}]),
            "policies 404": lambda r: r.pop(env_key + "/deployment-branch-policies"),
            "bypass true": lambda r: r[env_key].__setitem__("can_admins_bypass", True),
            "bypass absent": lambda r: r[env_key].pop("can_admins_bypass"),
            "bypass string": lambda r: r[env_key].__setitem__("can_admins_bypass", "false"),
        }
        for name, mutate in cases.items():
            r = good_responses(); mutate(r)
            with self.subTest(name=name), self.assertRaises(PreflightError) as raised:
                check_environment(FakeGitHub(r), OWNER)
            if name.startswith("bypass"):
                self.assertIn("Cannot verify", str(raised.exception))


class EligibilityTest(unittest.TestCase):
    def test_merged_into_main(self):
        check_merged_into_main(FakeGitHub(good_responses()), SHA)
        for status in ("ahead", "diverged"):
            r = good_responses(); r[f"compare/main...{SHA}"] = {"status": status}
            with self.subTest(status=status), self.assertRaises(PreflightError):
                check_merged_into_main(FakeGitHub(r), SHA)

    def test_android_check_identity(self):
        key = f"commits/{SHA}/check-runs?check_name=Android%20checks&per_page=50"
        check_android_ci(FakeGitHub(good_responses()), SHA)
        r = good_responses(); r[key]["check_runs"][0]["app"] = {"slug": "someone-else"}
        with self.assertRaises(PreflightError):
            check_android_ci(FakeGitHub(r), SHA)
        r = good_responses(); r["actions/runs/500"] = {"path": ".github/workflows/other.yml"}
        with self.assertRaises(PreflightError):
            check_android_ci(FakeGitHub(r), SHA)
        r = good_responses(); r[key]["check_runs"][0]["conclusion"] = "failure"
        with self.assertRaises(PreflightError) as raised:
            check_android_ci(FakeGitHub(r), SHA)
        self.assertIn("failure", str(raised.exception))

    def test_latest_real_check_wins_over_older(self):
        key = f"commits/{SHA}/check-runs?check_name=Android%20checks&per_page=50"
        r = good_responses()
        r[key]["check_runs"].insert(0, {"app": {"slug": "github-actions"}, "status": "completed", "conclusion": "failure",
                                        "completed_at": "2025-01-01T00:00:00Z", "details_url": "https://github.com/o/r/actions/runs/400"})
        r["actions/runs/400"] = {"path": ".github/workflows/android.yml"}
        check_android_ci(FakeGitHub(r), SHA)


class RedeliveryTest(unittest.TestCase):
    HEX = "1" * 64
    WF_SHA = "b" * 40

    def ledger(self, hwm=5, cert=None):
        cert = cert or self.HEX
        ledger = release_ledger.Ledger(floor=1, bootstrap={})
        for code in range(2, hwm + 1):
            ledger.reservations[code] = {"code": code}
            ledger.issuances[code] = {"code": code, "run_id": 100 + code, "run_attempt": 1, "workflow_sha": self.WF_SHA,
                                      "source_sha": SHA, "signed_sha256": self.HEX, "cert_sha256": cert, "package": "app.ovrly"}
        return ledger

    def run_info(self, **over):
        base = {"repository": {"full_name": "o/r"}, "path": ".github/workflows/telegram-apk.yml", "workflow_id": 777,
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": self.WF_SHA, "run_attempt": 1,
                "status": "completed", "conclusion": "failure"}
        base.update(over)
        return base

    def github(self, run_id=105, artifacts=None, **run_over):
        responses = {f"actions/runs/{run_id}": self.run_info(**run_over),
                     "actions/workflows/telegram-apk.yml": {"id": 777, "path": ".github/workflows/telegram-apk.yml"}}
        default = [{"id": 9001, "name": "ovrly-signed-5", "expired": False, "digest": "sha256:" + self.HEX,
                    "workflow_run": {"id": run_id, "head_sha": self.WF_SHA, "head_branch": "main"}}]
        return FakeGitHub(responses, {f"actions/runs/{run_id}/artifacts": default if artifacts is None else artifacts})

    def resolve(self, gh, ledger, run_id=105, cert=None):
        return resolve_redelivery(gh, ledger, run_id, "o/r", cert or self.HEX)

    def test_failed_telegram_run_with_current_issued_artifact_is_eligible(self):
        resolved = self.resolve(self.github(), self.ledger())
        self.assertEqual((resolved["code"], resolved["artifact_id"], resolved["signed_sha256"]), (5, 9001, self.HEX))

    def test_timed_out_cancelled_and_success_eligible_active_not(self):
        for conclusion in ("timed_out", "cancelled", "success"):
            self.resolve(self.github(conclusion=conclusion), self.ledger())
        with self.assertRaises(PreflightError):
            self.resolve(self.github(status="in_progress", conclusion=None), self.ledger())

    def test_older_issued_artifact_is_superseded(self):
        gh = self.github(run_id=104, artifacts=[{"id": 9000, "name": "ovrly-signed-4", "expired": False, "digest": "sha256:" + self.HEX,
                                                  "workflow_run": {"id": 104, "head_sha": self.WF_SHA, "head_branch": "main"}}])
        with self.assertRaises(PreflightError) as raised:
            self.resolve(gh, self.ledger(), 104)
        self.assertIn("superseded", str(raised.exception))

    def test_missing_expired_or_unissued_artifact_fails(self):
        with self.assertRaises(PreflightError):
            self.resolve(self.github(artifacts=[]), self.ledger())
        with self.assertRaises(PreflightError):
            self.resolve(self.github(artifacts=[{"id": 1, "name": "ovrly-signed-5", "expired": True}]), self.ledger())
        ledger = self.ledger(); ledger.issuances.pop(5)
        with self.assertRaises(PreflightError):
            self.resolve(self.github(), ledger)

    def test_untrusted_producer_binding_fails(self):
        cases = {
            "other workflow": dict(path=".github/workflows/android.yml"),
            "other workflow id": dict(workflow_id=778),
            "fork": dict(repository={"full_name": "fork/r"}),
            "not dispatch": dict(event="push"),
            "not main": dict(head_branch="feat/x"),
            "other revision": dict(head_sha="9" * 40),
            "other attempt": dict(run_attempt=2),
        }
        for name, over in cases.items():
            with self.subTest(name=name), self.assertRaises(PreflightError):
                self.resolve(self.github(**over), self.ledger())
        ledger = self.ledger(); ledger.issuances[5]["run_id"] = 999
        with self.assertRaises(PreflightError):
            self.resolve(self.github(), ledger)

    def test_artifact_producer_facts_are_required(self):
        def mutate(fn):
            gh = self.github()
            fn(gh.pages["actions/runs/105/artifacts"][0])
            return gh
        cases = {
            "other run": lambda a: a["workflow_run"].__setitem__("id", 42),
            "other head": lambda a: a["workflow_run"].__setitem__("head_sha", "8" * 40),
            "other branch": lambda a: a["workflow_run"].__setitem__("head_branch", "feat/x"),
            "no producer": lambda a: a.pop("workflow_run"),
            "no digest": lambda a: a.pop("digest"),
            "bad digest": lambda a: a.__setitem__("digest", "md5:abc"),
        }
        for name, fn in cases.items():
            with self.subTest(name=name), self.assertRaises(PreflightError):
                self.resolve(mutate(fn), self.ledger())

    def test_certificate_pin_must_match_issuance(self):
        with self.assertRaises(PreflightError) as raised:
            self.resolve(self.github(), self.ledger(), cert="2" * 64)
        self.assertIn("EXPECTED_SIGNING_CERT_SHA256", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
