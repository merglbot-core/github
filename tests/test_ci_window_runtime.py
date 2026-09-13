"""Window dispatch and scheduled shutdown use the existing supervisor."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/ci-pilot'))
import controller
import repo_window
import runtime
import experiment_measurements
from github_client import Gap
from test_ci_repo_window import FakeGitHub


class WindowRuntimeTests(unittest.TestCase):
    def test_prepared_window_is_dispatched_before_retired_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = {'repo_window': {'phase': 'prepared'}, 'experiment': {'active': None}}
            (root / 'state.json').write_text(json.dumps(state))
            result = {'action': 'window_prepared', 'status': 'inactive'}
            with patch.object(sys, 'argv', ['controller', 'tick', '--state-dir', directory]), patch.object(
                    repo_window, 'tick', return_value=result) as tick, patch('builtins.print'):
                self.assertEqual(0, controller.main())
            self.assertEqual(state, tick.call_args.args[1])

    def test_schedule_only_stops_after_verified_drain(self):
        now = dt.datetime.now(dt.timezone.utc)
        result = {'action': 'observe_window', 'status': 'active',
                  'history': {'unfinished_runs': 1, 'data_gaps': 0}}
        self.assertFalse(runtime.schedule(result, now)['stopped'])
        result.update(action='repo_window_complete', status='inactive',
                      history={'unfinished_runs': 0, 'data_gaps': 0})
        self.assertTrue(runtime.schedule(result, now)['stopped'])

    def test_runtime_emergency_cleanup_includes_new_switch(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
                runtime, 'cleanup', return_value=True), patch.object(repo_window, 'disable', return_value=False) as disable:
            self.assertFalse(runtime.locked_cleanup(Path(directory)))
            self.assertTrue(disable.call_args.kwargs['apply'])

    def test_invalid_window_deadline_invokes_cleanup(self):
        for expiry in ('broken', 123, '2026-09-13T12:00:00', None):
            with self.subTest(expiry=expiry), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'state.json').write_text(json.dumps({'repo_window': {
                    'phase': 'active', 'expires_at': expiry}}))
                result = {'action': 'cleanup_verified', 'status': 'inactive'}
                with patch.object(runtime, 'emergency_cleanup', return_value=result) as cleanup, patch.object(
                        runtime, 'sync_heartbeat', return_value=True):
                    runtime.wake(root, dt.datetime.now(dt.timezone.utc))
                cleanup.assert_called_once()
                saved = json.loads((root / 'next_action.json').read_text())
                self.assertEqual('recovery_required', saved['action'])
                self.assertEqual('unverified', saved['status'])

    def test_prepared_window_rearms_stopped_runtime_without_erasing_history(self):
        state = {'repo_window': {'phase': 'prepared'}, 'experiment': {'version': 1, 'active': None, 'cases': []},
                 'counted_prs': ['historical/repo#1'], 'history': [{'retained': True}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = json.dumps(state)
            (root / 'state.json').write_text(original)
            (root / 'runtime.json').write_text(json.dumps({'stopped': True, 'healthy': True}))
            with patch.object(runtime, 'supervisor_unloaded', return_value=True), patch.object(
                    repo_window, 'disable', return_value=True), patch.object(runtime, 'cleanup', return_value=True), patch.object(
                    controller, 'history_evidence', return_value={'unfinished_runs': 0, 'data_gaps': 0}):
                self.assertEqual(0, runtime.recover_window(root, dt.datetime.now(dt.timezone.utc)))
            self.assertEqual(original, (root / 'state.json').read_text())
            plan = json.loads((root / 'runtime.json').read_text())
            self.assertFalse(plan['stopped'])
            self.assertFalse(plan['healthy'])
            self.assertNotIn('last_successful_wake', plan)

    def test_window_cleanup_precedes_stalled_legacy_cleanup(self):
        calls = []
        def remove(gh, apply):
            self.assertEqual(20, gh.command_timeout)
            calls.append('window')
            return True
        def legacy(*args):
            self.assertEqual(['window'], calls)
            raise TimeoutError('simulated legacy stall')
        with tempfile.TemporaryDirectory() as directory, patch.object(
                repo_window, 'disable', side_effect=remove), patch.object(runtime, 'cleanup', side_effect=legacy):
            with self.assertRaises(TimeoutError):
                runtime.locked_cleanup(Path(directory))
        self.assertEqual(['window'], calls)

    def test_recovery_requires_terminal_experimental_history(self):
        for outcome in ({'unfinished_runs': 1, 'data_gaps': 0},
                        {'unfinished_runs': 0, 'data_gaps': 1},
                        Gap('unreadable history'),
                        {'unfinished_runs': 0, 'data_gaps': 0}):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                case = {'phase': 'inactive', 'pr': 1}
                state = {'repo_window': {'phase': 'prepared'}, 'experiment': {
                    'version': 1, 'active': None, 'cases': [case,
                    {'phase': 'aborted_no_write', 'write_attempted': False}]}}
                original = json.dumps(state)
                (root / 'state.json').write_text(original)
                (root / 'runtime.json').write_text('{"stopped": true}')
                options = ({'side_effect': outcome} if isinstance(outcome, Exception)
                           else {'return_value': outcome})
                with patch.object(runtime, 'supervisor_unloaded', return_value=True), patch.object(
                        repo_window, 'disable', return_value=True), patch.object(runtime, 'cleanup', return_value=True), patch.object(
                        controller, 'history_evidence', return_value={'unfinished_runs': 0, 'data_gaps': 0}), patch.object(
                        experiment_measurements, 'histories', **options) as histories:
                    if isinstance(outcome, Exception) or any(outcome.values()):
                        with self.assertRaises(Gap):
                            runtime.recover_window(root, dt.datetime.now(dt.timezone.utc))
                        self.assertEqual('{"stopped": true}', (root / 'runtime.json').read_text())
                    else:
                        self.assertEqual(0, runtime.recover_window(root, dt.datetime.now(dt.timezone.utc)))
                    self.assertEqual([case], histories.call_args.args[1]['cases'])
                self.assertEqual(original, (root / 'state.json').read_text())

    def test_emergency_stop_preserves_boundaries_for_drain(self):
        for storage_failure in (False, True):
            with self.subTest(storage_failure=storage_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                now = dt.datetime.now(dt.timezone.utc)
                gh = FakeGitHub()
                gh.values = {r: {repo_window.SWITCH: 'true'} for r in repo_window.REPOS}
                state = {'repo_window': {'phase': 'active', 'disabled': [],
                    'started_at': now.isoformat(), 'expires_at': (now + dt.timedelta(hours=5)).isoformat(),
                    'activation_intents': {r: now.isoformat() for r in repo_window.REPOS}}}
                (root / 'state.json').write_text(json.dumps(state))
                original_atomic = runtime.atomic
                def save(*args):
                    if storage_failure:
                        raise OSError('simulated storage failure')
                    return original_atomic(*args)
                with patch.object(runtime, 'GitHub', return_value=gh), patch.object(
                        runtime, 'cleanup', return_value=True), patch.object(runtime, 'atomic', side_effect=save):
                    self.assertEqual(not storage_failure, runtime.locked_cleanup(root))
                self.assertFalse(any(gh.values.values()))
                retained = json.loads((root / 'state.json').read_text())
                with patch.object(repo_window, 'observe', return_value={'unfinished_runs': 0, 'data_gaps': 0}):
                    result = repo_window.tick(gh, retained, dt.datetime.now(dt.timezone.utc), apply=True)
                if storage_failure:
                    self.assertEqual('unverified', result['status'])
                    self.assertTrue(retained['repo_window']['boundary_gap'])
                else:
                    self.assertEqual(set(repo_window.REPOS), set(retained['repo_window']['stopped_at']))
                    self.assertEqual('repo_window_complete', result['action'])

    def test_controller_receipt_error_preserves_stop_boundaries(self):
        for apply in (False, True):
            with self.subTest(apply=apply), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                now = dt.datetime.now(dt.timezone.utc)
                gh = FakeGitHub()
                gh.values = {r: {repo_window.SWITCH: 'true'} for r in repo_window.REPOS}
                state = {'repo_window': {'phase': 'active', 'disabled': [],
                    'started_at': now.isoformat(), 'expires_at': (now + dt.timedelta(hours=5)).isoformat(),
                    'activation_intents': {r: now.isoformat() for r in repo_window.REPOS}}}
                original = json.dumps(state)
                (root / 'state.json').write_text(original)
                args = ['controller', 'prepare-window', '--state-dir', directory] + (['--apply'] if apply else [])
                with patch.object(sys, 'argv', args), patch.object(controller, 'GitHub', return_value=gh), patch('builtins.print'):
                    self.assertEqual(1, controller.main())
                if not apply:
                    self.assertTrue(all(gh.values.values()))
                    self.assertEqual(original, (root / 'state.json').read_text())
                    continue
                self.assertFalse(any(gh.values.values()))
                retained = json.loads((root / 'state.json').read_text())
                self.assertEqual(set(repo_window.REPOS), set(retained['repo_window']['stopped_at']))
                with patch.object(repo_window, 'observe', return_value={'unfinished_runs': 0, 'data_gaps': 0}):
                    result = repo_window.tick(gh, retained, dt.datetime.now(dt.timezone.utc), apply=True)
                self.assertEqual('repo_window_complete', result['action'])


if __name__ == '__main__':
    unittest.main()
