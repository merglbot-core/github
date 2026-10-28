"""Pure preflight contract for separately authorized #934 adoption.

This module performs no filesystem writes, launchd calls or model runs. Passing
it is necessary, never sufficient, for source admission or owner installation.
"""
import json
import plistlib
from pathlib import PurePosixPath

LABEL = 'ai.merglbot.gh-cost-wave3-acceptance'


def validate_transition(before, after, state_bytes, *, home, objective):
    """Permit only the bounded caller transition, preserving all other fields."""
    home = PurePosixPath(home)
    objective = PurePosixPath(objective)
    if not home.is_absolute() or not objective.is_absolute():
        raise ValueError('absolute_paths_required')
    if '..' in home.parts or '..' in objective.parts:
        raise ValueError('noncanonical_path')
    old = plistlib.loads(before)
    new = plistlib.loads(after)
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError('plist_mapping_required')
    code = str(home / '.merglbot/gh-cost-wave3/acceptance_observer.py')
    if old.get('Label') != LABEL:
        raise ValueError('foreign_service')
    if old.get('ProgramArguments') != ['/opt/homebrew/bin/python3', code]:
        raise ValueError('unexpected_previous_caller')
    expected = dict(old)
    expected['ProgramArguments'] = [
        str(home / '.merglbot/mem-guard/capped-run'), '--max-gb', '2',
        '--timeout', '600', '--', '/opt/homebrew/bin/python3', code,
        '--objective', str(objective),
    ]
    if new != expected:
        raise ValueError('caller_invariant_drift')
    state = json.loads(state_bytes)
    if not isinstance(state, dict) or not isinstance(state.get('stages'), dict):
        raise ValueError('state_schema_invalid')
    # No reset/recovery is implicit in code adoption. A running journal entry
    # can be interrupted work even when the kernel lock is currently free.
    if any(not isinstance(stage, dict) for stage in state['stages'].values()):
        raise ValueError('stage_schema_invalid')
    if any(stage.get('status') == 'running' for stage in state['stages'].values()):
        raise ValueError('unfinalized_stage')
    return {'caller_transition_verified': True, 'state_write_permitted': False,
            'installation_authorized': False, 'launch_authorized': False}
