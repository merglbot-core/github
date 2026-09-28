"""Code-only #888 install using the protected #922 locking/backup/rollback path."""
import argparse
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).parent
BASE = Path.home() / ".merglbot/gh-cost"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install(expected_code, expected_state, dry_run=False):
    shared = load("window_install", HERE.parent / "gh-cost-922-window/install.py")
    shared.BASE = BASE
    numeric = load("numeric_install", HERE.parent / "gh-cost-922-numerics/install.py")
    patcher = load("wave888_patch", HERE / "patch_autopilot.py")
    numeric.load = lambda name, path: shared if name == "window_install" else patcher
    return numeric.install(expected_code, expected_state, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-code", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(install(args.expected_code, args.expected_state, args.dry_run), sort_keys=True))
