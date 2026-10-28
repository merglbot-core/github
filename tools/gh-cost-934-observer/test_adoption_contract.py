import copy
import json
import plistlib
import unittest
from unittest.mock import patch

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'financial934_adoption_contract', Path(__file__).with_name('adoption_contract.py'))
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class AdoptionContractTests(unittest.TestCase):
    def setUp(self):
        self.home = '/synthetic-owner'
        self.objective = '/synthetic-owner/private/objective.txt'
        code = self.home + '/.merglbot/gh-cost-wave3/acceptance_observer.py'
        self.old = {'Label': contract.LABEL,
                    'ProgramArguments': ['/opt/homebrew/bin/python3', code],
                    'RunAtLoad': True, 'KeepAlive': False,
                    'EnvironmentVariables': {'CODEX_HOME': '/synthetic-profile'},
                    'StartCalendarInterval': {'Hour': 2, 'Minute': 10}}
        self.new = copy.deepcopy(self.old)
        self.new['ProgramArguments'] = [self.home + '/.merglbot/mem-guard/capped-run',
            '--max-gb', '2', '--timeout', '600', '--', '/opt/homebrew/bin/python3',
            code, '--objective', self.objective]
        self.state = {'stages': {'post_window': {'attempts': 2, 'status': 'done'}},
                      'halted': 'EXISTING_STOP', 'needs_owner_review': True}

    def check(self):
        return contract.validate_transition(plistlib.dumps(self.old), plistlib.dumps(self.new),
            json.dumps(self.state).encode(), home=self.home, objective=self.objective)

    def test_exact_transition_preserves_stop_and_consumed_attempts_without_authority(self):
        snapshot = copy.deepcopy(self.state)
        result = self.check()
        self.assertEqual(self.state, snapshot)
        self.assertFalse(result['state_write_permitted'])
        self.assertFalse(result['installation_authorized'])
        self.assertFalse(result['launch_authorized'])

    def test_unrelated_caller_changes_are_rejected(self):
        for key, value in [('RunAtLoad', False), ('KeepAlive', True),
                           ('StartCalendarInterval', {'Hour': 3}),
                           ('EnvironmentVariables', {}), ('NewField', True)]:
            with self.subTest(key=key):
                original = copy.deepcopy(self.new)
                self.new[key] = value
                with self.assertRaisesRegex(ValueError, 'caller_invariant_drift'):
                    self.check()
                self.new = original

    def test_timeout_objective_and_guard_cannot_be_substituted(self):
        for index in (0, 2, 4, 9):
            with self.subTest(index=index):
                old = self.new['ProgramArguments'][index]
                self.new['ProgramArguments'][index] = 'foreign'
                with self.assertRaises(ValueError):
                    self.check()
                self.new['ProgramArguments'][index] = old

    def test_running_or_malformed_state_is_not_repaired_by_adoption(self):
        for state in ({'stages': {'x': {'status': 'running'}}},
                      {'stages': []}, {'stages': {'x': None}}, []):
            with self.subTest(state=state):
                self.state = state
                with self.assertRaises(ValueError):
                    self.check()

    def test_foreign_service_or_previous_command_refused(self):
        self.old['Label'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'foreign_service'):
            self.check()
        self.old['Label'] = contract.LABEL
        self.old['ProgramArguments'].append('--force')
        with self.assertRaisesRegex(ValueError, 'unexpected_previous_caller'):
            self.check()

    def test_current_required_objective_program_cannot_be_previous_no_argument_caller(self):
        source = Path(__file__).with_name('acceptance_observer.py').read_bytes()
        with self.assertRaisesRegex(ValueError, 'previous_program_cli_and_contention_contract_unverified'):
            contract.validate_previous_program(source, self.old['ProgramArguments'], home=self.home)

    def test_previous_contract_requires_both_pinned_bytes_and_exact_argv(self):
        fixture = b'synthetic reviewed legacy program'
        with patch.object(contract, 'PREVIOUS_NO_OBJECTIVE_SHA256', contract.hashlib.sha256(fixture).hexdigest()):
            contract.validate_previous_program(fixture, self.old['ProgramArguments'], home=self.home)
            with self.assertRaisesRegex(ValueError, 'previous_program_arguments_unverified'):
                contract.validate_previous_program(fixture, self.old['ProgramArguments'] + ['--objective', self.objective], home=self.home)
            with self.assertRaisesRegex(ValueError, 'previous_program_cli_and_contention_contract_unverified'):
                contract.validate_previous_program(fixture + b'changed', self.old['ProgramArguments'], home=self.home)


if __name__ == '__main__':
    unittest.main()
