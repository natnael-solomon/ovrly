import base64
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import release_tag
from release_tag import ReleaseError
from test_release_preflight import FakeGitHub, good_responses

SHA = "a" * 40
OWNER = "natnael-solomon"
CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Something newer.

## [0.2.0] - 2026-10-20

### Added

- Tagged demo releases.

### Known limitations

- Research is not connected.
- Physical-device testing is partial.

## [0.1.0] - 2026-09-01

### Known limitations

- Older limitation.
"""
GRADLE = 'plugins {}\n\nval appVersionName = "0.2.0"\n'
PUSH_ENV = {"GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/tags/v0.2.0", "GITHUB_SHA": SHA}


def release_responses():
    responses = good_responses()
    responses["environments/release"] = {
        "protection_rules": [{"type": "required_reviewers", "prevent_self_review": False,
                              "reviewers": [{"type": "User", "reviewer": {"login": OWNER}}]}],
        "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
        "can_admins_bypass": False,
    }
    responses["environments/release/deployment-branch-policies"] = {
        "branch_policies": [{"name": "v*", "type": "tag"}]}
    return responses


class GateTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root / "android/app").mkdir(parents=True)
        (self.root / "android/app/build.gradle.kts").write_text(GRADLE, encoding="utf-8")
        (self.root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def gate(self, responses=None, env=None):
        github = FakeGitHub(responses or release_responses())
        return release_tag.gate(github, {**PUSH_ENV, **(env or {})}, self.root, OWNER)

    def test_good_tag_copies_known_limitations_into_notes(self):
        version, notes = self.gate()
        self.assertEqual("0.2.0", version)
        self.assertTrue(notes.startswith("# ovrly 0.2.0 (2026-10-20)\n"))
        limitations = notes.split("## Known limitations\n\n")[1].split("\n\n## Changes")[0]
        self.assertEqual("- Research is not connected.\n- Physical-device testing is partial.", limitations)
        self.assertIn("- Tagged demo releases.", notes)
        self.assertNotIn("Something newer", notes)
        self.assertNotIn("Older limitation", notes)
        self.assertIn(SHA, notes)

    def test_tag_shape_and_event(self):
        for env in ({"GITHUB_REF": "refs/tags/v0.2"}, {"GITHUB_REF": "refs/heads/main"},
                    {"GITHUB_REF": "refs/tags/v0.2.0-rc1"}, {"GITHUB_EVENT_NAME": "pull_request"},
                    {"GITHUB_SHA": "main"}):
            with self.subTest(env=env), self.assertRaises(ReleaseError):
                self.gate(env=env)

    def test_version_name_must_match_tag(self):
        (self.root / "android/app/build.gradle.kts").write_text(GRADLE.replace("0.2.0", "0.1.0"))
        with self.assertRaisesRegex(ReleaseError, "versionName 0.1.0"):
            self.gate()

    def test_changelog_needs_version_date_and_limitations(self):
        cases = {
            "no section": CHANGELOG.replace("## [0.2.0] - 2026-10-20", "## [0.2.1] - 2026-10-20"),
            "no date": CHANGELOG.replace("## [0.2.0] - 2026-10-20", "## [0.2.0]"),
            "bad date": CHANGELOG.replace("2026-10-20", "2026-13-40"),
            "no limitations": CHANGELOG.replace("### Known limitations\n\n- Research is not connected.\n"
                                                "- Physical-device testing is partial.\n", ""),
        }
        for name, text in cases.items():
            (self.root / "CHANGELOG.md").write_text(text, encoding="utf-8")
            with self.subTest(name), self.assertRaises(ReleaseError):
                self.gate()

    def test_environment_must_be_protected_and_tag_scoped(self):
        def mutate(change):
            responses = release_responses()
            change(responses)
            return responses
        cases = {
            "missing": lambda r: r.pop("environments/release"),
            "no reviewer": lambda r: r["environments/release"].update(protection_rules=[]),
            "other reviewer": lambda r: r["environments/release"]["protection_rules"][0].update(
                reviewers=[{"reviewer": {"login": "someone"}}]),
            "any ref": lambda r: r["environments/release"].update(
                deployment_branch_policy={"custom_branch_policies": False}),
            "branch policy": lambda r: r.update({"environments/release/deployment-branch-policies": {
                "branch_policies": [{"name": "main", "type": "branch"}]}}),
            "admin bypass": lambda r: r["environments/release"].update(can_admins_bypass=True),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(ReleaseError):
                self.gate(mutate(change))

    def test_commit_must_be_on_main_with_green_android_checks(self):
        responses = release_responses()
        responses[f"compare/main...{SHA}"] = {"status": "diverged"}
        with self.assertRaises(release_tag.PreflightError):
            self.gate(responses)
        responses = release_responses()
        responses[f"commits/{SHA}/check-runs?check_name=Android%20checks&per_page=50"]["check_runs"][0][
            "conclusion"] = "failure"
        with self.assertRaises(release_tag.PreflightError):
            self.gate(responses)


class FakeTools:
    """Stands in for keytool, aapt2 and apksigner; records every invocation."""

    def __init__(self, debuggable=False, code=1):
        self.calls = []
        self.debuggable = debuggable
        self.code = code

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        tool = Path(args[0]).name
        if tool == "keytool":
            Path(args[args.index("-keystore") + 1]).write_bytes(b"debug-keystore")
            return subprocess.CompletedProcess(args, 0, "", "")
        if tool == "aapt2":
            debug = "application-debuggable\n" if self.debuggable else ""
            out = f"package: name='app.ovrly' versionCode='{self.code}' versionName='0.2.0'\n{debug}"
            return subprocess.CompletedProcess(args, 0, out, "")
        if args[1] == "sign":
            Path(args[args.index("--out") + 1]).write_bytes(b"signed")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "Signer #1 certificate SHA-256 digest: " + "c" * 64, "")


class SignTest(unittest.TestCase):
    BASE = {**PUSH_ENV, "GITHUB_RUN_ID": "9", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_SHA": SHA}
    SECRETS = {"DEMO_KEYSTORE_B64": base64.b64encode(b"jks").decode(), "DEMO_KEYSTORE_PASSWORD": "s",
               "DEMO_KEY_ALIAS": "ovrly-demo", "DEMO_KEY_PASSWORD": "k"}

    def sign(self, env, tools=None):
        tools = tools or FakeTools()
        with tempfile.TemporaryDirectory() as temporary:
            unsigned = Path(temporary) / "app-release-unsigned.apk"
            unsigned.write_bytes(b"unsigned")
            out = Path(temporary) / "out"
            provenance = release_tag.sign({**self.BASE, **env}, {"aapt2": "aapt2", "apksigner": "apksigner"},
                                          unsigned, out, "# ovrly 0.2.0\n", runner=tools)
            notes = (out / "release-notes.md").read_text(encoding="utf-8")
            recorded = json.loads((out / "release-provenance.json").read_text(encoding="utf-8"))
            self.assertTrue((out / "ovrly-0.2.0.apk").is_file())
        self.assertEqual(provenance, recorded)
        return provenance, notes, tools.calls

    def test_without_secrets_signs_with_a_fresh_debug_key(self):
        provenance, notes, calls = self.sign({})
        self.assertEqual("debug", provenance["signing"])
        self.assertEqual("keytool", calls[1][0])
        sign_call = next(call for call in calls if call[1:2] == ["sign"])
        self.assertIn(release_tag.DEBUG_ALIAS, sign_call)
        self.assertIn("throwaway debug key", notes)
        self.assertEqual(1, provenance["version_code"])

    def test_with_all_secrets_signs_with_the_demo_key(self):
        provenance, notes, calls = self.sign(self.SECRETS)
        self.assertEqual("demo", provenance["signing"])
        self.assertFalse(any(call[0] == "keytool" for call in calls))
        self.assertIn("ovrly-demo", next(call for call in calls if call[1:2] == ["sign"]))
        self.assertIn("c" * 64, notes)
        for secret in ("s", "k"):
            self.assertFalse(any(arg == secret for call in calls for arg in call))

    def test_partial_secrets_fail(self):
        partial = dict(self.SECRETS)
        partial.pop("DEMO_KEY_PASSWORD")
        with self.assertRaisesRegex(ReleaseError, "partly set"):
            self.sign(partial)

    def test_certificate_pin(self):
        self.sign({**self.SECRETS, "DEMO_SIGNING_CERT_SHA256": "c" * 64})
        with self.assertRaisesRegex(ReleaseError, "does not match"):
            self.sign({**self.SECRETS, "DEMO_SIGNING_CERT_SHA256": "d" * 64})
        with self.assertRaisesRegex(ReleaseError, "refusing a debug signature"):
            self.sign({"DEMO_SIGNING_CERT_SHA256": "c" * 64})

    def test_rejects_debuggable_or_unexpected_version_code(self):
        for tools in (FakeTools(debuggable=True), FakeTools(code=2)):
            with self.subTest(code=tools.code), self.assertRaises(release_tag.PublishError):
                self.sign({}, copy.copy(tools))


if __name__ == "__main__":
    unittest.main()
