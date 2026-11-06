"""Read-only manifest and input binding before any #934 service mutation."""
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def verify(manifest, expected_binding, *, home, source_bytes, read_bytes,
           native_snapshot, prepare_plan, validate_transition, owner_hold_exists):
    """The owner runner supplies the admitted digest, never from the same JSON.

No permission failure is treated as missing. Input reads are bounded by the
owner entrypoint; objective contents are hashed locally and never returned.
"""
    if digest(canonical(manifest)) != expected_binding:
        raise ValueError('owner_manifest_binding_mismatch')
    if manifest.get('schema') != 'cost934.adoption.plan.v1':
        raise ValueError('unsupported_manifest')
    if owner_hold_exists():
        raise ValueError('owner_hold')
    base = Path(home) / '.merglbot/gh-cost-wave3'
    code = base / 'acceptance_observer.py'
    caller = Path(home) / 'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'
    state = base / 'acceptance_observer_state.json'
    paths = {'code': code, 'caller': caller}
    preimages = manifest['preimages']
    if set(preimages) != set(paths):
        raise ValueError('exact_pair_required')
    for key, path in paths.items():
        if preimages[key]['path'] != str(path):
            raise ValueError('foreign_target')
        if native_snapshot(path) != preimages[key]['snapshot']:
            raise ValueError('native_preimage_drift:' + key)
    if manifest['preserve_state']['path'] != str(state):
        raise ValueError('foreign_state')
    state_bytes = read_bytes(state)
    if digest(state_bytes) != manifest['preserve_state']['sha256']:
        raise ValueError('state_pin_changed_prepare_fresh_plan')
    objective = manifest['objective']
    if digest(read_bytes(Path(objective['path']))) != objective['sha256']:
        raise ValueError('objective_changed')
    rebuilt, rebuilt_binding, candidate = prepare_plan(home=str(home),
        objective=objective['path'], objective_sha256=objective['sha256'],
        code_bytes=source_bytes, caller_bytes=read_bytes(caller), state_bytes=state_bytes,
        preimages=preimages, validate_transition=validate_transition)
    if rebuilt != manifest or rebuilt_binding != expected_binding:
        raise ValueError('manifest_semantics_mismatch')
    rows = []
    for key, data in (('code', source_bytes), ('caller', candidate)):
        rows.append({'target': str(paths[key]), 'previous': preimages[key]['snapshot'],
                     'source_bytes': data, 'source_sha256': digest(data)})
    return rows, state_bytes
