"""Validate a separately admitted recovery against its original install record."""
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def prepare(recovery, expected_binding, *, home, journal_bytes, state_bytes, parse_journal):
    if digest(canonical(recovery)) != expected_binding:
        raise ValueError('recovery_binding_mismatch')
    if recovery.get('schema') != 'cost934.adoption.recovery.v1':
        raise ValueError('unsupported_recovery')
    original = recovery['original_plan']
    original_binding = digest(canonical(original))
    if original_binding != recovery['original_binding']:
        raise ValueError('original_plan_binding_mismatch')
    if original.get('schema') != 'cost934.adoption.plan.v1':
        raise ValueError('unsupported_original_plan')
    home = Path(home)
    installation = home / '.merglbot/gh-cost-wave3/adoptions' / original_binding
    if recovery['journal']['path'] != str(installation / 'journal.jsonl'):
        raise ValueError('foreign_install_journal')
    if digest(journal_bytes) != recovery['journal']['sha256']:
        raise ValueError('install_journal_changed')
    if digest(state_bytes) != recovery['current_state_sha256']:
        raise ValueError('recovery_state_changed_prepare_fresh_plan')
    events, _ = parse_journal(journal_bytes, original_binding)
    receipts = [event['receipt'] for event in events if event.get('phase') == 'native_transaction']
    if not receipts:
        raise ValueError('no_native_receipt_manual_disposition_required')
    receipt = receipts[-1]
    rows = receipt.get('rows', [])
    expected = original['preimages']
    if set(expected) != {'code', 'caller'} or len(rows) != 2:
        raise ValueError('incomplete_pair_manual_disposition_required')
    backup = Path(receipt['backup_dir'])
    if backup.parent != installation or not backup.name.startswith('session-context-'):
        raise ValueError('foreign_backup_directory')
    by_path = {row['target']: row for row in rows}
    if set(by_path) != {item['path'] for item in expected.values()}:
        raise ValueError('foreign_recovery_target')
    for key, item in expected.items():
        row = by_path[item['path']]
        if row['previous'] != item['snapshot']:
            raise ValueError('foreign_backup_preimage')
        if row['candidate']['sha256'] != original['postimage_sha256'][key]:
            raise ValueError('foreign_installed_candidate')
    for index, row in enumerate(rows):
        if row['backup'] != str(backup / str(index)):
            raise ValueError('foreign_backup_file')
    return original, receipt, installation
