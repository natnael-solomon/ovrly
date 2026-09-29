"""Regression tests for dependency scanning and Android gate enforcement."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import android_quality
import dependency_audit as audit

ROOT = audit.ROOT
REQUIRED = __name__ == "__main__" or os.environ.get("OVRLY_REQUIRE_ANDROID_TOOLS") == "1"


class DependencyAuditTest(unittest.TestCase):
    def test_backend_export_is_frozen_hashed_and_includes_all_groups(self):
        def command(*args, **kwargs):
            if args[0] == "uv":
                self.assertTrue({"--frozen", "--all-groups", "--no-emit-project"}.issubset(args))
                self.assertNotIn("--no-hashes", args)
                Path(args[args.index("--output-file") + 1]).write_text("requests==2.34.2\n")
                return subprocess.CompletedProcess(args, 0, "", "")
            self.assertTrue({"--require-hashes", "--no-deps", "--disable-pip", "--strict"}.issubset(args))
            return subprocess.CompletedProcess(args, 0, '{"dependencies":[{"vulns":[]}]}', "")
        with patch.object(audit, "run", side_effect=command):
            audit.backend_audit()

    def test_repository_scan_uses_only_dependency_inputs_and_requires_both_stacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in audit.REQUIRED_LOCKFILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            tracked = subprocess.CompletedProcess([], 0, b"backend/uv.lock\0README.md\0")
            with patch.object(audit.subprocess, "run", return_value=tracked):
                self.assertEqual({root / name for name in audit.REQUIRED_LOCKFILES},
                                 set(audit.lockfiles(root)))
                (root / "backend/uv.lock").unlink()
                with self.assertRaisesRegex(audit.AuditError, "Missing"):
                    audit.lockfiles(root)

    def test_git_error_is_not_a_clean_empty_scan(self):
        with patch.object(audit.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                audit.lockfiles(ROOT)

    def test_osv_rejects_failures_empty_reports_and_missing_ecosystems(self):
        cases = [
            (2, "", "service unavailable"),
            (1, '{"results":[]}', "vulnerable"),
            (0, '{"results":[]}', ""),
            (0, '{"results":[{"packages":[{"package":{"ecosystem":"PyPI"}}]}]}', ""),
        ]
        with patch.object(audit, "lockfiles", return_value=[ROOT / "backend/uv.lock"]):
            for code, output, error in cases:
                with self.subTest(code=code, output=output):
                    result = subprocess.CompletedProcess([], code, output, error)
                    with patch.object(audit, "run", return_value=result):
                        with self.assertRaises(audit.AuditError):
                            audit.osv_audit("fixture")

    def test_osv_rejects_findings_even_with_zero_exit_code(self):
        packages = [
            {"package": {"ecosystem": "PyPI", "name": "requests", "version": "2.19.1"},
             "vulnerabilities": [{"id": "fixture"}]},
            {"package": {"ecosystem": "Maven"}},
        ]
        result = subprocess.CompletedProcess([], 0, json.dumps({"results": [{"packages": packages}]}), "")
        with patch.object(audit, "lockfiles", return_value=[ROOT / "backend/uv.lock"]), \
                patch.object(audit, "run", return_value=result) as run:
            with self.assertRaisesRegex(audit.AuditError, "vulnerabilities"):
                audit.osv_audit("fixture")
            self.assertIn("--no-resolve", run.call_args.args)
            self.assertIn("--no-call-analysis=all", run.call_args.args)
            self.assertNotIn("--recursive", run.call_args.args)

    def test_osv_requires_a_nonempty_result_for_every_input(self):
        inputs = [ROOT / name for name in audit.REQUIRED_LOCKFILES]
        sources = [
            {"source": {"path": str(path), "type": "lockfile"},
             "packages": [{"package": {"ecosystem": ecosystem}}]}
            for path, ecosystem in zip(inputs, ("PyPI", "Maven"))
        ]
        with patch.object(audit, "lockfiles", return_value=inputs):
            result = subprocess.CompletedProcess([], 0, json.dumps({"results": sources}), "")
            with patch.object(audit, "run", return_value=result):
                audit.osv_audit("fixture")
            sources[0]["packages"].extend(sources[1]["packages"])
            for results in (sources[:1], [sources[0], {**sources[1], "packages": []}]):
                with self.subTest(results=results):
                    result.stdout = json.dumps({"results": results})
                    with patch.object(audit, "run", return_value=result):
                        with self.assertRaisesRegex(audit.AuditError, "omitted dependency inputs"):
                            audit.osv_audit("fixture")

    def test_findings_print_only_package_coordinates_and_advisory_ids(self):
        report = {"results": [{"packages": [{
            "package": {"name": "requests", "version": "2.19.1", "ecosystem": "PyPI"},
            "vulnerabilities": [{"id": "GHSA-fixture", "details": "long advisory " * 1000}],
        }]}]}
        result = subprocess.CompletedProcess([], 1, json.dumps(report), "")
        with patch.object(audit, "lockfiles", return_value=[ROOT / "backend/uv.lock"]), \
                patch.object(audit, "run", return_value=result):
            with self.assertRaises(audit.AuditError) as caught:
                audit.osv_audit("fixture")
        self.assertEqual("OSV vulnerabilities:\nrequests@2.19.1: GHSA-fixture",
                         str(caught.exception))

    def test_export_failure_prevents_backend_audit(self):
        failure = subprocess.CompletedProcess([], 2, "", "lockfile missing")
        with patch.object(audit, "run", return_value=failure) as run:
            with self.assertRaisesRegex(audit.AuditError, "export"):
                audit.backend_audit()
            self.assertEqual(1, run.call_count)

    def test_backend_rejects_incomplete_reports(self):
        for packages in ([], [{}], [{"skip_reason": "not audited"}]):
            def command(*args, **kwargs):
                if args[0] == "uv":
                    Path(args[args.index("--output-file") + 1]).write_text("requests==2.34.2\n")
                    return subprocess.CompletedProcess(args, 0, "", "")
                return subprocess.CompletedProcess(
                    args, 0, json.dumps({"dependencies": packages}), "")
            with self.subTest(packages=packages), patch.object(audit, "run", side_effect=command):
                with self.assertRaisesRegex(audit.AuditError, "missing or skipped"):
                    audit.backend_audit()

    def test_scanner_checksum_mismatch_cannot_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".github").mkdir()
            (root / ".github/osv-scanner.json").write_text(json.dumps({
                "version": "2.6.0", "sha256": {"linux_amd64": hashlib.sha256(b"expected").hexdigest()},
            }))
            cache = root / ".local/quality-tools/osv/2.6.0/osv-scanner_linux_amd64"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"corrupt")
            with patch.object(audit, "ROOT", root), \
                    patch.object(audit.platform, "system", return_value="Linux"), \
                    patch.object(audit.platform, "machine", return_value="x86_64"):
                with self.assertRaisesRegex(audit.AuditError, "checksum"):
                    with audit.osv_binary():
                        self.fail("An invalid scanner was exposed for execution")

    def test_android_runner_propagates_failure_and_requires_strict_verification(self):
        with patch.object(android_quality.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 7)) as run:
            self.assertEqual(7, android_quality.main())
            self.assertIn("--dependency-verification=strict", run.call_args.args[0])
            self.assertIn("qualityCheck", run.call_args.args[0])


@unittest.skipUnless(REQUIRED, "Native enforcement fixtures run in Quality checks")
class NativeAndroidGateTest(unittest.TestCase):
    def gradle(self, *args):
        return subprocess.run(
            android_quality.gradle_command(*args),
            cwd=ROOT / "android", text=True, capture_output=True, timeout=600, encoding="utf-8",
        )

    @unittest.skipUnless(os.name == "nt", "Windows executable lookup policy")
    def test_wrapper_runs_when_current_directory_lookup_is_disabled(self):
        with patch.dict(os.environ, {"NoDefaultCurrentDirectoryInExePath": "1"}):
            result = self.gradle("--version")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"Gradle \d+\.\d+")

    def test_real_linters_reject_new_findings_and_accept_clean_sources(self):
        # Both linters relativize source paths to the checkout, including on Windows.
        fixture_root = ROOT / "android/build"
        fixture_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="quality-fixture-", dir=fixture_root) as temporary:
            directory = Path(temporary)
            source = directory / "fixture/GateFixture.kt"
            source.parent.mkdir()
            init = directory / "fixture.gradle"
            init.write_text("""
gradle.projectsEvaluated {
    def fixture = new File(System.getenv("OVRLY_GATE_FIXTURE"))
    rootProject.tasks.named("detekt").configure { setSource(fixture) }
    rootProject.tasks.named("runKtlintCheckOverKotlinScripts").configure { setSource(fixture) }
}
""", encoding="utf-8")
            with patch.dict(os.environ, {"OVRLY_GATE_FIXTURE": str(source)}):
                source.write_text(
                    "package fixture\n\nimport androidx.compose.runtime.Composable\n\n"
                    "@Composable\nfun bad_name(){ try { error(\"fixture\") } catch (e: Exception) {} }\n",
                    encoding="utf-8",
                )
                result = self.gradle("--init-script", str(init), "--continue", "--rerun-tasks",
                                     "detekt", "ktlintKotlinScriptCheck")
                output = result.stdout + result.stderr
                self.assertNotEqual(0, result.returncode, output)
                self.assertIn("ComposableNaming", output)
                self.assertIn("EmptyCatchBlock", output)
                self.assertIn(":ktlintKotlinScriptCheck FAILED", output)
                report = ROOT / "android/build/reports/ktlint/ktlintKotlinScriptCheck"
                findings = (report / "ktlintKotlinScriptCheck.txt").read_text(encoding="utf-8")
                self.assertIn(directory.name, findings)
                self.assertIn("standard:curly-spacing", findings)
                source.write_text(
                    "package fixture\n\nfun increment(value: Int): Int = value + 1\n",
                    encoding="utf-8",
                )
                result = self.gradle("--init-script", str(init), "--rerun-tasks",
                                     "detekt", "ktlintKotlinScriptCheck")
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_gradle_accepts_pinned_bytes_and_rejects_changed_checksum(self):
        metadata = ROOT / "android/gradle/verification-metadata.xml"
        tree = ET.parse(metadata)
        namespace = {"v": tree.getroot().tag.partition("}")[0].removeprefix("{")}
        component = tree.find(".//v:component[@group='org.jetbrains'][@name='annotations']", namespace)
        self.assertIsNotNone(component)
        version = component.attrib["version"]
        with tempfile.TemporaryDirectory(prefix="ovrly-verification-") as temporary:
            project = Path(temporary)
            (project / "gradle").mkdir()
            (project / "settings.gradle").write_text("rootProject.name = 'verification-fixture'\n")
            (project / "build.gradle").write_text(
                "repositories { mavenCentral() }\nconfigurations { fixture }\n"
                f"dependencies {{ fixture 'org.jetbrains:annotations:{version}' }}\n"
                "tasks.register('resolveFixture') { doLast { configurations.fixture.files } }\n"
            )
            destination = project / "gradle/verification-metadata.xml"
            shutil.copyfile(metadata, destination)
            result = self.gradle("--project-dir", str(project), "resolveFixture")
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            jar = component.find(f"v:artifact[@name='annotations-{version}.jar']", namespace)
            self.assertIsNotNone(jar)
            for checksum in jar.findall("v:sha256", namespace):
                checksum.set("value", "0" * 64)
                for trusted in list(checksum):
                    checksum.remove(trusted)
            ET.register_namespace("", namespace["v"])
            tree.write(destination, encoding="utf-8", xml_declaration=True)
            result = self.gradle("--project-dir", str(project), "resolveFixture")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("Dependency verification failed", result.stdout + result.stderr)


@unittest.skipUnless(REQUIRED, "Native vulnerability fixtures run in Quality checks")
class NativeVulnerabilityGateTest(unittest.TestCase):
    def test_known_vulnerable_public_package_fails_both_scanners(self):
        with tempfile.TemporaryDirectory(prefix="ovrly-vulnerable-dependency-") as temporary:
            requirements = Path(temporary) / "requirements.txt"
            requirements.write_text("requests==2.19.1\n", encoding="utf-8")
            pip = audit.run(sys.executable, "-m", "pip_audit", "--requirement", str(requirements),
                            "--no-deps", "--disable-pip", "--strict", "--progress-spinner", "off",
                            "--format=json")
            self.assertEqual(1, pip.returncode, pip.stdout + pip.stderr)
            self.assertTrue(json.loads(pip.stdout)["dependencies"][0]["vulns"])
            with audit.osv_binary() as binary:
                osv = audit.run(str(binary), "scan", "source", "--no-resolve",
                                "--no-call-analysis=all", "--config", str(ROOT / "osv-scanner.toml"),
                                f"--lockfile=:{requirements}", "--format=json")
            self.assertEqual(1, osv.returncode, osv.stdout + osv.stderr)
            self.assertTrue(any(package.get("vulnerabilities") for source in
                                json.loads(osv.stdout)["results"] for package in source["packages"]))


if __name__ == "__main__":
    unittest.main()
