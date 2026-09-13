"""Exercise bounded switch activation, restart and fail-safe cleanup."""
import copy
import datetime as dt
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'scripts/ci-pilot'))
import repo_window as window
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


    def test_storage_failure_still_cleans_partial_activation(self):
        calls = []
        def persist(state):
            calls.append(1)
            if len(calls) >= 3:
                raise OSError('synthetic disk full')
        result = window.tick(self.gh, self.state, self.now, True, {'start_window': True},
                             persist=persist, clock=lambda: self.now, supervisor_ready=lambda: True)
        self.assertEqual('unverified', result['status'])
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse(any(self.gh.values.values()))

    def test_expiry_during_observation_cleans_before_return(self):
        self.start()
        def observation(gh, value, persist, checkpoint):
            self.now += dt.timedelta(hours=5)
            checkpoint()
        with patch.object(window, 'observe', side_effect=observation):
            result = self.tick()
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse(any(self.gh.values.values()))

    def test_hold_during_observation_cleans_before_return(self):
        self.start()
        held = [False]
        def observation(gh, value, persist, checkpoint):
            held[0] = True
            checkpoint()
        with patch.object(window, 'observe', side_effect=observation):
            result = window.tick(self.gh, self.state, self.now, True,
                                 clock=lambda: self.now, hold_check=lambda: held[0])
        self.assertTrue(result['cleanup_verified'])
        self.assertFalse(any(self.gh.values.values()))

    def test_only_first_attempt_within_actual_boundaries_is_observed(self):
        value = {'activation_intents': {window.REPOS[0]: self.now.isoformat()},
                 'stopped_at': {window.REPOS[0]: (self.now + dt.timedelta(hours=5)).isoformat()}}
        self.gh.pages = lambda path, *args: ([{'id': 1, 'created_at': self.now.isoformat(),
            'run_attempt': 2}] if '/runs?' in path else [])
        calls = []
        def api(path):
            calls.append(path)
            return {'id': 1, 'run_started_at': self.now.isoformat(), 'status': 'completed',
                    'pull_requests': []}
        self.gh.api = api
        result = window.observe(self.gh, value, lambda: None)
        self.assertEqual({'unfinished_runs': 0, 'data_gaps': 0}, result)
        self.assertEqual([f'repos/{window.REPOS[0]}/actions/runs/1/attempts/1'], calls)
        for offset in (-1, 5 * 3600):
            value.pop('observations', None)
            self.gh.api = lambda path: {'run_started_at': (self.now + dt.timedelta(seconds=offset)).isoformat()}
            self.assertEqual(0, window.observe(self.gh, value, lambda: None)['data_gaps'])
            self.assertFalse(value.get('observations'))


class PreflightTests(unittest.TestCase):
    def setUp(self):
        import base64
        import json
        self.repo = window.REPOS[0]
        self.gh = FakeGitHub()
        protection = {'required_status_checks': {'strict': True, 'checks': [
            {'context': 'Merglbot PR Assistant v6', 'app_id': 3518182},
            {'context': 'Unit tests', 'app_id': 15368}]}}
        self.contract = {'workflow_sha256': window.digest('workflow'),
                         'protection_sha256': window.digest(json.dumps(
                             {'protection': protection, 'rules': []}, sort_keys=True))}
        self.seconds = 10
        self.secrets = []
        def api(path):
            if '/git/ref/' in path:
                return {'object': {'sha': 'a' * 40}}
            if '/contents/' in path:
                return {'content': base64.b64encode(b'workflow').decode()}
            if path.endswith('/protection'):
                return protection
            if path.endswith('/deployment_protection_rules'):
                return {'total_count': 0}
            return {'protection_rules': ([{'type': 'wait_timer', 'wait_timer': self.seconds}]
                        if path.endswith('/ci-pr-delay') else []),
                    'deployment_branch_policy': {'protected_branches': False,
                                                 'custom_branch_policies': True}}
        def pages(path, key=None, size=100):
            if path.endswith('/secrets'):
                return self.secrets
            if path.endswith('/deployment-branch-policies'):
                return [{'name': 'refs/pull/*/merge', 'type': 'branch'}]
            return []
        self.gh.api, self.gh.pages = api, pages

    def test_live_contract_and_unsafe_environment_changes(self):
        window.preflight(self.gh, self.repo, self.contract)
        self.seconds = 15
        with self.assertRaisesRegex(Gap, 'timer_changed'):
            window.preflight(self.gh, self.repo, self.contract)
        self.seconds = 10
        self.secrets = [{'name': 'synthetic-secret-name'}]
        with self.assertRaisesRegex(Gap, 'environment_not_empty'):
            window.preflight(self.gh, self.repo, self.contract)

    def test_drift_cannot_be_approved_by_prior_hash(self):
        self.contract['workflow_sha256'] = 'b' * 64
        with self.assertRaisesRegex(Gap, 'contract_changed'):
            window.preflight(self.gh, self.repo, self.contract)


if __name__ == '__main__':
    unittest.main()
