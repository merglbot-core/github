#!/usr/bin/env python3
"""Owner-only #934 adoption entrypoint; default mode is read-only preflight.

Run only from a reviewed, source-pinned owner-script-emit packet. POTVRZUJI and
manifest digest are process controls, not proof of who invoked this command.
Source/owner authority must be independently admitted by that packet.
"""
import argparse
import hashlib
import types
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys


def helper(name):
    path = Path(__file__).with_name(name + '.py')
    module = types.ModuleType('_cost934_' + name)
    module.__file__ = str(path)
    # Execute source bytes; never trust or create an unpinned bytecode cache.
    exec(compile(read_bytes(path), str(path), 'exec'), module.__dict__)
    return module


def read_bytes(path, limit=4 * 1024 * 1024):
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('symlink_path_refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('regular_single_link_input_required')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('bounded_input_exceeded')
        final = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise ValueError('input_changed_during_read')
        return data


def hold_exists(home):
    directory = home / '.claude/merglbot-preauth'
    if not stat.S_ISDIR(directory.stat().st_mode) or directory.is_symlink():
        raise ValueError('owner_hold_directory_unavailable')
    try:
        (directory / 'OWNER_HOLD').lstat()
        return True
    except FileNotFoundError:
        return False


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--mode', choices=('preflight', 'install', 'rollback'), default='preflight')
    result.add_argument('--plan', type=Path, required=True)
    result.add_argument('--binding', required=True)
    result.add_argument('--native-bundle', type=Path, required=True)
    result.add_argument('confirmation', nargs='?')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not sys.flags.isolated:
        raise ValueError('use_python_isolated_mode')
    if args.mode in ('install', 'rollback') and args.confirmation != 'POTVRZUJI':
        raise ValueError('owner_confirmation_required')
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        raise ValueError('own_user_context_required_no_sudo')
    home = Path.home()
    plan = helper('adoption_plan')
    contract = helper('adoption_contract')
    preflight = helper('adoption_preflight')
    bundle = helper('adoption_bundle')
    native = helper('adoption_native')
    lifecycle = helper('adoption_lifecycle')
    locking = helper('adoption_transaction')
    journals = helper('adoption_journal')
    services = helper('adoption_service')
    raw_plan = read_bytes(args.plan)
    manifest = json.loads(raw_plan)
    if plan.digest(plan.canonical(manifest)) != args.binding:
        raise ValueError('manifest_binding_mismatch')
    tx = bundle.load(args.native_bundle)
    if args.mode == 'rollback':
        return recover_owner(args, manifest, home, tx, native, lifecycle, locking, journals, services)
    source = read_bytes(Path(__file__).with_name('acceptance_observer.py'))
    rows, state_before = preflight.verify(manifest, args.binding, home=home,
        source_bytes=source, read_bytes=read_bytes, native_snapshot=tx.snapshot,
        prepare_plan=plan.prepare, validate_transition=contract.validate_transition,
        owner_hold_exists=lambda: hold_exists(home))
    base = home / '.merglbot/gh-cost-wave3'
    state_path = base / 'acceptance_observer_state.json'
    caller = home / 'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'
    old_args = plistlib.loads(read_bytes(caller))['ProgramArguments']
    contract.validate_previous_program(read_bytes(base / 'acceptance_observer.py'),
                                      old_args, home=str(home))
    new_args = plistlib.loads(next(row['source_bytes'] for row in rows if row['target'] == str(caller)))['ProgramArguments']
    run = lambda command, timeout: subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    service = services.Service(os.geteuid(), caller, old_args, new_args, run=run)
    service.verify_old_idle()
    lock_path = base / 'acceptance_observer.lock'
    with locking.observer_lock(lock_path):
        if read_bytes(state_path) != state_before:
            raise ValueError('state_changed_after_preflight')
    if args.mode == 'preflight':
        print(json.dumps({'state': 'preflight_passed', 'binding': args.binding,
                          'host_mutations': 0, 'installation_authorized': False}))
        return 0
    # Exclusive plan reservation, never reuse a consumed install directory.
    root = base / 'adoptions'
    locking.no_symlink(root)
    root.mkdir(mode=0o700, exist_ok=True)
    installation = root / args.binding
    installation.mkdir(mode=0o700)
    journal = journals.Journal(installation / 'journal.jsonl', args.binding)
    state_read = lambda: read_bytes(state_path)

    def guard():
        if hashlib.sha256(state_read()).hexdigest() != manifest['preserve_state']['sha256']:
            raise ValueError('state_pin_changed')
        if read_bytes(args.plan) != raw_plan or hold_exists(home):
            raise ValueError('plan_changed_or_owner_hold')
        if hashlib.sha256(read_bytes(Path(__file__).with_name('acceptance_observer.py'))).hexdigest() != plan.OBSERVER_SHA256:
            raise ValueError('observer_source_changed')
        for row in rows:
            locking.no_symlink(Path(row['target']))

    try:
        result = lifecycle.install(service=service,
            lock=lambda: locking.observer_lock(lock_path), guard=guard,
            state_read=state_read,
            replace_files=lambda: native.install(tx, rows, str(home), installation,
                guard=guard, state_read=state_read,
                journal=lambda receipt: journal.emit({'phase': 'native_transaction', 'receipt': receipt})),
            journal=journal.emit)
        print(json.dumps(result))
        return 0 if result['state'] == 'installed_loaded_verified' else 2
    finally:
        journal.close()


def recover_owner(args, recovery, home, tx, native, lifecycle, locking, journals, services):
    recovery_module = helper('adoption_recovery')
    base = home / '.merglbot/gh-cost-wave3'
    state_path = base / 'acceptance_observer_state.json'
    original_binding = recovery['original_binding']
    if len(original_binding) != 64 or any(c not in '0123456789abcdef' for c in original_binding):
        raise ValueError('invalid_original_binding')
    expected_journal = base / 'adoptions' / original_binding / 'journal.jsonl'
    if recovery['journal']['path'] != str(expected_journal):
        raise ValueError('foreign_journal')
    old_journal = read_bytes(expected_journal)
    original, receipt, installation = recovery_module.prepare(recovery, args.binding,
        home=home, journal_bytes=old_journal, state_bytes=read_bytes(state_path),
        parse_journal=journals.parse)
    native.validate_rows(receipt['rows'], str(home))
    if hold_exists(home):
        raise ValueError('owner_hold')
    tx.rollback_preflight(receipt)
    caller = home / 'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'
    old_caller_row = next(row for row in receipt['rows'] if row['target'] == str(caller))
    old_args = plistlib.loads(read_bytes(Path(old_caller_row['backup'])))['ProgramArguments']
    old_code_row = next(row for row in receipt['rows']
                        if row['target'] == str(base / 'acceptance_observer.py'))
    helper('adoption_contract').validate_previous_program(
        read_bytes(Path(old_code_row['backup'])), old_args, home=str(home))
    current_args = plistlib.loads(read_bytes(caller))['ProgramArguments']
    service = services.Service(os.geteuid(), caller, old_args, current_args,
        run=lambda command, timeout: subprocess.run(command, capture_output=True, text=True, timeout=timeout))
    locking.no_symlink(installation)
    recovery_dir = installation / ('recovery-' + args.binding)
    recovery_dir.mkdir(mode=0o700)
    journal = journals.Journal(recovery_dir / 'journal.jsonl', args.binding)
    recovery_bytes = read_bytes(args.plan)

    def guard():
        if hashlib.sha256(read_bytes(state_path)).hexdigest() != recovery['current_state_sha256']:
            raise ValueError('recovery_state_pin_changed')
        live_plan = read_bytes(args.plan)
        if recovery_module.digest(recovery_module.canonical(json.loads(live_plan))) != args.binding:
            raise ValueError('recovery_binding_changed')
        if hold_exists(home) or live_plan != recovery_bytes:
            raise ValueError('recovery_plan_changed_or_owner_hold')
        if read_bytes(expected_journal) != old_journal:
            raise ValueError('original_journal_changed')
        for row in receipt['rows']:
            locking.no_symlink(Path(row['target']))
            locking.no_symlink(Path(row['backup']))

    state_read = lambda: read_bytes(state_path)
    try:
        result = lifecycle.recover(service=service,
            lock=lambda: locking.observer_lock(base / 'acceptance_observer.lock'),
            guard=guard, state_read=state_read,
            restore_files=lambda: native.rollback(tx, receipt, str(home), guard=guard,
                state_read=state_read,
                journal=lambda row: journal.emit({'phase': 'native_rollback', 'receipt': row})),
            journal=journal.emit)
        print(json.dumps(result))
        return 0 if result['state'] == 'rollback_loaded_verified' else 2
    finally:
        journal.close()


if __name__ == '__main__':
    raise SystemExit(main())
