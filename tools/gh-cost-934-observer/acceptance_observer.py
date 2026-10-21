#!/usr/bin/env python3
"""Bounded local wake-up for wave-3 net acceptance issue github#934."""

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from time import monotonic
from zoneinfo import ZoneInfo


PRAGUE = ZoneInfo('Europe/Prague')
BASE = Path.home()/'.merglbot/gh-cost-wave3'
STATE = BASE/'acceptance_observer_state.json'
LOCK = BASE/'acceptance_observer.lock'
AUDIT = BASE/'evidence/completion_audit_20260925.json'
HANDOFF = BASE/'HANDOFF.md'
# The admitted private objective path is supplied by the existing caller.
OBJECTIVE = BASE/'owner-objective.txt'
WORKSPACE = Path.home()/'Documents/work/merglbot.ai'
CODEX = Path('/opt/homebrew/bin/codex')
CAPPED = Path.home()/'.merglbot/mem-guard/capped-run'
STAGES = (
    ('post_window', dt.datetime(2026, 10, 14, 2, 10, tzinfo=PRAGUE)),
    ('closed_month', dt.datetime(2026, 11, 2, 2, 10, tzinfo=PRAGUE)),
    ('final_settlement', dt.datetime(2026, 12, 2, 2, 10, tzinfo=PRAGUE)),
)
MAX_ATTEMPTS_PER_STAGE = 2
RETRY_AFTER = dt.timedelta(hours=24)
CAP_SECONDS = 600
FINALIZATION_RESERVE_SECONDS = 10


def now_prague():
    return dt.datetime.now(PRAGUE)


def read_json(path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text())


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.acceptance-state-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def running_stages(state):
    return sorted(name for name, record in state.get('stages', {}).items()
                  if isinstance(record, dict) and record.get('status') == 'running')


def child_seconds(deadline):
    return max(0, min(CAP_SECONDS, int(deadline - monotonic())
                      - FINALIZATION_RESERVE_SECONDS))


def due_stage(now, state):
    if state.get('halted') or state.get('complete') or running_stages(state):
        return None
    for name, due in STAGES:
        if now < due:
            return None
        record = state.get('stages', {}).get(name, {})
        if record.get('status') == 'done' or record.get('attempts', 0) >= MAX_ATTEMPTS_PER_STAGE:
            continue
        previous = record.get('last_attempt_at_prague')
        if previous and now - dt.datetime.fromisoformat(previous) < RETRY_AFTER:
            return None
        return name
    return None


def exhausted_without_completion(now, state):
    """Make a finite observer's unresolved terminal state explicit."""
    return (not state.get('complete') and not state.get('halted')
            and now >= STAGES[-1][1]
            and all((record := state.get('stages', {}).get(name, {})).get('status') == 'done'
                    or record.get('attempts', 0) >= MAX_ATTEMPTS_PER_STAGE
                    for name, _ in STAGES))


def refresh_completion_from_audit(state):
    """Observe proof written by the child before finalizing its stage."""
    if AUDIT.exists():
        proof = read_json(AUDIT, {}).get('financial_acceptance', {})
        if (isinstance(proof, dict) and proof.get('status') == 'PROVEN'
                and isinstance(proof.get('evidence'), list) and proof['evidence']):
            state['complete'] = True


def prompt_for(stage):
    return (f'Resume the owner-authorized GitHub savings program, focusing on the single '
            f'cross-program financial acceptance issue merglbot-core/github#934. '
            f'This is scheduled stage {stage}. Read the complete owner objective at {OBJECTIVE} '
            f'and the current handoff at {HANDOFF} before acting. Revalidate current GitHub '
            f'estate, latest relevant configuration merge, API rate headers and settled billing. '
            f'At post_window, collect the full 14-day run window only if it has closed; use '
            f'checkpointed tools and leave unsettled finance as DATA_GAP. At closed_month or '
            f'final_settlement, reconcile fresh numerically validated September/October billing '
            f'with the closed billed report or invoice. Do not duplicate #888/#910 autopilots, '
            f'force CI, alter guardrails/fleet, or claim a saving from an open-month snapshot. '
            f'Update #934, the Czech HTML/PDF, HANDOFF and completion audit with evidence. '
            f'If data remain unavailable, record the exact blocker and leave #934 open. '
            f'Keep technical goal_completion separate from financial acceptance. Write '
            f'financial_acceptance as status PROVEN with a nonempty evidence list only '
            f'after live settled net acceptance is proved; otherwise status NOT_PROVEN. '
            f'Do not create another goal or observer. All owner-facing times Europe/Prague.')


def main(argv=None):
    global OBJECTIVE
    deadline = monotonic() + CAP_SECONDS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--objective', type=Path, required=True,
                        help='Existing admitted local objective file; never its contents')
    args = parser.parse_args(argv)
    if not args.objective.is_absolute():
        parser.error('--objective must be an absolute path')
    OBJECTIVE = args.objective
    now = now_prague()
    state = read_json(STATE, {'schema_version': 1, 'stages': {}})
    # A dry read cannot distinguish an active stage from an interrupted one.
    # Neither may cause a new child or an inferred successful completion.
    pending = running_stages(state)
    if not pending:
        refresh_completion_from_audit(state)
    stage = due_stage(now, state)
    if args.dry_run:
        print(json.dumps({'now_prague': now.isoformat(timespec='seconds'),
                          'due_stage': stage, 'stages': state.get('stages', {}),
                          'complete': state.get('complete', False),
                          'will_launch_codex': bool(stage),
                          'unfinalized_or_active_stages': pending,
                          'source_deadline_seconds': CAP_SECONDS}))
        return 0
    BASE.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state = read_json(STATE, {'schema_version': 1, 'stages': {}})
        # Only the lock holder may classify a persisted running stage as
        # unfinalized. Never repeat it after an outer guard kill or crash.
        if running_stages(state):
            if not state.get('halted'):
                state['halted'] = 'PRIOR_STAGE_UNFINALIZED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 2
        refresh_completion_from_audit(state)
        stage = due_stage(now, state)
        state['last_wake_prague'] = now.isoformat(timespec='seconds')
        if not stage:
            if exhausted_without_completion(now, state):
                state['halted'] = 'BOUNDED_FOLLOWUP_EXHAUSTED_UNRESOLVED'
                state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 0
        if not all(path.exists() for path in (OBJECTIVE, HANDOFF, CODEX, CAPPED)):
            state['halted'] = 'MISSING_REQUIRED_LOCAL_INPUT'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 2
        if child_seconds(deadline) < 1:
            state['halted'] = 'OBSERVER_TIME_BUDGET_EXHAUSTED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 2
        record = state.setdefault('stages', {}).setdefault(stage, {})
        record['attempts'] = record.get('attempts', 0) + 1
        record['last_attempt_at_prague'] = now.isoformat(timespec='seconds')
        record['status'] = 'running'
        atomic_json(STATE, state)
        child_limit = child_seconds(deadline)
        if child_limit < 1:
            record['status'] = 'not_started_time_budget_exhausted'
            state['halted'] = 'OBSERVER_TIME_BUDGET_EXHAUSTED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 3
        command = [str(CAPPED), '--max-gb', '2', '--timeout', str(child_limit), '--',
                   str(CODEX), 'exec', '-C', str(WORKSPACE), '--skip-git-repo-check', '-']
        env = os.environ.copy()
        env['CODEX_HOME'] = str(Path.home()/'.codex')
        env['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        # Count prompt/environment preparation against the same total budget.
        prompt = prompt_for(stage)
        launch_limit = child_seconds(deadline)
        if launch_limit < 1:
            record['status'] = 'not_started_time_budget_exhausted'
            state['halted'] = 'OBSERVER_TIME_BUDGET_EXHAUSTED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 3
        command[4] = str(min(child_limit, launch_limit))
        try:
            result = subprocess.run(command, input=prompt, text=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    cwd=WORKSPACE, env=env, timeout=launch_limit + 1)
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 86
        except OSError:
            record['status'] = 'child_start_failed_no_auto_retry'
            state['halted'] = 'CHILD_START_FAILED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
            atomic_json(STATE, state)
            return 3
        record['exit_code'] = code
        record['finished_at_prague'] = now_prague().isoformat(timespec='seconds')
        if code in (86, 137, -9):
            record['status'] = 'stopped_by_memory_or_time_guard'
            state['halted'] = 'CAPPED_RUN_KILLED_NO_AUTO_RETRY'
            state['needs_owner_review'] = True
        elif code == 0:
            record['status'] = 'done'
        else:
            record['status'] = 'failed_retry_bounded'
        refresh_completion_from_audit(state)
        if exhausted_without_completion(now_prague(), state):
            state['halted'] = 'BOUNDED_FOLLOWUP_EXHAUSTED_UNRESOLVED'
            state['needs_owner_review'] = True
        atomic_json(STATE, state)
        return 0 if code == 0 else 3


if __name__ == '__main__':
    sys.exit(main())
