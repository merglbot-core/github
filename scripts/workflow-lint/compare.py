"""Compare zizmor json-v1 reports for the same workflow path across two revisions."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


# Blocking scope from github#961. Other audits are still reported by the scanner.
BLOCKING_RULES = frozenset({"template-injection", "excessive-permissions"})


def step_correspondence(old, new):
    """Map candidate indices to baseline indices within a single job."""
    mapping = {}
    used = set()

    def pair_unique(key):
        def positions(steps, excluded):
            result = {}
            for i, step in enumerate(steps):
                if i not in excluded:
                    value = key(step)
                    if value is not None:
                        result.setdefault(value, []).append(i)
            return result

        before = positions(old, used)
        after = positions(new, mapping)
        for value in before.keys() & after.keys():
            if len(before[value]) == len(after[value]) == 1:
                base, head = before[value][0], after[value][0]
                if compatible(base, head):
                    mapping[head] = base
                    used.add(base)

    def compatible(base, head):
        left, right = old[base], new[head]
        if left.get("id") and right.get("id") and left["id"] != right["id"]:
            return False
        ignored = {"id", "name", "run"}
        return ({k: v for k, v in left.items() if k not in ignored}
                == {k: v for k, v in right.items() if k not in ignored})

    pair_unique(lambda step: step.get("id"))
    pair_unique(lambda step: step.get("name"))
    # Anonymous steps at equal positions retain identity across edits. A
    # reorder cannot transfer an issue to a different anonymous position.
    if len(old) == len(new):
        for i in range(len(new)):
            if i not in mapping and i not in used and compatible(i, i):
                mapping[i] = i
                used.add(i)
    else:
        # Pair independently of unrelated changed siblings. Display names
        # and an added or removed ID do not change the step's executable data.
        def executable(step):
            return json.dumps({k: v for k, v in step.items() if k not in {"name", "id"}},
                              sort_keys=True)
        pair_unique(executable)
    return mapping


def job_steps(path):
    # Both supported GitHub-hosted images include Mike Farah's yq. The
    # expression is constant; the workflow is read only as YAML data.
    output = subprocess.check_output([
        "yq", "eval", "-o=json", ".jobs | with_entries(.value = (.value.steps // []))", str(path),
    ])
    return json.loads(output)


def injection_feature(feature, fragment):
    # A scanner subfeature identifies the expression, not the command that
    # consumes it. Keep the complete source, except for an unambiguous
    # literal echo prelude. Never interpret escapes, quoting or shell syntax.
    # Restrict the remainder to one simple echo/printf expression command;
    # otherwise keep the entire scanner feature, including its quoting.
    simple = re.fullmatch(
        r"(?:echo [a-zA-Z0-9_ .-]+(?:;[ \t]+|\n))+"
        r'((?:echo|printf) "\$\{\{ [a-zA-Z0-9_.]+ \}\}")',
        feature)
    return [fragment, simple.group(1) if simple else feature]


def blocking_findings(report, renames=None, step_maps=None):
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
            route = list(symbolic["route"]["route"])
            kind = symbolic.get("feature_kind")
            fragment = (kind.get("Subfeature", {}).get("fragment") if isinstance(kind, dict) else None)
            feature = loc["concrete"]["feature"]
            if fragment is not None and rule == "template-injection":
                feature = injection_feature(feature, fragment)
            if step_maps is not None and len(route) >= 4 and route[2] == {"Key": "steps"}:
                step_index = route[3]["Index"]
                base_index = step_maps.get((path, route[1]["Key"]), {}).get(step_index)
                route[3] = {"Index": base_index} if base_index is not None else {"New": step_index}
            locations.append(json.dumps([
                (renames or {}).get(path, path), route, feature, symbolic["annotation"],
            ], ensure_ascii=False, sort_keys=True))
        if not locations:
            raise ValueError("Blocking finding has no primary location")
        findings[(rule, tuple(sorted(locations)))] += 1
    return findings


def introduced(base_report, head_report, renames=None, step_maps=None):
    return blocking_findings(head_report, step_maps=step_maps) - blocking_findings(base_report, renames)


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
        step_maps = {}
        baseline_step_paths = {
            loc["symbolic"]["key"]["Local"]["verbatim_path"]
            for item in baseline if item["ident"] in BLOCKING_RULES and item["determinations"]["severity"] == "High"
            for loc in item["locations"] if loc["symbolic"]["kind"] == "Primary"
            and {"Key": "steps"} in loc["symbolic"]["route"]["route"]
        }
        for name in base_paths:
            previous = os.fsdecode(name)
            current = renames.get(previous, previous)
            old_path, new_path = root / "base" / previous, root / "head" / current
            if not (old_path.is_file() and new_path.is_file()):
                continue
            if previous not in baseline_step_paths:
                continue
            old_jobs = job_steps(old_path)
            for job, steps in job_steps(new_path).items():
                step_maps[current, job] = step_correspondence(old_jobs.get(job, []), steps)
        new = introduced(baseline, candidate, renames, step_maps)
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
