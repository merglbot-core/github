"""Pure, source-bound preparation of a #934 owner installation manifest.

A plan contains no owner authorization. The emitted owner command must bind its
canonical digest after independent source review and fresh native preflight.
"""
import hashlib
import json
import plistlib
from pathlib import PurePosixPath

OBSERVER_MERGE = 'cc77f82ce0ea79589dadea17789b9b54b4c06e1d'
OBSERVER_SHA256 = '84c2f384aadece84282278621868e6ee901ae73ea0653aade2e3c8a3952c91aa'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def prepare(*, home, objective, objective_sha256, code_bytes, caller_bytes,
            state_bytes, preimages, validate_transition):
    """Construct bounded caller bytes and a reviewable manifest without IO."""
    home = PurePosixPath(home)
    objective = PurePosixPath(objective)
    if not home.is_absolute() or not objective.is_absolute():
        raise ValueError('absolute_paths_required')
    if '..' in home.parts or '..' in objective.parts:
        raise ValueError('noncanonical_path')
    if digest(code_bytes) != OBSERVER_SHA256:
        raise ValueError('observer_source_pin_mismatch')
    if len(objective_sha256) != 64 or any(c not in '0123456789abcdef' for c in objective_sha256):
        raise ValueError('objective_pin_required')
    expected_paths = {
        'code': str(home / '.merglbot/gh-cost-wave3/acceptance_observer.py'),
        'caller': str(home / 'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'),
    }
    if set(preimages) != set(expected_paths):
        raise ValueError('exact_pair_required')
    for key, path in expected_paths.items():
        if preimages[key].get('path') != path:
            raise ValueError('foreign_target:' + key)
        if not isinstance(preimages[key].get('snapshot'), dict):
            raise ValueError('native_snapshot_required:' + key)
    if preimages['caller']['snapshot'].get('sha256') != digest(caller_bytes):
        raise ValueError('caller_preimage_mismatch')
    old = plistlib.loads(caller_bytes)
    new = dict(old)
    new['ProgramArguments'] = [str(home / '.merglbot/mem-guard/capped-run'),
        '--max-gb', '2', '--timeout', '600', '--', '/opt/homebrew/bin/python3',
        expected_paths['code'], '--objective', str(objective)]
    candidate = plistlib.dumps(new, sort_keys=True)
    validate_transition(caller_bytes, candidate, state_bytes,
                        home=str(home), objective=str(objective))
    manifest = {
        'schema': 'cost934.adoption.plan.v1',
        'observer_merge': OBSERVER_MERGE,
        'observer_sha256': OBSERVER_SHA256,
        'objective': {'path': str(objective), 'sha256': objective_sha256},
        'preimages': preimages,
        'postimage_sha256': {'code': digest(code_bytes), 'caller': digest(candidate)},
        'preserve_state': {'path': str(home / '.merglbot/gh-cost-wave3/acceptance_observer_state.json'),
                           'sha256': digest(state_bytes), 'restore_permitted': False},
        'service_label': 'ai.merglbot.gh-cost-wave3-acceptance',
        'owner_authorized': False,
        'runtime_verified': False,
    }
    return manifest, digest(canonical(manifest)), candidate
