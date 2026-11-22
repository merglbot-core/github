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


def location_identity(loc, locations):
    symbolic = loc["symbolic"]
    route = list(symbolic["route"]["route"])
    feature = loc["concrete"]["feature"]
    feature_kind = symbolic.get("feature_kind")
    fragment = (feature_kind.get("Subfeature", {}).get("fragment")
                if isinstance(feature_kind, dict) else None)
    for i, part in enumerate(route):
        if i == 0 or route[i - 1] != {"Key": "steps"} or "Index" not in part:
            continue
        for context in locations:
            parent = context["symbolic"]
            if (parent["kind"] != "Hidden" or parent["key"] != symbolic["key"]
                    or parent["route"]["route"] != route[:i + 1]):
                continue
            # The scanner supplies the containing step including id, name, if,
            # env and shell. Preserve the context on both sides of the scalar.
            step = context["concrete"]["feature"]
            if feature and step.count(feature) == 1:
                before, after = step.split(feature)
                route[i] = {"Step": [before, after]}
            break
    return route, fragment if fragment is not None else feature


def blocking_findings(report, renames=None):
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
            path = symbolic["key"]["Local"]["verbatim_path"]
            route, feature = location_identity(loc, item["locations"])
            locations.append(json.dumps([
                (renames or {}).get(path, path), route, feature, symbolic["annotation"],
            ], ensure_ascii=False, sort_keys=True))
        if not locations:
            raise ValueError("Blocking finding has no primary location")
        findings[(rule, tuple(sorted(locations)))] += 1
    return findings


def introduced(base_report, head_report, renames=None):
    return blocking_findings(head_report) - blocking_findings(base_report, renames)


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
            if not check_syntax:
                continue
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
    findings = []
    for path in paths:
        result = subprocess.run([*args, path], cwd=directory, capture_output=True)
        if result.returncode:
            # A repaired workflow can have an unparseable merge-base version.
            # Only accept the pinned scanner's input-parse error for that file.
            if (not check_syntax and result.returncode == 3
                    and b"failed to parse input:" in result.stderr
                    and b"error: no inputs collected" in result.stderr):
                continue
            result.check_returncode()
        report = json.loads(result.stdout)
        if not isinstance(report, list):
            raise ValueError("Expected a zizmor json-v1 report")
        findings.extend(report)
    return findings


def workflow_paths(changes):
    fields = iter(changes.split(b"\0"))
    base_paths, head_paths, renames = set(), set(), {}
    for status in fields:
        if not status:
            break
        source = next(fields)
        if status.startswith(b"R"):
            dest = next(fields)
            renames[os.fsdecode(source)] = os.fsdecode(dest)
        else:
            dest = source
        if status != b"A":
            base_paths.add(source)
        head_paths.add(dest)
    return base_paths, head_paths, renames


def main(base_sha, severity):
    if severity not in {"unknown", "informational", "low", "medium", "high"}:
        raise ValueError("Unexpected zizmor severity")
    base = subprocess.check_output(["git", "merge-base", base_sha, "HEAD"], text=True).strip()
    changes = subprocess.check_output([
        "git", "diff", "--name-status", "--find-renames", "--diff-filter=AMRT", "-z",
        base, "HEAD", "--", ".github/workflows/",
    ])
    base_paths, head_paths, renames = workflow_paths(changes)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        baseline = scan_revision(base, root / "base", severity, base_paths, check_syntax=False)
        candidate = scan_revision("HEAD", root / "head", severity, head_paths, check_syntax=True)
    new = introduced(baseline, candidate, renames)
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
