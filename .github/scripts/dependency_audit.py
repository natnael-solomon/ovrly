"""Audit resolved dependencies, never private files or the developer's environment."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_LOCKFILES = ("backend/uv.lock", "android/gradle/verification-metadata.xml")
LOCK_NAMES = {
    "uv.lock", "verification-metadata.xml", "gradle.lockfile", "buildscript-gradle.lockfile",
    "Pipfile.lock", "poetry.lock", "pdm.lock", "pylock.toml", "requirements.txt",
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "Cargo.lock", "go.mod",
    "Gemfile.lock", "gems.locked", "composer.lock", "pubspec.lock", "mix.lock", "conan.lock",
    "packages.lock.json", "renv.lock", "cabal.project.freeze", "stack.yaml.lock",
}
MAX_BINARY_BYTES = 256 * 1024 * 1024


class AuditError(RuntimeError):
    """An incomplete or failed dependency audit."""


def run(*args, cwd=ROOT, timeout=300):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=timeout,
                          encoding="utf-8")


def require_success(result, label):
    if result.returncode:
        raise AuditError(f"{label} failed (exit {result.returncode}):\n"
                         f"{result.stdout}\n{result.stderr}")


def backend_audit():
    with tempfile.TemporaryDirectory(prefix="ovrly-pip-audit-") as temporary:
        requirements = Path(temporary) / "requirements.txt"
        export = run("uv", "export", "--project", str(ROOT / "backend"), "--frozen",
                     "--all-groups", "--no-emit-project", "--output-file", str(requirements))
        require_success(export, "Locked dependency export")
        if not requirements.is_file() or "==" not in requirements.read_text(encoding="utf-8"):
            raise AuditError("Locked dependency export produced no pinned requirements")
        result = run(sys.executable, "-m", "pip_audit", "--requirement", str(requirements),
                     "--require-hashes", "--no-deps", "--disable-pip", "--strict",
                     "--progress-spinner", "off", "--format", "json")
        if result.returncode not in (0, 1):
            require_success(result, "Backend dependency audit")
        report = json.loads(result.stdout)
        packages = report["dependencies"]
        findings = [
            f"{package['name']}=={package['version']}: {vulnerability['id']}"
            for package in packages for vulnerability in package.get("vulns", [])
        ]
        if findings:
            raise AuditError("Backend vulnerabilities:\n" + "\n".join(sorted(set(findings))))
        require_success(result, "Backend dependency audit")
        if not packages or any("skip_reason" in package or "vulns" not in package
                               for package in packages):
            raise AuditError("Backend audit contains missing or skipped dependency results")
        print(f"Backend audit: {len(packages)} locked packages, no known vulnerabilities.")


def lockfiles(repository):
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=repository, capture_output=True,
                             check=True, timeout=30).stdout
    paths = {Path(os.fsdecode(name)) for name in tracked.split(b"\0") if name}
    # New verification metadata must also be checked before its first commit.
    paths.update(Path(name) for name in REQUIRED_LOCKFILES)
    selected = sorted(path for path in paths if path.name in LOCK_NAMES)
    for path in selected:
        candidate = repository / path
        if not candidate.is_file() or candidate.is_symlink():
            raise AuditError(f"Missing or symlinked dependency input: {path}")
        if not candidate.resolve().is_relative_to(repository.resolve()):
            raise AuditError(f"Dependency input leaves the checkout: {path}")
    return [repository / path for path in selected]


@contextmanager
def osv_binary():
    manifest = json.loads((ROOT / ".github/osv-scanner.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise AuditError("Invalid OSV-Scanner version")
    arch = {"amd64": "amd64", "x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower()
    )
    system = platform.system().lower()
    digest = manifest["sha256"].get(f"{system}_{arch}")
    if digest is None:
        raise AuditError(f"Unsupported OSV-Scanner platform: {system}/{arch}")
    if shutil.disk_usage(ROOT).free < 2 * 1024 ** 3:
        raise AuditError("OSV-Scanner requires 2 GiB free; no data was deleted")
    name = f"osv-scanner_{system}_{arch}" + (".exe" if system == "windows" else "")
    cache = ROOT / ".local/quality-tools/osv" / version / name
    if cache.exists():
        if cache.stat().st_size > MAX_BINARY_BYTES:
            raise AuditError("Cached OSV-Scanner exceeds the size limit")
        data = cache.read_bytes()
    else:
        url = f"https://github.com/google/osv-scanner/releases/download/v{version}/{name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_BINARY_BYTES + 1)
    if len(data) > MAX_BINARY_BYTES or hashlib.sha256(data).hexdigest() != digest:
        raise AuditError("OSV-Scanner checksum/size mismatch; nothing was executed")
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as stream:
            staged = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                staged.replace(cache)
            finally:
                staged.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="ovrly-osv-") as temporary:
        binary = Path(temporary) / name
        binary.write_bytes(data)
        binary.chmod(0o700)
        yield binary


def osv_audit(binary, repository=ROOT):
    inputs = lockfiles(repository)
    result = run(str(binary), "scan", "source", "--no-resolve", "--no-call-analysis=all",
                 "--config", str(ROOT / "osv-scanner.toml"), "--format=json", "--all-packages",
                 *(f"--lockfile=:{path}" for path in inputs))
    if result.returncode not in (0, 1):
        require_success(result, "OSV repository dependency audit")
    report = json.loads(result.stdout)
    packages = [package for source in report["results"] for package in source["packages"]]
    findings = [
        f"{package['package']['name']}@{package['package']['version']}: {vulnerability['id']}"
        for package in packages for vulnerability in package.get("vulnerabilities", [])
    ]
    if findings:
        raise AuditError("OSV vulnerabilities:\n" + "\n".join(sorted(set(findings))))
    require_success(result, "OSV repository dependency audit")
    ecosystems = {package["package"]["ecosystem"] for package in packages}
    if not {"PyPI", "Maven"}.issubset(ecosystems):
        raise AuditError("OSV did not report dependencies from both backend and Android")
    reported_paths = {
        (repository / source["source"]["path"]).resolve()
        for source in report["results"]
        if source["packages"] and source["source"]["type"] == "lockfile"
    }
    missing = sorted(str(path.relative_to(repository)) for path in inputs
                     if path.resolve() not in reported_paths)
    if missing:
        raise AuditError("OSV omitted dependency inputs: " + ", ".join(missing))
    print(f"OSV audit: {len(inputs)} dependency files, {len(packages)} packages, "
          "no known vulnerabilities.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope", choices=("backend", "osv"))
    args = parser.parse_args()
    try:
        if args.scope == "backend":
            backend_audit()
        else:
            with osv_binary() as binary:
                osv_audit(binary)
        return 0
    except (AuditError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"ERROR: dependency audit did not pass: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
