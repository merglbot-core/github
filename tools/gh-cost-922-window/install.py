"""Install the reviewed window guard and verified billing-only follow-ups."""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

BASE = Path.home() / ".merglbot/gh-cost-910"
HOLD = Path.home() / ".claude/merglbot-preauth/OWNER_HOLD"
HERE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix=".billing-window-", dir=path.parent)
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


def verify_followups(keys):
    if len(keys) > 20 or len(set(keys)) != len(keys):
        raise ValueError("follow-up batch must be unique and at most 20 PRs")
    # This is a real request's core header, not the rate_limit endpoint.
    result = subprocess.run(["gh", "api", "-i", "user", "--jq", ".login"],
                            capture_output=True, text=True, timeout=30, check=True)
    match = re.search(r"(?im)^x-ratelimit-remaining:\s*(\d+)", result.stdout)
    if not match or int(match[1]) < 2000 + len(keys) + 5:
        raise RuntimeError("shared core reserve unavailable")
    records = {}
    for key in keys:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*", key):
            raise ValueError("invalid PR key")
        repo, number = key.rsplit("#", 1)
        projection = ('{merged,merged_at,merge_sha:.merge_commit_sha,base_ref:.base.ref,'
                      'scope_ok:(.body|test("(?i)(refs|closes|fixes|resolves) +merglbot-core/github#921\\\\b"))}')
        result = subprocess.run(["gh", "api", f"repos/{repo}/pulls/{number}", "--jq", projection],
                                capture_output=True, text=True, timeout=30, check=True)
        record = json.loads(result.stdout)
        if record.get("merged") is not True or record.get("scope_ok") is not True \
                or record.get("base_ref") != "main" \
                or not re.fullmatch(r"[0-9a-f]{40}", record.get("merge_sha", "")):
            raise ValueError("follow-up is not a main merge referencing #921")
        instant = dt.datetime.fromisoformat(record["merged_at"].replace("Z", "+00:00"))
        if instant.tzinfo is None or instant > dt.datetime.now(dt.timezone.utc):
            raise ValueError("invalid merged_at")
        records[key] = {field: record[field] for field in ("merged_at", "merge_sha", "base_ref")}
        records[key]["scope_issue"] = "merglbot-core/github#921"
    return records


def install(expected_code, expected_state, followups, dry_run=False):
    if HOLD.exists():
        raise RuntimeError("OWNER_HOLD present")
    lock = BASE / "autopilot/lock"
    lock.mkdir()
    try:
        code, state_path = BASE / "autopilot.py", BASE / "state.json"
        old_code, old_state = code.read_bytes(), state_path.read_bytes()
        if digest(old_code) != expected_code or digest(old_state) != expected_state:
            raise RuntimeError("source or state changed; revalidate")
        spec = importlib.util.spec_from_file_location("window_patch", HERE / "patch_autopilot.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        new_code = module.patch(old_code.decode()).encode()
        state = json.loads(old_state)
        if state.get("closed_at") or state.get("billing", {}).get("posted_at"):
            raise RuntimeError("published/closed program requires reconciliation")
        registered = state.setdefault("billing_followups", {})
        for key, record in followups.items():
            if key in state["prs"] or (key in registered and registered[key] != record):
                raise RuntimeError("follow-up registry conflicts")
            registered[key] = record
        new_state = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()
        facts = dict(code_before=digest(old_code), code_after=digest(new_code),
                     state_before=digest(old_state), state_after=digest(new_state))
        if dry_run:
            return dict(dry_run=True, **facts)
        if HOLD.exists():
            raise RuntimeError("OWNER_HOLD appeared")
        backup = BASE / "backups" / ("billing-window-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        backup.mkdir(parents=True, mode=0o700)
        atomic(backup / "autopilot.py", old_code)
        atomic(backup / "state.json", old_state)
        atomic(backup / "manifest.json", (json.dumps(facts) + "\n").encode())
        try:
            atomic(code, new_code)
            atomic(state_path, new_state)
            if digest(code.read_bytes()) != facts["code_after"] or digest(state_path.read_bytes()) != facts["state_after"]:
                raise RuntimeError("installation readback mismatch")
        except Exception:
            # No tick can run under the held lock; restore both saved inputs.
            atomic(code, old_code)
            atomic(state_path, old_state)
            raise
        return dict(backup=str(backup), **facts)
    finally:
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-code", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--followup", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # Verify live GitHub before the lock, then compare exact fresh state under it.
    followups = verify_followups(args.followup)
    print(json.dumps(install(args.expected_code, args.expected_state, followups, args.dry_run), sort_keys=True))


if __name__ == "__main__":
    main()
