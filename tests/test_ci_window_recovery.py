"""Recovery preserves prior CI experiment evidence before window activation."""
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


class WindowRecoveryTests(unittest.TestCase):
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

