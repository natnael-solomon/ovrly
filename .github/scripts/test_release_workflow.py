"""Structural assertions over telegram-apk.yml: the trust boundaries live in the YAML, so test them."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import yaml
except ImportError:  # pragma: no cover - the CI image ships python3-yaml; local envs may not
    yaml = None

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "telegram-apk.yml"
ANDROID_WORKFLOW = WORKFLOW.with_name("android.yml")


def load(path=WORKFLOW):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data, data["jobs"]


@unittest.skipIf(yaml is None, "PyYAML is required to parse the workflow")
class TrustBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.data, self.jobs = load()

    def test_only_manual_dispatch_and_no_default_permissions(self):
        on = self.data[True] if True in self.data else self.data["on"]
        self.assertEqual(list(on), ["workflow_dispatch"])
        self.assertEqual(self.data["permissions"], {})

    def test_build_job_has_no_write_and_no_secrets(self):
        build = self.jobs["build"]
        self.assertEqual(build["permissions"], {"contents": "read"})
        self.assertNotIn("environment", build)
        text = yaml.safe_dump(build)
        self.assertNotIn("secrets.", text)
        checkout = build["steps"][0]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertIs(checkout["with"]["persist-credentials"], False)
        self.assertEqual(checkout["with"]["ref"], "${{ needs.preflight.outputs.source_sha }}")

    def test_trusted_jobs_check_out_workflow_revision_and_never_run_gradle(self):
        for name in ("preflight", "publish"):
            job = self.jobs[name]
            checkout = job["steps"][0]
            self.assertEqual(checkout["with"]["ref"], "${{ github.workflow_sha }}", name)
            self.assertIs(checkout["with"]["persist-credentials"], False, name)
            paths = set(checkout["with"]["sparse-checkout"].split())
            # The preflight test step reads the workflow file, so the sparse checkout must include it.
            self.assertEqual(paths, {".github/scripts", ".github/workflows"}, name)
            self.assertNotIn("gradlew", yaml.safe_dump(job), name)

    def test_preflight_test_step_dependencies_are_checked_out(self):
        steps = {s["name"]: s for s in self.jobs["preflight"]["steps"]}
        run = steps["Test the release helpers"]["run"]
        self.assertIn("test_release_*.py", run)
        for needed in (WORKFLOW.relative_to(WORKFLOW.parents[2]).as_posix(), ".github/scripts"):
            self.assertTrue(any(needed.startswith(p) for p in self.jobs["preflight"]["steps"][0]["with"]["sparse-checkout"].split()), needed)

    def test_sparse_checkout_contains_actual_test_dependencies(self):
        root = WORKFLOW.parents[2]
        patterns = self.jobs["preflight"]["steps"][0]["with"]["sparse-checkout"].split()
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            for pattern in patterns:
                shutil.copytree(root / pattern, checkout / pattern)
            self.assertTrue((checkout / ".github/scripts/test_release_workflow.py").is_file())
            _, jobs = load(checkout / WORKFLOW.relative_to(root))
            self.assertEqual(jobs["publish"]["environment"], "production-signing")

    def test_owner_pins_are_supplied_to_every_protected_helper(self):
        names = {
            "Verify protections, eligibility and reserve a version code",
            "Verify, sign and record issuance (new build)",
            "Verify and redeliver the issued artifact",
        }
        checked = set()
        for job in self.jobs.values():
            for step in job["steps"]:
                if step["name"] in names:
                    for key in ("EXPECTED_SIGNING_CERT_SHA256", "LEDGER_BOOTSTRAP_TAG_SHA"):
                        self.assertEqual(step["env"][key], "${{ vars." + key + " }}", step["name"])
                    checked.add(step["name"])
                self.assertNotRegex(step.get("run", ""), r"\$\{\{\s*(?:inputs|needs)\.")
        self.assertEqual(checked, names)

    def test_mapping_is_required_and_artifacts_are_not_overwritten(self):
        provenance = next(step for step in self.jobs["build"]["steps"] if step.get("id") == "provenance")
        self.assertIn("cp android/app/build/outputs/mapping/release/mapping.txt out/mapping.txt", provenance["run"])
        self.assertNotIn("|| true", provenance["run"])
        for job in self.jobs.values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/upload-artifact@"):
                    self.assertEqual(step["with"]["if-no-files-found"], "error")
                    self.assertEqual(step["with"]["retention-days"], 90)
                    self.assertFalse(step["with"].get("overwrite", False))

    def test_ledger_writes_are_confined_to_trusted_jobs(self):
        self.assertEqual(self.jobs["preflight"]["permissions"]["contents"], "write")
        self.assertEqual(self.jobs["publish"]["permissions"]["contents"], "write")
        for name, job in self.jobs.items():
            self.assertNotIn("id-token", job.get("permissions", {}), name)
            self.assertNotIn("packages", job.get("permissions", {}), name)

    def test_publish_is_environment_gated_and_serialized(self):
        publish = self.jobs["publish"]
        self.assertEqual(publish["environment"], "production-signing")
        self.assertEqual(publish["concurrency"], {"group": "ovrly-production-publish", "cancel-in-progress": False, "queue": "max"})
        for name in ("preflight", "build"):
            self.assertNotIn("environment", self.jobs[name])
            self.assertNotIn("concurrency", self.jobs[name])

    def test_redelivery_dag_is_executable(self):
        cond = " ".join(self.jobs["publish"]["if"].split())
        self.assertIn("!cancelled()", cond)
        self.assertIn("needs.preflight.result == 'success'", cond)
        self.assertIn("needs.build.result == 'success'", cond)
        self.assertIn("needs.preflight.outputs.mode == 'redeliver' && needs.build.result == 'skipped'", cond)
        self.assertEqual(self.jobs["build"]["if"], "needs.preflight.outputs.mode == 'build'")
        self.assertEqual(self.jobs["publish"]["needs"], ["preflight", "build"])

    def test_issuance_precedes_exposure_and_delivery(self):
        names = [step["name"] for step in self.jobs["publish"]["steps"]]
        issue = names.index("Verify, sign and record issuance (new build)")
        expose = names.index("Expose the signed artifact (after issuance)")
        deliver = names.index("Deliver to Telegram (new build)")
        self.assertLess(issue, expose)
        self.assertLess(expose, deliver)
        steps = {s["name"]: s for s in self.jobs["publish"]["steps"]}
        self.assertNotIn("continue-on-error", yaml.safe_dump(self.jobs["publish"]))
        self.assertEqual(steps["Expose the signed artifact (after issuance)"]["with"]["if-no-files-found"], "error")

    def test_secrets_only_in_publish_and_downloads_are_pinned_by_id_with_digest_check(self):
        for name, job in self.jobs.items():
            has_secrets = "secrets." in yaml.safe_dump(job)
            self.assertEqual(has_secrets, name == "publish", name)
        for step in self.jobs["publish"]["steps"]:
            if step.get("uses", "").startswith("actions/download-artifact@"):
                self.assertIn("artifact-ids", step["with"])
                self.assertEqual(step["with"]["digest-mismatch"], "error")
                self.assertNotIn("name", step["with"])
        redeliver = next(s for s in self.jobs["publish"]["steps"] if s["name"].startswith("Download the issued"))
        self.assertEqual(redeliver["with"]["run-id"], "${{ inputs.redeliver_run_id }}")

    def test_actions_are_commit_pinned(self):
        for job in self.jobs.values():
            for step in job["steps"]:
                uses = step.get("uses")
                if uses:
                    ref = uses.split("@")[1].split(" ")[0]
                    self.assertRegex(ref, r"^[0-9a-f]{40}$", uses)


@unittest.skipIf(yaml is None, "PyYAML is required to parse the workflow")
class BuildProvenanceTest(unittest.TestCase):
    def run_provenance(self, badging=None, subject="feat(android): example", code="2"):
        _, jobs = load()
        step = next(step for step in jobs["build"]["steps"] if step.get("id") == "provenance")
        script = re.search(r"<<'PY'\n(.*?)\nPY(?:\n|$)", step["run"], re.DOTALL)
        self.assertIsNotNone(script, "Execute the actual inline provenance program from the workflow")
        if badging is None:
            badging = "package: name='app.ovrly' versionCode='2' versionName='0.1.0'\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            outputs = root / "outputs"
            result = subprocess.run(
                [sys.executable, "-", code, "a" * 40, "b" * 64, badging, subject],
                input=script[1], text=True, capture_output=True, cwd=root,
                env={**os.environ, "GITHUB_OUTPUT": str(outputs), "GITHUB_RUN_ID": "123",
                     "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW_SHA": "c" * 40},
            )
            path = root / "out/build-provenance.json"
            provenance = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            output = outputs.read_text(encoding="utf-8") if outputs.exists() else ""
            return result, provenance, output

    def test_provenance_matches_publisher_contract_and_prints_review_hash(self):
        result, provenance, output = self.run_provenance(subject="subject\r\nsha256=injected")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(provenance, {
            "schema": "ovrly-build-provenance/v1", "code": 2, "source_sha": "a" * 40,
            "unsigned_sha256": "b" * 64, "version_name": "0.1.0", "package": "app.ovrly",
            "run_id": 123, "run_attempt": 1, "workflow_sha": "c" * 40,
        })
        self.assertEqual(output.splitlines(), [
            "sha256=" + "b" * 64, "version_name=0.1.0", "subject=subject sha256=injected",
        ])
        self.assertIn("SHA-256=" + "b" * 64, result.stdout)

    def test_bad_apk_metadata_produces_no_success_outputs(self):
        cases = [
            "package: name='appXovrly' versionCode='2' versionName='0.1.0'\n",
            "package: name='app.ovrly' versionCode='3' versionName='0.1.0'\n",
            "package: name='app.ovrly' versionCode='2' versionName='0.1.0'\napplication-debuggable\n",
            "package: name='app.ovrly' versionCode='2' versionName='0.1.0\nsha256=injected'\n",
            "",
        ]
        for badging in cases:
            with self.subTest(badging=badging):
                result, provenance, output = self.run_provenance(badging=badging)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::", result.stderr)
                self.assertIsNone(provenance)
                self.assertEqual(output, "")

    def test_version_code_bounds_are_inclusive(self):
        for code in ("1", "2100000000", "0", "2100000001"):
            with self.subTest(code=code):
                badging = f"package: name='app.ovrly' versionCode='{code}' versionName='0.1.0'\n"
                result, provenance, _ = self.run_provenance(badging=badging, code=code)
                self.assertEqual(result.returncode == 0, code in ("1", "2100000000"))
                if result.returncode == 0:
                    self.assertEqual(provenance["code"], int(code))


@unittest.skipIf(yaml is None, "PyYAML is required to parse the workflow")
class AndroidSigningCITest(unittest.TestCase):
    def setUp(self):
        self.data, jobs = load(ANDROID_WORKFLOW)
        self.job = jobs["android"]
        self.steps = {step["name"]: step for step in self.job["steps"]}

    def test_fixture_follows_android_checks_and_preserves_change_gate(self):
        names = list(self.steps)
        self.assertLess(names.index("Build, unit test and lint"), names.index("Build unsigned release fixture"))
        self.assertLess(names.index("Build unsigned release fixture"), names.index("Verify release signing with ephemeral key"))
        for name in ("Build unsigned release fixture", "Verify release signing with ephemeral key"):
            self.assertEqual(self.steps[name]["if"], "steps.changes.outputs.android == 'true'")
        self.assertIn("-Povrly.versionCode=2", self.steps["Build unsigned release fixture"]["run"])
        self.assertIn(":app:assembleRelease", self.steps["Build unsigned release fixture"]["run"])

    def test_fixture_has_no_production_credentials_or_apk_uploads(self):
        self.assertEqual(self.data["permissions"], {"contents": "read"})
        self.assertNotIn("environment", self.job)
        self.assertNotIn("secrets.", yaml.safe_dump(self.job))
        for step in self.job["steps"]:
            if step.get("uses", "").startswith("actions/upload-artifact@"):
                self.assertNotIn(".apk", step["with"]["path"])
                self.assertNotIn("outputs/apk", step["with"]["path"])

    def test_real_fixture_guard_rejects_skips_failures_and_empty_suites(self):
        import release_publish

        run = self.steps["Verify release signing with ephemeral key"]["run"]
        script = re.search(r"<<'PY'\n(.*?)\nPY(?:\n|$)", run, re.DOTALL)
        self.assertIsNotNone(script)
        self.assertIn("test_release_publish.RealToolSigningTest", script[1])
        self.assertIn("outputs/mapping/release/mapping.txt", run)
        for count, skipped, success in ((1, [], True), (1, [("fixture", "missing tool")], True),
                                        (0, [], True), (1, [], False)):
            with self.subTest(count=count, skipped=skipped, success=success):
                result = SimpleNamespace(testsRun=count, skipped=skipped, wasSuccessful=lambda: success)
                with (patch.dict(os.environ, {"OVRLY_UNSIGNED_APK": "fixture.apk"}),
                      patch.object(sys, "path", sys.path.copy()),
                      patch.object(release_publish, "tools_from_env", return_value={"aapt2": "fixture-tool"}),
                      patch.object(release_publish, "badging", return_value={"fixture": True}),
                      patch.object(release_publish, "verify_badging") as verify,
                      patch.object(unittest.defaultTestLoader, "loadTestsFromName"),
                      patch.object(unittest.TextTestRunner, "run", return_value=result)):
                    if count == 1 and not skipped and success:
                        exec(compile(script[1], str(ANDROID_WORKFLOW), "exec"), {})
                    else:
                        with self.assertRaises(SystemExit):
                            exec(compile(script[1], str(ANDROID_WORKFLOW), "exec"), {})
                    verify.assert_called_once_with({"fixture": True}, 2)


if __name__ == "__main__":
    unittest.main()
