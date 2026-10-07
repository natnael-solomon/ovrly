"""Compare two builds of the same commit entry by entry and report any nondeterminism.

Usage: apk_reproducibility.py FIRST_DIR SECOND_DIR [--report report.md]

Each directory holds app-release-unsigned.apk and mapping.txt from one CI build. Identical bytes
pass. Otherwise every differing ZIP entry or ZIP header field is listed; entries matching
KNOWN_NONDETERMINISM pass with their documented reason, anything else fails.
"""

import argparse
import fnmatch
import hashlib
import sys
import zipfile
from pathlib import Path

ARTIFACTS = ("app-release-unsigned.apk", "mapping.txt")

# Reviewed exceptions: entry pattern -> reason. Keep in sync with docs/release-signing.md.
KNOWN_NONDETERMINISM = {}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def entries(apk):
    with zipfile.ZipFile(apk) as archive:
        return [
            {
                "name": info.filename, "sha256": sha256_bytes(archive.read(info)),
                "date_time": info.date_time, "compress_type": info.compress_type,
                "external_attr": info.external_attr, "extra": info.extra.hex(),
            }
            for info in archive.infolist()
        ]


def compare_apks(first, second):
    """Return a list of (entry, difference) pairs; empty when the archives match entry by entry."""
    left, right = entries(first), entries(second)
    left_by, right_by = {e["name"]: e for e in left}, {e["name"]: e for e in right}
    differences = []
    for name in sorted(left_by.keys() - right_by.keys()):
        differences.append((name, "only in the first build"))
    for name in sorted(right_by.keys() - left_by.keys()):
        differences.append((name, "only in the second build"))
    for name in sorted(left_by.keys() & right_by.keys()):
        a, b = left_by[name], right_by[name]
        for field in ("sha256", "date_time", "compress_type", "external_attr", "extra"):
            if a[field] != b[field]:
                differences.append((name, "content" if field == "sha256" else f"zip {field}"))
    common = [e["name"] for e in left if e["name"] in right_by]
    if common != [e["name"] for e in right if e["name"] in left_by]:
        differences.append(("(archive)", "entry order"))
    return differences


def known_reason(name):
    for pattern, reason in KNOWN_NONDETERMINISM.items():
        if fnmatch.fnmatchcase(name, pattern):
            return reason
    return None


def compare(first_dir, second_dir):
    """Return (ok, markdown report lines)."""
    lines = ["## Release reproducibility", ""]
    for name in ARTIFACTS:
        for directory in (first_dir, second_dir):
            if not (directory / name).is_file():
                return False, [*lines, f"Missing `{name}` in `{directory}`."]
    lines += ["| Artifact | First build SHA-256 | Second build SHA-256 | Same |", "|---|---|---|---|"]
    same = {}
    for name in ARTIFACTS:
        a, b = sha256_file(first_dir / name), sha256_file(second_dir / name)
        same[name] = a == b
        lines.append(f"| `{name}` | `{a}` | `{b}` | {'yes' if a == b else 'no'} |")
    lines.append("")
    ok = same["mapping.txt"]
    if not ok:
        lines.append("The R8 mappings differ, so code shrinking or obfuscation is not deterministic.")
    if same["app-release-unsigned.apk"]:
        lines.append("The unsigned APKs are byte-for-byte identical.")
        return ok, lines
    differences = compare_apks(first_dir / ARTIFACTS[0], second_dir / ARTIFACTS[0])
    if not differences:
        lines.append("All entries and ZIP headers match; only archive-level bytes (alignment or "
                     "central directory) differ.")
        return False, lines
    lines += ["| Entry | Difference | Known reason |", "|---|---|---|"]
    for name, difference in differences:
        reason = known_reason(name)
        ok = ok and reason is not None
        lines.append(f"| `{name}` | {difference} | {reason or '**unexplained**'} |")
    return ok, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    ok, lines = compare(args.first, args.second)
    report = "\n".join(lines) + "\n"
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    if not ok:
        print("::error::The two builds differ in ways not listed in KNOWN_NONDETERMINISM")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
