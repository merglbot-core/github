"""Narrow #934 adapter over the separately pinned native transaction library.

The owner entrypoint must admit the docs bundle before passing its transaction
module here. No module path, shell command, or authority is accepted from CLI.
"""
from pathlib import Path


def targets(home):
    home = Path(home)
    if not home.is_absolute() or '..' in home.parts:
        raise ValueError('absolute_canonical_home_required')
    return {str(home / '.merglbot/gh-cost-wave3/acceptance_observer.py'),
            str(home / 'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist')}


def validate_rows(rows, home):
    if len(rows) != 2 or {row['target'] for row in rows} != targets(home):
        raise ValueError('exact_financial_observer_pair_required')


def install(tx, rows, home, backup_parent, *, guard, state_read, journal):
    """Caller holds the existing flock and proves the service is quiescent."""
    validate_rows(rows, home)
    before = state_read()

    def invariant():
        guard()
        if state_read() != before:
            raise ValueError('state_changed_during_adoption')

    invariant()
    # Existing tx handles native metadata, backup, staging, fsync and receipts.
    # A partial receipt is returned for owner recovery, never treated as done.
    return tx.install(rows, backup_parent, invariant_check=invariant, journal=journal)


def rollback(tx, receipt, home, *, guard, state_read, journal):
    """Restore only the admitted two files; preserve the CURRENT state bytes."""
    validate_rows(receipt['rows'], home)
    before = state_read()

    def invariant():
        guard()
        if state_read() != before:
            raise ValueError('state_changed_during_rollback')

    invariant()
    tx.rollback_preflight(receipt)
    return tx.rollback(receipt, invariant_check=invariant, journal=journal)
