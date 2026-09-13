"""Exercise bounded switch activation, restart and fail-safe cleanup."""
import copy
import datetime as dt
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts/ci-pilot'))
import repo_window as window
import runtime
from github_client import Gap


class FakeGitHub:
    def __init__(self):
        self.values = {repo: {} for repo in window.REPOS}
        self.writes = []
        self.fail_enable = None
        self.fail_delete = False

    def selectors(self, repo):
        return {}

    def pages(self, path, key=None, size=100):
        if '/actions/variables' in path:
            repo = path.split('/actions/')[0][6:]
            return [{'name': k, 'value': v} for k, v in self.values[repo].items()]
        return []

    def command(self, args, **kwargs):
        enabled = args[4] == 'set'
        repo = args[args.index('--repo') + 1]
        self.writes.append((repo, enabled))
        if enabled and repo == self.fail_enable or not enabled and self.fail_delete:
            raise Gap('simulated_api_failure')
        self.values[repo] = {window.SWITCH: 'true'} if enabled else {}


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 13, 12, tzinfo=dt.timezone.utc)
        self.gh = FakeGitHub()
        self.state = {'counted_prs': ['historical/one#1'], 'history': [],
                      'repo_window': {'phase': 'prepared', 'contracts': {r: {} for r in window.REPOS},
                                      'disabled': [], 'activation_intents': {}}}
        self.preflight = patch.object(window, 'preflight')
        self.preflight.start()
        self.addCleanup(self.preflight.stop)

    def tick(self, receipt=None, ready=True, apply=True):
        return window.tick(self.gh, self.state, self.now, apply, receipt,
                           clock=lambda: self.now, supervisor_ready=lambda: ready)

    def start(self):
        return self.tick({'start_window': True})

    def test_shared_deadline_and_restart_preserve_history(self):
        self.assertEqual('active', self.start()['status'])
        deadline = self.state['repo_window']['expires_at']
        self.now += dt.timedelta(hours=2)
        self.assertEqual('active', self.tick()['status'])
        self.assertEqual(deadline, self.state['repo_window']['expires_at'])
        self.assertEqual(['historical/one#1'], self.state['counted_prs'])
        self.assertEqual(2, len(self.gh.writes))

    def test_second_activation_failure_removes_both_switches(self):
        self.gh.fail_enable = window.REPOS[1]
        result = self.start()
        self.assertEqual('unverified', result['status'])
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse(any(self.gh.values.values()))

    def test_expiry_removes_before_observation(self):
        self.start()
        self.now += dt.timedelta(hours=5)
        def observation(*args):
            self.assertFalse(any(self.gh.values.values()))
            return {'unfinished_runs': 0, 'data_gaps': 0}
        with patch.object(window, 'observe', side_effect=observation):
            self.assertEqual('repo_window_complete', self.tick()['action'])

    def test_failed_removal_retries_without_reenabling(self):
        self.start()
        self.gh.fail_delete = True
        self.now += dt.timedelta(hours=5)
        self.assertFalse(self.tick()['cleanup_verified'])
        self.gh.fail_delete = False
        self.assertEqual('repo_window_complete', self.tick()['action'])
        self.assertEqual(2, sum(enabled for _, enabled in self.gh.writes))

    def test_incomplete_restart_never_continues_activation(self):
        self.start()
        self.state['repo_window']['phase'] = 'activating'
        self.assertEqual('window_incomplete_activation', self.tick()['reason'])
        self.assertFalse(any(self.gh.values.values()))

    def test_per_repo_stop_does_not_stop_other_repo(self):
        self.start()
        self.tick({'stop_repos': [window.REPOS[0]]})
        self.assertFalse(self.gh.values[window.REPOS[0]])
        self.assertTrue(self.gh.values[window.REPOS[1]])
        self.assertEqual('active', self.tick()['status'])

    def test_no_activation_without_live_supervisor(self):
        self.assertEqual('window_start_not_ready', self.tick({'start_window': True}, ready=False)['reason'])
        self.assertFalse(self.gh.writes)

    def test_invalid_repo_cannot_mutate(self):
        with self.assertRaises(Gap):
            window.write_switch(self.gh, 'someone/else', True)
        self.assertFalse(self.gh.writes)

    def test_readonly_does_not_activate(self):
        self.assertEqual('readonly', self.tick({'start_window': True}, apply=False)['status'])
        self.assertFalse(self.gh.writes)

    def test_runtime_drains_before_stopping(self):
        result = {'action': 'observe_window', 'status': 'active',
                  'history': {'unfinished_runs': 1, 'data_gaps': 0}}
        self.assertFalse(runtime.schedule(result, self.now)['stopped'])
        result.update(action='repo_window_complete', status='inactive',
                      history={'unfinished_runs': 0, 'data_gaps': 0})
        self.assertTrue(runtime.schedule(result, self.now)['stopped'])


if __name__ == '__main__':
    unittest.main()
