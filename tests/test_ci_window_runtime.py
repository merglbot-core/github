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
        state = {'repo_window': {'phase': 'prepared'}, 'experiment': {'active': None},
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


if __name__ == '__main__':
    unittest.main()
