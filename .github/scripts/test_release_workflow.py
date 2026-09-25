"""Structural assertions over telegram-apk.yml: the trust boundaries live in the YAML, so test them."""

import unittest
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - the CI image ships python3-yaml; local envs may not
    yaml = None

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "telegram-apk.yml"


def load():
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
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
            self.assertEqual(checkout["with"]["sparse-checkout"], ".github/scripts", name)
            self.assertNotIn("gradlew", yaml.safe_dump(job), name)

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


if __name__ == "__main__":
    unittest.main()
