import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import release_ledger
from release_publish import (
    PublishError, SigningMaterial, publish_new_build, render_caption, verify_badging, verify_redelivery,
)

SHA = "a" * 40
SHA_B = "b" * 40
WF = "c" * 40
HEAD = "9" * 40
CERT = "d" * 64
OTHER_CERT = "e" * 64
TOOLS = {"aapt2": "/fake/aapt2", "apksigner": "/fake/apksigner"}


def badging_out(package="app.ovrly", code=2, debuggable=False):
    text = f"package: name='{package}' versionCode='{code}' versionName='0.1.0'\n"
    if debuggable:
        text += "application-debuggable\n"
    return text


class FakeRunner:
    """Simulates aapt2 and apksigner; 'signing' copies the unsigned file and appends a marker."""

    def __init__(self, code=2, cert=CERT, package="app.ovrly", debuggable=False, sign_fails=False):
        self.code, self.cert, self.package, self.debuggable, self.sign_fails = code, cert, package, debuggable, sign_fails
        self.calls = []

    def __call__(self, args, capture_output=True, text=True):
        self.calls.append(args)
        tool = Path(args[0]).name
        if tool == "aapt2":
            return SimpleNamespace(returncode=0, stdout=badging_out(self.package, self.code, self.debuggable), stderr="")
        if args[1] == "verify":
            return SimpleNamespace(returncode=0, stdout=f"Signer #1 certificate SHA-256 digest: {self.cert}\n", stderr="")
        if args[1] == "sign":
            if self.sign_fails:
                return SimpleNamespace(returncode=1, stdout="", stderr="keystore password was incorrect")
            out, src = Path(args[args.index("--out") + 1]), Path(args[-1])
            out.write_bytes(src.read_bytes() + b"SIGNED")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(args)


class FakeGitHub:
    def __init__(self, approvals_status=200):
        self.created = []
        self.approvals = [{"user": {"login": "natnael-solomon"}, "state": "approved", "comment": "ok"}]
        self.approvals_status = approvals_status

    def get(self, path):
        if path == "git/ref/heads/main":
            return {"object": {"sha": HEAD, "type": "commit"}}
        if path.endswith("/approvals"):
            if self.approvals_status != 200:
                from telegram_api import GitHubApiError
                raise GitHubApiError(self.approvals_status, "nope", "GET", path)
            return self.approvals
        raise AssertionError(path)

    def request(self, method, path, payload=None, ok=(200, 201)):
        self.created.append((path, payload))
        return {"sha": "t" * 40} if path == "git/tags" else {"ref": payload["ref"]}


def ledger_with(reservations=(), issuances=()):
    ledger = release_ledger.Ledger(floor=1, bootstrap={})
    for code, sha in reservations:
        ledger.reservations[code] = {"code": code, "source_sha": sha, "run_id": 10, "run_attempt": 1, "workflow_sha": WF, "requested_by": "dev"}
    for code, sha, cert in issuances:
        ledger.issuances[code] = {"code": code, "source_sha": sha, "run_id": 20, "signed_sha256": "f" * 64,
                                  "cert_sha256": cert, "package": "app.ovrly", "requested_by": "dev"}
    return ledger


def provenance(unsigned_sha, **over):
    base = {"schema": "ovrly-build-provenance/v1", "code": 2, "source_sha": SHA, "unsigned_sha256": unsigned_sha,
            "version_name": "0.1.0", "package": "app.ovrly", "run_id": 10, "run_attempt": 1, "workflow_sha": WF}
    base.update(over)
    return base


class PublishBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.unsigned = self.tmp / "unsigned.apk"
        self.unsigned.write_bytes(b"APK" * 100)
        from release_publish import sha256_of
        self.unsigned_sha = sha256_of(self.unsigned)
        self.prov = self.tmp / "build-provenance.json"
        self.write_prov()

    def write_prov(self, **over):
        self.prov.write_text(json.dumps(provenance(self.unsigned_sha, **over)))

    def cfg(self, **over):
        base = {
            "code": 2, "source_sha": SHA, "run_id": 10, "run_attempt": 1, "workflow_sha": WF, "actor": "natnael-solomon",
            "unsigned_apk": str(self.unsigned), "signed_apk": str(self.tmp / "signed.apk"),
            "build_provenance": str(self.prov), "signed_provenance": str(self.tmp / "signed-provenance.json"),
            "build_output_sha256": self.unsigned_sha, "version_name": "0.1.0",
            "keystore_b64": "QUJD", "store_password": "p", "key_alias": "k", "key_password": "p",
            "expected_cert_sha256": CERT, "observed_at": "2026-01-01T00:00:00Z",
            "tagger": {"name": "t", "email": "t@example.invalid"},
        }
        base.update(over)
        return base

    def test_happy_path_issues_before_returning(self):
        gh, runner = FakeGitHub(), FakeRunner()
        result = publish_new_build(gh, ledger_with([(2, SHA)]), self.cfg(), TOOLS, runner)
        self.assertEqual(result["code"], 2)
        self.assertEqual(result["cert_sha256"], CERT)
        self.assertEqual([p for p, _ in gh.created], ["git/tags", "git/refs"])
        self.assertEqual(gh.created[1][1]["ref"], "refs/tags/release-ledger/issue/2")
        record = json.loads(Path(self.cfg()["signed_provenance"]).read_text())
        self.assertEqual(record["approvals"][0]["user"], "natnael-solomon")
        self.assertEqual(record["approvals_observed_at"], "2026-01-01T00:00:00Z")
        self.assertNotIn("approved_at", record)
        self.assertTrue(any(a[1] == "sign" and "--v1-signing-enabled" in a for a in runner.calls))

    def test_old_build_approved_after_newer_delivery_is_refused_before_signing(self):
        gh, runner = FakeGitHub(), FakeRunner()
        ledger = ledger_with([(2, SHA), (3, SHA_B)], [(3, SHA_B, CERT)])
        with self.assertRaises(PublishError) as raised:
            publish_new_build(gh, ledger, self.cfg(), TOOLS, runner)
        self.assertIn("superseded", str(raised.exception))
        self.assertEqual(runner.calls, [])
        self.assertEqual(gh.created, [])

    def test_digest_and_provenance_mismatches(self):
        cfg_cases = {"output digest": {"build_output_sha256": "0" * 64}, "wrong code": {"code": 3}}
        for name, over in cfg_cases.items():
            with self.subTest(name=name), self.assertRaises(PublishError):
                publish_new_build(FakeGitHub(), ledger_with([(2, SHA), (3, SHA_B)]), self.cfg(**over), TOOLS, FakeRunner())
        prov_cases = {
            "schema": {"schema": "other/v9"}, "run_id": {"run_id": 11}, "run_attempt": {"run_attempt": 2},
            "workflow_sha": {"workflow_sha": SHA_B}, "source": {"source_sha": SHA_B}, "package": {"package": "com.x"},
            "digest": {"unsigned_sha256": "0" * 64},
        }
        for name, over in prov_cases.items():
            self.write_prov(**over)
            with self.subTest(name=name), self.assertRaises(PublishError):
                publish_new_build(FakeGitHub(), ledger_with([(2, SHA)]), self.cfg(), TOOLS, FakeRunner())

    def test_reservation_tuple_must_match_current_context(self):
        for key, value in (("run_id", 11), ("run_attempt", 2), ("workflow_sha", SHA_B), ("source_sha", SHA_B)):
            ledger = ledger_with([(2, SHA)])
            ledger.reservations[2][key] = value
            with self.subTest(key=key), self.assertRaises(PublishError) as raised:
                publish_new_build(FakeGitHub(), ledger, self.cfg(), TOOLS, FakeRunner())
            self.assertIn("different source, run, attempt or workflow", str(raised.exception))

    def test_size_guards_before_signing_and_before_issuance(self):
        import release_publish
        original = release_publish.MAX_UPLOAD_BYTES
        self.addCleanup(setattr, release_publish, "MAX_UPLOAD_BYTES", original)
        release_publish.MAX_UPLOAD_BYTES = 200        # unsigned is 300 bytes
        gh, runner = FakeGitHub(), FakeRunner()
        with self.assertRaises(PublishError) as raised:
            publish_new_build(gh, ledger_with([(2, SHA)]), self.cfg(), TOOLS, runner)
        self.assertIn("50 MB", str(raised.exception))
        self.assertEqual(runner.calls, [])
        release_publish.MAX_UPLOAD_BYTES = 303        # unsigned fits; signed (300 + 6) does not
        gh, runner = FakeGitHub(), FakeRunner()
        with self.assertRaises(PublishError) as raised:
            publish_new_build(gh, ledger_with([(2, SHA)]), self.cfg(), TOOLS, runner)
        self.assertIn("Signed APK", str(raised.exception))
        self.assertEqual(gh.created, [])
        self.assertFalse(Path(self.cfg()["signed_apk"]).exists())

    def test_badging_failures(self):
        for kw in ({"package": "com.other"}, {"code": 9}, {"debuggable": True}):
            with self.subTest(kw=kw), self.assertRaises(PublishError):
                publish_new_build(FakeGitHub(), ledger_with([(2, SHA)]), self.cfg(), TOOLS, FakeRunner(**kw))

    def test_cert_pin_mismatch_removes_signed_apk_and_does_not_issue(self):
        gh, runner = FakeGitHub(), FakeRunner(cert=OTHER_CERT)
        with self.assertRaises(PublishError) as raised:
            publish_new_build(gh, ledger_with([(2, SHA)]), self.cfg(), TOOLS, runner)
        self.assertIn("does not match", str(raised.exception))
        self.assertFalse(Path(self.cfg()["signed_apk"]).exists())
        self.assertEqual(gh.created, [])

    def test_cert_cannot_diverge_from_established_ledger_identity(self):
        self.write_prov(code=3)
        ledger = ledger_with([(2, SHA_B), (3, SHA)], [(2, SHA_B, OTHER_CERT)])
        with self.assertRaises(PublishError) as raised:
            publish_new_build(FakeGitHub(), ledger, self.cfg(code=3), TOOLS, FakeRunner(code=3))
        self.assertIn("cannot change silently", str(raised.exception))

    def test_signing_failure_does_not_leak_args(self):
        with self.assertRaises(PublishError) as raised:
            publish_new_build(FakeGitHub(), ledger_with([(2, SHA)]), self.cfg(), TOOLS, FakeRunner(sign_fails=True))
        self.assertIn("keystore password was incorrect", str(raised.exception))
        self.assertNotIn("--ks-pass", str(raised.exception))

    def test_audit_unavailable_is_recorded_not_hidden(self):
        gh = FakeGitHub(approvals_status=404)
        publish_new_build(gh, ledger_with([(2, SHA)]), self.cfg(), TOOLS, FakeRunner())
        record = json.loads(Path(self.cfg()["signed_provenance"]).read_text())
        self.assertIs(record["approvals_audit_available"], False)
        self.assertEqual(record["approvals"], [])

    def test_signing_material_is_private_and_cleaned(self):
        with SigningMaterial("QUJD", "s", "k") as m:
            self.assertEqual(m.keystore.read_bytes(), b"ABC")
            if os.name != "nt":
                self.assertEqual(os.stat(m.dir).st_mode & 0o777, 0o700)
                self.assertEqual(os.stat(m.keystore).st_mode & 0o777, 0o600)
            path = m.dir
        self.assertFalse(path.exists())

    def test_signing_material_enter_failure_leaves_no_directory(self):
        before = set(Path(tempfile.gettempdir()).glob("ovrly-sign-*"))
        with self.assertRaises(PublishError):
            SigningMaterial("not base64!", "s", "k").__enter__()
        material = SigningMaterial("QUJD", "s", "k")
        material._write = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(OSError):
            material.__enter__()
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("ovrly-sign-*")), before)


class RedeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.apk = self.tmp / "signed.apk"
        self.apk.write_bytes(b"SIGNEDBYTES")
        from release_publish import sha256_of
        self.sha = sha256_of(self.apk)

    def ledger(self, hwm=5, cert=CERT):
        ledger = release_ledger.Ledger(floor=1, bootstrap={})
        for code in range(2, hwm + 1):
            ledger.reservations[code] = {"code": code}
            ledger.issuances[code] = {"code": code, "source_sha": SHA, "run_id": 100 + code,
                                      "signed_sha256": self.sha if code == hwm else "9" * 64,
                                      "cert_sha256": cert, "package": "app.ovrly"}
        return ledger

    def expected(self, code=5, cert=CERT):
        return {"code": code, "signed_sha256": self.sha, "cert_sha256": cert}

    def test_current_issued_bytes_redeliver_without_signing(self):
        runner = FakeRunner(code=5)
        result = verify_redelivery(self.ledger(), self.expected(), self.apk, TOOLS, CERT, runner)
        self.assertEqual(result["code"], 5)
        self.assertFalse(any(a[1] == "sign" for a in runner.calls))

    def test_older_code_superseded_even_with_matching_bytes(self):
        ledger = self.ledger(); ledger.issuances[4]["signed_sha256"] = self.sha
        with self.assertRaises(PublishError) as raised:
            verify_redelivery(ledger, self.expected(code=4), self.apk, TOOLS, CERT, FakeRunner(code=4))
        self.assertIn("superseded", str(raised.exception))

    def test_pin_ledger_and_evidence_must_all_agree(self):
        with self.assertRaises(PublishError) as raised:
            verify_redelivery(self.ledger(), self.expected(), self.apk, TOOLS, OTHER_CERT, FakeRunner(code=5))
        self.assertIn("do not all agree", str(raised.exception))
        with self.assertRaises(PublishError):
            verify_redelivery(self.ledger(cert=OTHER_CERT), self.expected(), self.apk, TOOLS, CERT, FakeRunner(code=5))
        with self.assertRaises(PublishError):
            verify_redelivery(self.ledger(), self.expected(cert=OTHER_CERT), self.apk, TOOLS, CERT, FakeRunner(code=5))

    def test_different_bytes_signer_or_package_fail(self):
        with self.assertRaises(PublishError):
            verify_redelivery(self.ledger(), dict(self.expected(), signed_sha256="0" * 64), self.apk, TOOLS, CERT, FakeRunner(code=5))
        with self.assertRaises(PublishError):
            verify_redelivery(self.ledger(), self.expected(), self.apk, TOOLS, CERT, FakeRunner(code=5, cert=OTHER_CERT))
        with self.assertRaises(PublishError):
            verify_redelivery(self.ledger(), self.expected(), self.apk, TOOLS, CERT, FakeRunner(code=5, package="x.y"))

class CaptionTest(unittest.TestCase):
    def test_caption(self):
        text = render_caption(7, "0.1.0", SHA, "fix(x): y", "natnael-solomon", "https://github.com/o/r", "f" * 64)
        lines = text.split("\n")
        self.assertEqual(lines[0], f'<b><a href="https://github.com/o/r/commit/{SHA}">ovrly 0.1.0 · build 7</a></b>')
        self.assertIn("<b>SHA-256</b>  <code>ffffffffffffffff…</code>", text)
        self.assertIn("<b>By</b>  <i>sol</i>", text)
        self.assertNotIn("Note", text)
        self.assertIn("redelivery", render_caption(7, "0.1.0", SHA, "", "dev", "u", "f" * 64, redelivery=True))

    def test_caption_truncates_and_bounds(self):
        long_subject = "x" * 500
        text = render_caption(7, "0.1.0", SHA, long_subject, "dev", "u", "f" * 64)
        self.assertLessEqual(len(text), 1024)
        self.assertIn("…", text)
        self.assertNotIn("x" * 30, text)


class PublishRerunGuardTest(unittest.TestCase):
    """'Re-run failed jobs' can restart only publish; both modes must refuse before any side effect."""

    GOOD = {"GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/telegram-apk.yml@refs/heads/main", "GITHUB_RUN_ATTEMPT": "1"}

    def test_first_attempt_from_main_passes(self):
        from release_publish import guard_execution_context
        guard_execution_context(self.GOOD)

    def test_rerun_of_publish_job_is_refused(self):
        from release_publish import guard_execution_context
        from release_preflight import PreflightError
        with self.assertRaises(PreflightError) as raised:
            guard_execution_context({**self.GOOD, "GITHUB_RUN_ATTEMPT": "2"})
        self.assertIn("redeliver_run_id", str(raised.exception))

    def test_non_main_publish_context_is_refused(self):
        from release_publish import guard_execution_context
        from release_preflight import PreflightError
        with self.assertRaises(PreflightError):
            guard_execution_context({**self.GOOD, "GITHUB_REF": "refs/heads/feat/x"})
        with self.assertRaises(PreflightError):
            guard_execution_context({**self.GOOD, "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/telegram-apk.yml@refs/heads/feat/x"})

    def test_main_entry_points_guard_before_network(self):
        import release_publish
        from unittest.mock import patch
        env = {**self.GOOD, "GITHUB_RUN_ATTEMPT": "2", "MODE": "redeliver"}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(release_publish, "GitHubClient", side_effect=AssertionError("network touched")), \
             patch.object(release_publish, "TelegramClient", side_effect=AssertionError("telegram touched")):
            with self.assertRaises(SystemExit):
                release_publish.main()
            with self.assertRaises(SystemExit):
                release_publish.deliver_main()


class PinnedToolsTest(unittest.TestCase):
    """The resolver must ignore newer installed build-tools; the fixture and production share it."""

    def test_pinned_version_wins_over_newer_installed(self):
        from unittest.mock import patch
        from release_publish import tools_from_env
        sdk = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, sdk)
        for version in ("36.0.0", "37.0.0"):
            d = sdk / "build-tools" / version
            d.mkdir(parents=True)
            for name in ("apksigner", "aapt2"):
                for suffix in ((".bat", ".exe") if os.name == "nt" else ("",)):
                    (d / f"{name}{suffix}").write_text("")
        with patch.dict(os.environ, {"ANDROID_HOME": str(sdk)}, clear=False):
            os.environ.pop("BUILD_TOOLS_VERSION", None)
            tools = tools_from_env()
        self.assertEqual(tools["version"], "36.0.0")
        self.assertIn(os.sep + "36.0.0" + os.sep, tools["apksigner"])
        self.assertNotIn("37.0.0", tools["aapt2"])

    def test_missing_pinned_version_fails_even_if_newer_exists(self):
        from unittest.mock import patch
        from release_publish import tools_from_env
        sdk = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, sdk)
        (sdk / "build-tools" / "37.0.0").mkdir(parents=True)
        with patch.dict(os.environ, {"ANDROID_HOME": str(sdk)}, clear=False):
            os.environ.pop("BUILD_TOOLS_VERSION", None)
            with self.assertRaises(PublishError) as raised:
                tools_from_env()
        self.assertIn("36.0.0", str(raised.exception))


def _pinned_tools():
    """Same resolver production uses: the pinned build-tools version, never the newest installed."""
    from release_publish import tools_from_env
    if not (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")):
        local = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk")
        if Path(local, "build-tools").exists():
            os.environ["ANDROID_HOME"] = local
    try:
        return tools_from_env()
    except PublishError:
        return None


def _find_unsigned_apk():
    env = os.environ.get("OVRLY_UNSIGNED_APK")
    if env and Path(env).is_file():
        return Path(env)
    default = Path(__file__).resolve().parents[2] / "android/app/build/outputs/apk/release/app-release-unsigned.apk"
    return default if default.is_file() else None


@unittest.skipUnless(_pinned_tools() and _find_unsigned_apk() and shutil.which("keytool"),
                     "real-tool test needs the pinned build-tools, keytool and a built unsigned release APK")
class RealToolSigningTest(unittest.TestCase):
    """Signs the actual unsigned release APK with an ephemeral fixture key in a temp dir.

    Uses exactly the tool resolver production uses (pinned build-tools version), so a format or
    behaviour difference in a newer SDK cannot pass here and fail in the release job. The key is
    generated here, used once, and deleted with the directory. It is never installed, uploaded,
    committed or logged, and has no relationship to any production or developer identity.
    """

    def test_sign_verify_and_badging_with_ephemeral_key(self):
        tmp = Path(tempfile.mkdtemp(prefix="ovrly-fixture-"))
        # Not ignore_errors: a residual fixture key must fail the test, not pass silently.
        self.addCleanup(shutil.rmtree, tmp)
        keystore = tmp / "fixture.jks"
        store_pass, key_pass = "fixture-store-only", "fixture-key-only"    # distinct, like production
        subprocess.run(["keytool", "-genkeypair", "-storetype", "JKS", "-keystore", str(keystore),
                        "-storepass", store_pass, "-keypass", key_pass, "-alias", "fixture",
                        "-keyalg", "RSA", "-keysize", "2048", "-validity", "1",
                        "-dname", "CN=ovrly test fixture, O=not production"],
                       check=True, capture_output=True)
        import base64
        from release_publish import badging, sha256_of, sign_apk, signer_cert_sha256
        tools = _pinned_tools()
        version_out = subprocess.run([tools["apksigner"], "--version"], capture_output=True, text=True).stdout.strip()
        print(f"\n[real-tool] build-tools={tools['version']} apksigner={tools['apksigner']} apksigner-version={version_out}")
        unsigned = _find_unsigned_apk()
        info = badging(tools["aapt2"], unsigned)
        self.assertEqual(info["package"], "app.ovrly")
        self.assertFalse(info["debuggable"])
        with self.assertRaises(PublishError):
            signer_cert_sha256(tools["apksigner"], unsigned)   # genuinely unsigned

        signed = tmp / "signed.apk"
        b64 = base64.b64encode(keystore.read_bytes()).decode()
        with SigningMaterial(b64, store_pass, key_pass) as m:
            sign_apk(tools["apksigner"], unsigned, signed, m.keystore, "fixture", m.store_pass, m.key_pass)
        raw = subprocess.run([tools["apksigner"], "verify", "--print-certs", str(signed)], capture_output=True, text=True)
        print(f"[real-tool] verify rc={raw.returncode} stdout:\n{raw.stdout.strip()}")
        cert = signer_cert_sha256(tools["apksigner"], signed)
        self.assertRegex(cert, r"^[0-9a-f]{64}$")
        self.assertEqual(badging(tools["aapt2"], signed)["version_code"], info["version_code"])
        self.assertNotEqual(sha256_of(signed), sha256_of(unsigned))

        # Swapped passwords must be rejected: proves store/key wiring is not interchangeable.
        with SigningMaterial(b64, key_pass, store_pass) as m, self.assertRaises(PublishError):
            sign_apk(tools["apksigner"], unsigned, tmp / "swapped.apk", m.keystore, "fixture", m.store_pass, m.key_pass)
        print(f"[real-tool] unsigned={unsigned} sha256={sha256_of(unsigned)} versionCode={info['version_code']} fixture_cert={cert[:16]}…")


if __name__ == "__main__":
    unittest.main()
