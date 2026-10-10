import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / ".github/workflows/contracts.yml").read_text(encoding="utf-8")
# On Windows "bash" may resolve to the WSL launcher; prefer Git's bash when it exists.
BASH = next((p for p in (r"C:\Program Files\Git\bin\bash.exe",) if os.name == "nt" and Path(p).exists()),
            shutil.which("bash") or "bash")


def run_script(script, env, cwd=None):
    """Run a workflow step script in bash with the given extra environment."""
    exports = "".join(f"export {key}='{value}'\n" for key, value in env.items())
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, newline="\n") as handle:
        handle.write(exports + script)
        path = handle.name
    try:
        return subprocess.run([BASH, Path(path).as_posix()], cwd=cwd, capture_output=True, text=True)
    finally:
        os.unlink(path)


def dedent_run(block):
    return "\n".join(line[10:] for line in block.splitlines())


class ContractWorkflowTest(unittest.TestCase):
    def test_stack_triggers_and_main_only_push(self):
        pr = re.search(r"^  pull_request:\n((?:^    .*\n)+)", SOURCE, re.M).group(1)
        self.assertNotRegex(pr, r"\b(?:branches|branches-ignore|paths|paths-ignore):")
        for event in ("opened", "synchronize", "reopened", "edited", "ready_for_review"):
            self.assertIn(event, pr)
        self.assertIn("  push:\n    branches: [main]", SOURCE)
        self.assertNotIn("pull_request_target", SOURCE)

    def test_read_only_pinned_tools_and_main_only_cache_writes(self):
        self.assertIn("permissions:\n  contents: read", SOURCE)
        self.assertNotIn("secrets.", SOURCE)
        for uses in re.findall(r"uses: (\S+)", SOURCE):
            self.assertRegex(uses, r"^[\w/-]+@[0-9a-f]{40}$")
        self.assertIn("persist-credentials: false", SOURCE)
        self.assertIn("save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}", SOURCE)
        self.assertIn("cache-read-only: true", SOURCE)
        self.assertNotIn("cache-read-only: ${{", SOURCE)
        self.assertIn("retention-days: 7", SOURCE)

    def test_tools_are_pinned_and_oasdiff_is_checksum_verified(self):
        self.assertRegex(SOURCE, r"OASDIFF_VERSION: '\d+\.\d+\.\d+'")
        self.assertRegex(SOURCE, r"OASDIFF_SHA256: [0-9a-f]{64}\n")
        self.assertRegex(SOURCE, r"SPECTRAL_VERSION: '\d+\.\d+\.\d+'")
        self.assertIn("sha256sum --check --strict", SOURCE)
        self.assertIn('@stoplight/spectral-cli@${SPECTRAL_VERSION}', SOURCE)
        self.assertIn("--fail-severity warn", SOURCE)
        self.assertIn("uv sync --frozen --group contracts", SOURCE)

    def test_server_job_runs_every_contract_gate(self):
        server = SOURCE.split("  server:\n", 1)[1].split("  android:\n", 1)[0]
        for command in (
            "packages/contracts/validate.py",
            "packages/contracts/openapi_check.py",
            "packages/contracts/roundtrip.py --check",
            "packages/contracts/compat.py",
            "../packages/contracts/tests",
            "tests/test_contract_roundtrip.py",
            "mypy --strict",
            "ruff check ../packages/contracts",
        ):
            self.assertIn(command, server, command)
        self.assertIn("--oasdiff /tmp/oasdiff/oasdiff", server)
        self.assertIn("git -C .. archive \"$BASE_SHA\" packages/contracts", server)

    def test_server_step_paths_resolve_from_their_working_directory(self):
        """Every repository path a server step passes to python/pytest/mypy/ruff must exist
        relative to that step's working-directory (the Contract tests step once pointed at
        ../tests/... from backend/, which does not exist)."""
        server = SOURCE.split("  server:\n", 1)[1].split("  android:\n", 1)[0]
        default = re.search(r"^    defaults:\n      run:\n(?:        .*\n)*", server, re.M)
        self.assertIsNotNone(default)
        self.assertNotIn("working-directory", default.group(0))
        steps = re.split(r"^      - name: ", server, flags=re.M)[1:]
        checked = 0
        for step in steps:
            run = re.search(r"^        run: (>-\n((?:          .*\n)+)|(.*)\n)", step, re.M)
            if not run:
                continue
            command = run.group(2) or run.group(3) or ""
            workdir = re.search(r"^        working-directory: (\S+)", step, re.M)
            base = ROOT / workdir.group(1) if workdir else ROOT
            for token in command.split():
                if not re.match(r"^(\.\./)?(packages|tests|backend|\.github)/", token):
                    continue
                self.assertTrue((base / token).exists(), f"{token} relative to {base}")
                checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_android_job_runs_the_contract_tests_against_the_same_checkout(self):
        android = SOURCE.split("  android:\n", 1)[1].split("  result:\n", 1)[0]
        self.assertIn(":app:testDebugUnitTest --tests 'app.ovrly.contract.*'", android)
        self.assertIn("--dependency-verification=strict", android)
        self.assertNotIn("VOXIDE_LIVE", android)

    def test_result_requires_both_sides(self):
        result = SOURCE.split("  result:\n", 1)[1]
        self.assertIn("name: Contract checks\n    if: ${{ always() }}", result)
        self.assertIn("needs: [server, android]", result)
        script = dedent_run(result.split("        run: |\n", 1)[1])
        cases = [
            ("success", "success", 0),
            ("failure", "success", 1),
            ("success", "failure", 1),
            ("cancelled", "success", 1),
            ("skipped", "success", 1),
            ("success", "skipped", 1),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary, "summary").as_posix()
            for server, android, expected in cases:
                with self.subTest(server=server, android=android):
                    run = run_script(script, {"SERVER": server, "ANDROID": android,
                                              "GITHUB_STEP_SUMMARY": summary})
                    self.assertEqual(expected, run.returncode, run.stdout + run.stderr)

    def test_base_resolution_script_handles_pr_push_and_missing(self):
        server = SOURCE.split("  server:\n", 1)[1].split("  android:\n", 1)[0]
        step = server.split("      - name: Resolve comparison base\n", 1)[1]
        script = dedent_run(step.split("        run: |\n", 1)[1].split("\n\n", 1)[0])
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e"]
            subprocess.run([*git, "init", "-q"], check=True)
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "one"], check=True)
            first = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True,
                                   text=True, check=True).stdout.strip()
            subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "two"], check=True)
            output = Path(temporary, "out")
            env = {"GITHUB_OUTPUT": output.as_posix(),
                   "GITHUB_STEP_SUMMARY": Path(temporary, "summary").as_posix()}
            for base, before, expected_sha, code in (
                (first, "", first, 0),
                ("", first, first, 0),
                ("", "0" * 40, first, 0),
                ("deadbeef" * 5, "", None, 1),
            ):
                with self.subTest(base=base, before=before):
                    output.write_text("")
                    run = run_script(script, {**env, "BASE_SHA": base, "BEFORE_SHA": before}, cwd=repo)
                    self.assertEqual(code, run.returncode, run.stdout + run.stderr)
                    if expected_sha:
                        self.assertIn(f"sha={expected_sha}", output.read_text())


if __name__ == "__main__":
    unittest.main()
