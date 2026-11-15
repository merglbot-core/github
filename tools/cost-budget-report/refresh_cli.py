"""Disabled-by-default refresh entrypoint for an owner-admitted capped invocation.

Deployment must wrap this entrypoint in capped-run (whole process tree, memory,
time and orphan cleanup) and pin all sibling source files. No timer is created.
"""
import argparse
from datetime import timedelta
import json
from pathlib import Path

ENDPOINT = 'https://api.github.com/enterprises/merglbot/settings/billing/budgets'


def main(argv=None, *, execute=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enabled', action='store_true')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--journal', type=Path)
    parser.add_argument('--refresh-after-hours', type=float)
    args = parser.parse_args(argv)
    if not args.enabled:
        print(json.dumps({'state':'disabled','request_count':0}))
        return 0
    if (not args.root or not args.root.is_absolute() or not args.journal or not args.journal.is_absolute()
            or args.refresh_after_hours is None or not 0 < args.refresh_after_hours <= 24):
        parser.error('enabled mode requires absolute admitted paths and explicit refresh threshold (0,24]')
    if execute is None:
        # The fixed package directory, not the caller checkout, supplies helpers.
        # Python -I removes cwd injection; insert only this pinned installed source.
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from refresh_transaction import refresh_once
        from bounded_transport import gh_get
        execute = lambda **kwargs: refresh_once(get=gh_get, **kwargs)
    try:
        result = execute(enabled=True,root=args.root,journal_path=args.journal,endpoint=ENDPOINT,
                         refresh_after=timedelta(hours=args.refresh_after_hours),duration=120)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        print(json.dumps({'state':'held_refresh_failed_preserve_evidence','retry':False}))
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0 if result.get('state') in ('disabled','reuse_existing_pair','published_verified') else 2


if __name__ == '__main__':
    raise SystemExit(main())
