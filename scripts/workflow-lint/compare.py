"""Compare zizmor json-v1 reports for the same workflow path across two revisions."""
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


# Blocking scope from github#961. Other audits are still reported by the scanner.
BLOCKING_RULES = frozenset({"template-injection", "excessive-permissions"})


def blocking_findings(report):
    if not isinstance(report, list):
        raise ValueError("Expected a zizmor json-v1 report")
    findings = Counter()
    for item in report:
        rule = item["ident"]
        severity = item["determinations"]["severity"]
        if rule not in BLOCKING_RULES or severity != "High":
            continue
        locations = []
        for loc in item["locations"]:
            symbolic = loc["symbolic"]
            if symbolic["kind"] != "Primary":
                continue
            # Keep file, logical route, and matched content. Line numbers and
            # byte offsets change when unrelated text is inserted earlier.
            locations.append(json.dumps([
                symbolic["key"]["Local"]["verbatim_path"], symbolic["route"]["route"],
                loc["concrete"]["feature"], symbolic["annotation"],
            ], ensure_ascii=False, sort_keys=True))
        if not locations:
            raise ValueError("Blocking finding has no primary location")
        findings[(rule, tuple(sorted(locations)))] += 1
    return findings


def introduced(base_report, head_report):
    return blocking_findings(head_report) - blocking_findings(base_report)


def scan_revision(revision, directory, severity, changed_paths, *, check_syntax):
    # Materialize only regular workflow blobs. Neither revision supplies executable
    # helpers, scanner configuration, symlinks, or a Git working directory.
    entries = subprocess.check_output([
        "git", "ls-tree", "-rz", revision, "--", ".github/workflows",
    ])
    paths = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        if name not in changed_paths:
            continue
        path = Path(os.fsdecode(name))
        if path.parent != Path(".github/workflows") or path.suffix not in {".yml", ".yaml"}:
            continue
        mode, kind, oid = meta.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("Expected a regular workflow file")
        dest = directory / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(subprocess.check_output(["git", "cat-file", "blob", oid]))
        paths.append(str(path))
    if not paths:
        return []
    if check_syntax:
        result = subprocess.run(["actionlint", "-format", "{{json .}}", *paths],
                                cwd=directory, capture_output=True)
        if result.returncode:
            diagnostics = json.loads(result.stdout)
            if result.returncode != 1 or not isinstance(diagnostics, list) or not diagnostics:
                raise ValueError("actionlint did not produce a complete report")
            print(f"::warning::{len(diagnostics)} actionlint diagnostics")
    args = ["zizmor", "--offline", "--no-progress", "--no-config", "--no-ignores",
            "--no-exit-codes", "--format", "json-v1"]
    if severity != "unknown":
        args.extend(["--min-severity", severity])
    return json.loads(subprocess.check_output([*args, *paths], cwd=directory, stderr=subprocess.PIPE))


def main(base_sha, severity):
    if severity not in {"unknown", "informational", "low", "medium", "high"}:
        raise ValueError("Unexpected zizmor severity")
    base = subprocess.check_output(["git", "merge-base", base_sha, "HEAD"], text=True).strip()
    changed_paths = set(subprocess.check_output([
        "git", "diff", "--name-only", "--no-renames", "-z", base, "HEAD", "--", ".github/workflows/",
    ]).split(b"\0"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline = scan_revision(base, root / "base", severity, changed_paths, check_syntax=False)
        candidate = scan_revision("HEAD", root / "head", severity, changed_paths, check_syntax=True)
    new = introduced(baseline, candidate)
    print(f"Workflow lint: {len(candidate)} findings at selected severity, "
          f"{sum(new.values())} new blocking findings")
    if new:
        for rule in sorted({rule for rule, _ in new}):
            print(f"::error::New high-severity workflow finding: {rule}")
    elif candidate:
        print("::warning::Workflow lint has advisory findings")
    return int(bool(new))


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
