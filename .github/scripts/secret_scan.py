"""Scan reachable history and tracked changes without exposing secret values."""

import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
HISTORY_OPTIONS = [
    "--all", "--full-history", "--diff-merges=first-parent", "--no-renames",
    "--no-ext-diff", "--no-textconv",
]

class ScanError(RuntimeError):
    """A safe diagnostic containing no scanner output or credential values."""


def verified_archive(data, digest):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ScanError("Gitleaks archive checksum mismatch; nothing was executed")
    return data


@contextmanager
def scanner():
    manifest = json.loads((ROOT / ".github/gitleaks.json").read_text())
    version = manifest["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ScanError("Invalid pinned Gitleaks version")
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower()
    )
    system = platform.system().lower()
    target = f"{system}_{arch}"
    digest = manifest["sha256"].get(target)
    if digest is None:
        raise ScanError(f"Unsupported Gitleaks platform: {target}")
    if shutil.disk_usage(ROOT).free < 2 * 1024 ** 3:
        raise ScanError("Gitleaks requires 2 GiB free; no data was deleted")
    suffix = "zip" if system == "windows" else "tar.gz"
    archive_name = f"gitleaks_{version}_{target}.{suffix}"
    cache = ROOT / ".local/quality-tools/gitleaks" / archive_name
    if cache.exists():
        if cache.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ScanError("Cached Gitleaks archive exceeds the size limit")
        data = verified_archive(cache.read_bytes(), digest)
    else:
        url = f"https://github.com/gitleaks/gitleaks/releases/download/v{version}/{archive_name}"
        print(f"Downloading checksum-pinned Gitleaks {version}.", flush=True)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise ScanError("Gitleaks download exceeded the size limit")
        verified_archive(data, digest)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as stream:
            staged = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                staged.replace(cache)
            finally:
                staged.unlink(missing_ok=True)
    name = "gitleaks.exe" if system == "windows" else "gitleaks"
    # Read one verified archive member; never extract arbitrary archive paths.
    if system == "windows":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            executable = archive.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember(name)
            if not member.isfile():
                raise ScanError("Gitleaks archive does not contain a regular executable")
            with archive.extractfile(member) as stream:
                executable = stream.read()
    with tempfile.TemporaryDirectory(prefix="ovrly-gitleaks-") as temporary:
        binary = Path(temporary) / name
        binary.write_bytes(executable)
        binary.chmod(0o700)
        yield binary


def git(repository, *args):
    result = subprocess.run(["git", *args], cwd=repository, capture_output=True, timeout=120)
    if result.returncode:
        raise ScanError("Git inspection failed; no clean scan was assumed")
    return result.stdout


def private_paths(repository):
    if git(repository, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise ScanError("Secret scanning requires full history; fetch --unshallow before retrying")
    index = git(repository, "ls-files", "--cached", "-z")
    history = git(repository, "log", *HISTORY_OPTIONS, "--format=", "--name-only", "-z")
    names = (index + history).split(b"\0")
    return sorted({os.fsdecode(name) for name in names if name
                   and PurePosixPath(os.fsdecode(name)).name.lower() == "voxide.local.properties"})


def scan(repository, binary, config=None):
    # Upstream loads source/.gitleaksignore even with an explicit ignore path.
    # Reject it rather than silently permitting unreviewed fingerprint bypasses.
    if (repository / ".gitleaksignore").exists():
        print("ERROR: .gitleaksignore is not permitted; use reviewed, narrow .gitleaks.toml rules")
        return 1
    prohibited = private_paths(repository)
    if prohibited:
        for name in prohibited:
            print(f"ERROR: prohibited private file in index/history: {json.dumps(name)}")
        return 1
    config = config or ROOT / ".gitleaks.toml"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GITLEAKS_")}
    failed = False
    with tempfile.TemporaryDirectory(prefix="ovrly-secret-reports-") as temporary:
        directory = Path(temporary)
        ignore = directory / "empty.ignore"
        ignore.write_text("")
        for label, options in (
            ("history", ["--log-opts=" + " ".join(HISTORY_OPTIONS)]),
            ("index", ["--staged"]),
            ("tracked working changes", ["--pre-commit"]),
        ):
            report = directory / "findings.json"
            report.unlink(missing_ok=True)
            result = subprocess.run([
                str(binary), "git", *options, "--config", str(config),
                "--redact=100", "--no-banner", "--no-color", "--log-level=error",
                "--ignore-gitleaks-allow", "--gitleaks-ignore-path", str(ignore),
                "--report-format=json", "--report-path", str(report), str(repository),
            ], cwd=repository, env=env, capture_output=True, timeout=180)
            # Never echo native output or upload reports: even parser/Git errors
            # can contain source text. Expose only location/rule metadata.
            if not report.exists():
                raise ScanError(f"Gitleaks {label} produced no report (exit {result.returncode})")
            findings = json.loads(report.read_text())
            if not isinstance(findings, list):
                raise ScanError("Gitleaks report must be an array")
            if result.returncode not in (0, 1) or (result.returncode == 1 and not findings):
                raise ScanError(f"Gitleaks {label} failed (exit {result.returncode})")
            if findings:
                failed = True
                for finding in findings:
                    location = json.dumps(finding["File"])
                    rule = json.dumps(finding["RuleID"])
                    line = int(finding["StartLine"])
                    print(f"ERROR: {label}: {location}:{line}, rule {rule}; value withheld")
            else:
                print(f"Gitleaks {label}: clean.")
    return int(failed)


def main():
    try:
        with scanner() as binary:
            return scan(ROOT, binary)
    except ScanError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, tarfile.TarError,
            zipfile.BadZipFile, KeyError):
        print("ERROR: secret scan could not complete. Check history, tool checksum, configuration "
              "and network access. Raw output withheld; no clean result was assumed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
