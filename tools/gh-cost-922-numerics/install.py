"""Code-only installation under the existing autopilot lock; never rewind state."""
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(expected_code, expected_state, dry_run=False):
    shared = load("window_install", HERE.parent / "gh-cost-922-window/install.py")
    patcher = load("numeric_patch", HERE / "patch_autopilot.py")
    if shared.HOLD.exists():
        raise RuntimeError("OWNER_HOLD present")
    lock = shared.BASE / "autopilot/lock"
    lock.mkdir()
    try:
        code, state = shared.BASE / "autopilot.py", shared.BASE / "state.json"
        old_code, old_state = code.read_bytes(), state.read_bytes()
        if shared.digest(old_code) != expected_code or shared.digest(old_state) != expected_state:
            raise RuntimeError("autopilot source/state drift")
        captured = json.loads(old_state)
        billing = captured.get("billing", {})
        if not isinstance(billing, dict):
            raise RuntimeError("invalid billing state")
        if captured.get("closed_at") or billing.get("posted_at") or billing.get("closed_at"):
            raise RuntimeError("published/closed billing acceptance requires reconciliation")
        new_code = patcher.patch(old_code.decode()).encode()
        facts = dict(code_before=expected_code, code_after=shared.digest(new_code), state_unchanged=expected_state)
        if dry_run:
            return dict(dry_run=True, **facts)
        if shared.HOLD.exists():
            raise RuntimeError("OWNER_HOLD appeared")
        backup = shared.BASE / "backups" / ("billing-numerics-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        backup.mkdir(parents=True, mode=0o700)
        shared.atomic(backup / "autopilot.py", old_code)
        shared.atomic(backup / "manifest.json", (json.dumps(facts) + "\n").encode())
        try:
            shared.atomic(code, new_code)
            if code.read_bytes() != new_code or state.read_bytes() != old_state:
                raise RuntimeError("installation readback/source-state mismatch")
        except Exception:
            shared.atomic(code, old_code)
            raise
        return dict(backup=str(backup), **facts)
    finally:
        lock.rmdir()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-code", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(args.expected_code, args.expected_state, args.dry_run), sort_keys=True))
