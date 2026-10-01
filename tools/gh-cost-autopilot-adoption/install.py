"""Install the reviewed #888/#910 autopilot adoption (owner plan of 30 Sep 2026).

  code                    patch both autopilots and place cost_adoption.py next to them
  record-owner-exception  write the #895 owner exception onto its two DoD rows (888)
  release-hold            release #917's technical_hold once it is closed and Done (910)
  rollback                restore a backup, only while the live files are its after-image

Every write: the installer, patch, helpers and decision record equal the protected main
branch byte for byte (contents API at the live main SHA, recorded in the manifest), OWNER_HOLD
(checked twice), the autopilot's own mkdir lock, exact fresh SHA-256 of code and state, backup
with manifest, atomic write and readback. `--dry-run` skips the main comparison so a review
branch can be tried. Verification is the next natural tick; never run a tick by hand.
"""
import argparse
import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import zoneinfo

HOME = pathlib.Path.home()
HERE = pathlib.Path(__file__).resolve().parent
HOLD = HOME / ".claude/merglbot-preauth/OWNER_HOLD"
LAUNCHD_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"
TARGETS = {
    "888": {"base": HOME / ".merglbot/gh-cost", "patch": "patch_888",
            "adopted": "4b78d53032afe48ea0c76eb841cd0657110d3b35a588f2163d5007e4bf7711f6"},
    "910": {"base": HOME / ".merglbot/gh-cost-910", "patch": "patch_910",
            "adopted": "08477d8776ae0c8bc273007b5f2d66e6d6fda6304503362fdf9d32ca2bcf76ce"},
}
DECISION_895 = "https://github.com/merglbot-core/github/issues/895#issuecomment-5917584272"
ROWS_895 = {
    "895|merglbot-denatura/acquisition-analysis": ("merglbot-denatura/acquisition-analysis#170",
                                                   ["gitleaks / Secret Scanning"]),
    "895|merglbot-proteinaco/acquisition-analysis": ("merglbot-proteinaco/acquisition-analysis#184",
                                                     ["gitleaks"]),
}
PROJECT_66, ITEM_917, DONE_66 = "PVT_kwDODhoOm84Bkd8e", "PVTI_lADODhoOm84Bkd8ezg8ZjTM", "98236657"
RESERVE = 2000 + 40
MAIN_REPO = "merglbot-core/github"
SOURCE_FILES = ("install.py", "patch_autopilot.py", "cost_adoption.py", "decisions/895.cs.md")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gh_api(path):
    """One REST read through gh; raises on failure. Returns (json, core remaining)."""
    result = subprocess.run(["gh", "api", "-i", path], capture_output=True, text=True, timeout=60, check=True)
    head, body = re.split(r"\r?\n\r?\n", result.stdout, maxsplit=1)
    match = re.search(r"(?im)^x-ratelimit-remaining:\s*(\d+)", head)
    return json.loads(body), int(match[1]) if match else None


def gh_graphql(query):
    result = subprocess.run(["gh", "api", "graphql", "-f", "query=" + query],
                            capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout).get("data") or {}


def atomic(path, data):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, name = tempfile.mkstemp(prefix=".adoption-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def py39_ok(sources):
    """Compile with the interpreter launchd actually runs (3.9.6), not the installer's."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, text in sources.items():
            (pathlib.Path(tmp) / name).write_text(text)
        code = "import pathlib,sys\n[compile(p.read_text(), str(p), 'exec') for p in pathlib.Path(sys.argv[1]).glob('*.py')]"
        subprocess.run([LAUNCHD_PYTHON, "-c", code, tmp], check=True, capture_output=True, timeout=60)
    return True


def older_patchers(target, new_code):
    """Every older patcher aimed at this autopilot must refuse (raise) or leave the adopted code
    unchanged. A tool's aim is the BASE of its install.py; a tool without one counts for both."""
    mine = TARGETS[target]["base"].name
    report = {}
    for path in sorted(HERE.parent.glob("gh-cost-*/patch_autopilot.py")):
        if path.parent == HERE:
            continue
        installer = path.parent / "install.py"
        aim = re.search(r'BASE\s*=\s*Path\.home\(\)\s*/\s*"\.merglbot/([\w.-]+)"',
                        installer.read_text()) if installer.exists() else None
        if aim and aim.group(1) != mine:
            report[path.parent.name] = "aims at " + aim.group(1)
            continue
        module = load(path, "older_" + path.parent.name.replace("-", "_"))
        try:
            out = module.patch(new_code)
        except Exception as error:  # noqa: BLE001 - any refusal is the wanted outcome
            report[path.parent.name] = "refused: " + type(error).__name__
            continue
        if out != new_code:
            raise RuntimeError(f"{path.parent.name} would rewrite the adopted code; aborting")
        report[path.parent.name] = "no-op"
    return report


class Locked:
    def __init__(self, base):
        self.lock = base / "autopilot/lock"

    def __enter__(self):
        if HOLD.exists():
            raise RuntimeError("OWNER_HOLD present")
        self.lock.mkdir()  # FileExistsError: a live tick owns the state
        return self

    def __exit__(self, *exc):
        self.lock.rmdir()


def backup_dir(base, kind):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = base / "backups" / f"{kind}-{stamp}"
    path.mkdir(parents=True, mode=0o700)
    return path


def install_code(target, expected_code, expected_state, dry_run=False, source_sha=None):
    spec = TARGETS[target]
    base = spec["base"]
    patch = getattr(load(HERE / "patch_autopilot.py", "adoption_patch"), spec["patch"])
    helper_new = (HERE / "cost_adoption.py").read_bytes()
    with Locked(base):
        code_path, state_path, helper_path = base / "autopilot.py", base / "state.json", base / "cost_adoption.py"
        old_code, old_state = code_path.read_bytes(), state_path.read_bytes()
        if digest(old_code) != expected_code or digest(old_state) != expected_state:
            raise RuntimeError("source or state changed; read fresh digests")
        new_code = patch(old_code.decode()).encode()
        if new_code == old_code and helper_path.exists() and helper_path.read_bytes() == helper_new:
            return {"target": target, "noop": True, "code": digest(old_code)}
        if digest(old_code) != spec["adopted"]:
            raise RuntimeError("unreviewed pre-image: live code is not the adopted image")
        check_state(target, json.loads(old_state))
        compile(new_code, str(code_path), "exec")
        py39_ok({"autopilot.py": new_code.decode(), "cost_adoption.py": helper_new.decode()})
        facts = {"target": target, "code_before": digest(old_code), "code_after": digest(new_code),
                 "state": digest(old_state), "cost_adoption_after": digest(helper_new),
                 "cost_adoption_before": digest(helper_path.read_bytes()) if helper_path.exists() else None,
                 "older_patchers": older_patchers(target, new_code.decode()), "python39": "compiled",
                 "source_main_sha": source_sha}
        if dry_run:
            return dict(facts, dry_run=True)
        if HOLD.exists():
            raise RuntimeError("OWNER_HOLD appeared")
        backup = backup_dir(base, "adoption")
        atomic(backup / "autopilot.py", old_code)
        if helper_path.exists():
            atomic(backup / "cost_adoption.py", helper_path.read_bytes())
        atomic(backup / "manifest.json", (json.dumps(facts, indent=1) + "\n").encode())
        old_helper = helper_path.read_bytes() if helper_path.exists() else None
        try:
            atomic(helper_path, helper_new)
            atomic(code_path, new_code)
            if digest(code_path.read_bytes()) != facts["code_after"] \
                    or digest(helper_path.read_bytes()) != facts["cost_adoption_after"]:
                raise RuntimeError("installation readback mismatch")
        except Exception:
            atomic(code_path, old_code)
            if old_helper is None:
                helper_path.unlink()
            else:
                atomic(helper_path, old_helper)
            raise
        return dict(facts, backup=str(backup))


def source_matches_main(fetch=gh_api):
    """Return the live main SHA when every SOURCE_FILES entry equals it byte for byte."""
    branch, remaining = fetch(f"repos/{MAIN_REPO}/branches/main")
    if remaining is not None and remaining < RESERVE:
        raise RuntimeError("GitHub core budget below the shared reserve")
    sha = ((branch or {}).get("commit") or {}).get("sha") if isinstance(branch, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        raise RuntimeError("main branch unreadable")
    for name in SOURCE_FILES:
        data, _ = fetch(f"repos/{MAIN_REPO}/contents/tools/gh-cost-autopilot-adoption/{name}?ref={sha}")
        published = (base64.b64decode(data.get("content", "")) if isinstance(data, dict)
                     and data.get("encoding") == "base64" else None)
        if published != (HERE / name).read_bytes():
            raise RuntimeError(f"{name} differs from main {sha[:8]}: install only from the protected main branch")
    return sha


def check_state(target, state):
    """The adopted edits came with state edits; confirm they are present (read-only)."""
    dod, subs = state.get("dod", {}), state.get("subs", {})
    if target == "888":
        ok = bool((dod.get("892|merglbot-core/merglbot-admin") or {}).get("owner_exception"))
    else:
        pilots = [i for i in dod.values() if i.get("kind") == "arm64_pilot"]
        ok = (len(pilots) == 5 and (subs.get("917") or {}).get("technical_hold") is True
              and bool((dod.get("912|merglbot-core/forecast-engine") or {}).get("ignored_run_ids"))
              and not (subs.get("921") or {}).get("technical_hold"))
    if not ok:
        raise RuntimeError(f"{target}: state lacks the adopted edits")


def verify_decision(fetch=gh_api):
    """Before any lock: the decision comment is on #895 with the reviewed body, the PRs stayed
    closed unmerged and the gitleaks contexts are still required. Returns the exception records."""
    comment_id = DECISION_895.rsplit("-", 1)[1]
    comment, remaining = fetch(f"repos/merglbot-core/github/issues/comments/{comment_id}")
    if remaining is not None and remaining < RESERVE:
        raise RuntimeError("shared core reserve unavailable")
    body = comment.get("body", "").replace("\r\n", "\n").strip()
    expected = (HERE / "decisions/895.cs.md").read_text().strip()
    if not str(comment.get("issue_url", "")).endswith("/repos/merglbot-core/github/issues/895") or body != expected:
        raise RuntimeError("decision comment is not the reviewed #895 record")
    decided = dt.datetime.fromisoformat(comment["created_at"].replace("Z", "+00:00"))
    prague = decided.astimezone(zoneinfo.ZoneInfo("Europe/Prague"))
    records = {}
    for key, (pr_key, contexts) in ROWS_895.items():
        repo, number = pr_key.rsplit("#", 1)
        pr, _ = fetch(f"repos/{repo}/pulls/{number}")
        checks, _ = fetch(f"repos/{repo}/branches/main/protection/required_status_checks")
        required = {c.get("context") for c in checks.get("checks") or []}
        if pr.get("state") != "closed" or pr.get("merged") is not False or not set(contexts) <= required:
            raise RuntimeError(f"{pr_key}: PR merged/open or gitleaks context no longer required")
        records[key] = {
            "text": "Výjimka jako #892", "decided_at": comment["created_at"],
            "decided_at_prague": f"{prague.day}. {prague.month}. {prague.year}",
            "source": DECISION_895, "body_sha256": digest(body.encode()),
            "basis": ("PR zavřen bez merge 23. 9. po nálezech review; gitleaks zůstává samostatný "
                      "povinný check; neuskutečněná úspora zhruba 1 USD/měs. se nepočítá."),
            "pr": pr_key, "required_contexts": contexts}
    return records


def code_installed(target):
    """The live autopilot is the fully patched image (state records come after the code)."""
    spec = TARGETS[target]
    patch = getattr(load(HERE / "patch_autopilot.py", "adoption_patch"), spec["patch"])
    code = (spec["base"] / "autopilot.py").read_text()
    if digest(code.encode()) == spec["adopted"] or patch(code) != code:
        raise RuntimeError(f"{target}: install the adopted code first (install.py code)")


def rewrite_state(target, expected_state, change, kind, dry_run=False):
    """Apply `change(state)` under the lock; everything but the changed keys must stay equal."""
    base = TARGETS[target]["base"]
    code_installed(target)
    with Locked(base):
        path = base / "state.json"
        old = path.read_bytes()
        if digest(old) != expected_state:
            raise RuntimeError("state changed; read a fresh digest")
        state = json.loads(old)
        touched = change(state)
        new = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
        before = json.loads(old)
        for dotted in touched:
            section, key = dotted.split(":", 1)
            before[section].pop(key, None)
            state[section].pop(key, None)
        if before != state:
            raise RuntimeError("state change touched more than the declared keys")
        facts = {"target": target, "kind": kind, "state_before": digest(old), "state_after": digest(new),
                 "touched": touched}
        if dry_run:
            return dict(facts, dry_run=True)
        if HOLD.exists():
            raise RuntimeError("OWNER_HOLD appeared")
        backup = backup_dir(base, kind)
        atomic(backup / "state.json", old)
        atomic(backup / "manifest.json", (json.dumps(facts, indent=1) + "\n").encode())
        atomic(path, new)
        if digest(path.read_bytes()) != facts["state_after"]:
            atomic(path, old)
            raise RuntimeError("state readback mismatch")
        return dict(facts, backup=str(backup))


def record_owner_exception(expected_state, dry_run=False, fetch=gh_api):
    records = verify_decision(fetch)

    def change(state):
        for key, record in records.items():
            row = state["dod"][key]
            if row.get("met_at") or row.get("owner_exception"):
                raise RuntimeError(f"{key} already met or excepted")
            row["owner_exception"] = record
        return [f"dod:{key}" for key in records]
    return rewrite_state("888", expected_state, change, "owner-exception-895", dry_run)


def release_hold(expected_state, dry_run=False, fetch=gh_api, graphql=gh_graphql):
    issue, _ = fetch("repos/merglbot-core/github/issues/917")
    node = (graphql('query { node(id: "%s") { ... on ProjectV2Item { project { id } '
                    'fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue '
                    '{ optionId } } } } }' % ITEM_917) or {}).get("node") or {}
    if issue.get("state") != "closed" or issue.get("state_reason") != "completed" \
            or (node.get("project") or {}).get("id") != PROJECT_66 \
            or (node.get("fieldValueByName") or {}).get("optionId") != DONE_66:
        raise RuntimeError("#917 is not closed as completed and Done on Project 66")
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def change(state):
        record = state["subs"]["917"]
        if record.get("technical_hold") is not True:
            raise RuntimeError("#917 hold already released")
        record.update(technical_hold=False, board_done_at=now, hold_released_at=now,
                      hold_release_reason="#917 closed and Done after the owner-decided rollout (30 Sep 2026)")
        return ["subs:917"]
    return rewrite_state("910", expected_state, change, "release-hold-917", dry_run)


def rollback(target, backup):
    base, backup = TARGETS[target]["base"], pathlib.Path(backup)
    manifest = json.loads((backup / "manifest.json").read_text())
    with Locked(base):
        code_path, helper_path = base / "autopilot.py", base / "cost_adoption.py"
        if digest(code_path.read_bytes()) != manifest["code_after"]:
            raise RuntimeError("newer code exists; rollback refused")
        atomic(code_path, (backup / "autopilot.py").read_bytes())
        if (backup / "cost_adoption.py").exists():
            atomic(helper_path, (backup / "cost_adoption.py").read_bytes())
        elif helper_path.exists():
            helper_path.unlink()
        if digest(code_path.read_bytes()) != manifest["code_before"]:
            raise RuntimeError("rollback readback mismatch")
    return {"target": target, "restored": manifest["code_before"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    code = sub.add_parser("code")
    for target in TARGETS:
        code.add_argument(f"--expected-code-{target}", required=True)
        code.add_argument(f"--expected-state-{target}", required=True)
    code.add_argument("--dry-run", action="store_true")
    owner = sub.add_parser("record-owner-exception")
    owner.add_argument("--expected-state-888", required=True)
    owner.add_argument("--dry-run", action="store_true")
    hold = sub.add_parser("release-hold")
    hold.add_argument("--expected-state-910", required=True)
    hold.add_argument("--dry-run", action="store_true")
    back = sub.add_parser("rollback")
    back.add_argument("--target", choices=sorted(TARGETS), required=True)
    back.add_argument("--backup", required=True)
    args = vars(parser.parse_args())
    # Writes only from the protected main branch; a dry run may try a review branch.
    source = None if args.get("dry_run") or args["command"] == "rollback" else source_matches_main()
    if args["command"] == "code":
        # Both targets are validated before either is written, so a predictable refusal on the
        # second never leaves the first installed alone; each write re-validates its digests.
        checked = [install_code(t, args[f"expected_code_{t}"], args[f"expected_state_{t}"], True, source)
                   for t in TARGETS]
        result = checked if args["dry_run"] else [
            install_code(t, args[f"expected_code_{t}"], args[f"expected_state_{t}"], False, source)
            for t in TARGETS]
    elif args["command"] == "record-owner-exception":
        result = record_owner_exception(args["expected_state_888"], args["dry_run"])
    elif args["command"] == "release-hold":
        result = release_hold(args["expected_state_910"], args["dry_run"])
    else:
        result = rollback(args["target"], args["backup"])
    print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
