import importlib.util
import hashlib
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj

m = module('adoption_plan')
contract = module('adoption_contract')


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.home = '/synthetic-owner'
        self.code_path = self.home + '/.merglbot/gh-cost-wave3/acceptance_observer.py'
        self.caller_path = self.home + '/Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'
        self.caller = plistlib.dumps({'Label': contract.LABEL, 'RunAtLoad': True,
            'ProgramArguments': ['/opt/homebrew/bin/python3', self.code_path],
            'StartCalendarInterval': {'Hour': 2, 'Minute': 10}})
        self.args = dict(home=self.home, objective=self.home + '/private/objective.txt',
            objective_sha256='a' * 64, code_bytes=b'synthetic reviewed source',
            caller_bytes=self.caller, state_bytes=b'{"stages":{},"halted":"STOP"}',
            preimages={'code': {'path': self.code_path, 'snapshot': {'sha256': 'b' * 64}},
                       'caller': {'path': self.caller_path,
                                  'snapshot': {'sha256': m.digest(self.caller)}}},
            validate_transition=contract.validate_transition)

    def prepare(self):
        with patch.object(m, 'OBSERVER_SHA256', m.digest(self.args['code_bytes'])):
            return m.prepare(**self.args)

    def test_manifest_binds_both_outputs_and_never_claims_authority(self):
        plan, binding, candidate = self.prepare()
        self.assertEqual(binding, m.digest(m.canonical(plan)))
        self.assertFalse(plan['owner_authorized'])
        self.assertFalse(plan['runtime_verified'])
        self.assertFalse(plan['preserve_state']['restore_permitted'])
        self.assertEqual(plan['postimage_sha256']['caller'], m.digest(candidate))
        self.assertEqual(plistlib.loads(candidate)['StartCalendarInterval'], {'Hour': 2, 'Minute': 10})

    def test_wrong_source_pin_refused(self):
        with self.assertRaisesRegex(ValueError, 'observer_source_pin_mismatch'):
            m.prepare(**self.args)

    def test_foreign_target_and_mismatched_caller_refused(self):
        self.args['preimages']['caller']['path'] = '/foreign'
        with self.assertRaisesRegex(ValueError, 'foreign_target'):
            self.prepare()
        self.args['preimages']['caller']['path'] = self.caller_path
        self.args['preimages']['caller']['snapshot']['sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'caller_preimage_mismatch'):
            self.prepare()

    def test_pending_stage_refused_without_reset(self):
        self.args['state_bytes'] = b'{"stages":{"closed_month":{"status":"running","attempts":1}}}'
        with self.assertRaisesRegex(ValueError, 'unfinalized_stage'):
            self.prepare()
