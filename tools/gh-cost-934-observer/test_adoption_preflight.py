import importlib.util
import copy
from pathlib import Path
import plistlib
import unittest
from unittest.mock import patch


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result

m=module('adoption_preflight'); plan=module('adoption_plan'); contract=module('adoption_contract')


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.home=Path('/synthetic-owner');base=self.home/'.merglbot/gh-cost-wave3'
        self.code=base/'acceptance_observer.py';self.state=base/'acceptance_observer_state.json'
        self.caller=self.home/'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist'
        self.objective=self.home/'private/objective.txt';self.source=b'synthetic source'
        self.data={self.code:b'old',self.state:b'{"stages":{},"halted":"STOP"}',self.objective:b'private fixture',
            self.caller:plistlib.dumps({'Label':contract.LABEL,'ProgramArguments':['/opt/homebrew/bin/python3',str(self.code)]})}
        self.metadata={p:{'sha256':m.digest(self.data[p])} for p in (self.code,self.caller)}
        self.patch=patch.object(plan,'OBSERVER_SHA256',m.digest(self.source));self.patch.start();self.addCleanup(self.patch.stop)
        self.manifest,self.binding,_=plan.prepare(home=str(self.home),objective=str(self.objective),
            objective_sha256=m.digest(self.data[self.objective]),code_bytes=self.source,
            caller_bytes=self.data[self.caller],state_bytes=self.data[self.state],
            preimages={k:{'path':str(p),'snapshot':self.metadata[p]} for k,p in [('code',self.code),('caller',self.caller)]},
            validate_transition=contract.validate_transition)
        self.hold=False

    def verify(self):
        return m.verify(self.manifest,self.binding,home=self.home,source_bytes=self.source,
            read_bytes=lambda p:self.data[p],native_snapshot=lambda p:self.metadata[p],
            prepare_plan=plan.prepare,validate_transition=contract.validate_transition,
            owner_hold_exists=lambda:self.hold)

    def test_complete_readonly_preflight_returns_only_exact_pair(self):
        before=copy.deepcopy(self.data);rows,state=self.verify()
        self.assertEqual({r['target'] for r in rows},{str(self.code),str(self.caller)})
        self.assertEqual(self.data,before);self.assertEqual(state,before[self.state])

    def test_owner_hold_and_changed_manifest_refuse(self):
        self.hold=True
        with self.assertRaisesRegex(ValueError,'owner_hold'):self.verify()
        self.hold=False;self.manifest['owner_authorized']=True
        with self.assertRaisesRegex(ValueError,'binding_mismatch'):self.verify()

    def test_new_state_or_objective_requires_new_plan(self):
        for path in (self.state,self.objective):
            with self.subTest(path=path):
                original=self.data[path];self.data[path]=b'changed'
                with self.assertRaises(ValueError):self.verify()
                self.data[path]=original

    def test_permission_error_never_means_absent(self):
        def denied():raise PermissionError('no access')
        with self.assertRaises(PermissionError):
            m.verify(self.manifest,self.binding,home=self.home,source_bytes=self.source,
                read_bytes=lambda p:self.data[p],native_snapshot=lambda p:self.metadata[p],
                prepare_plan=plan.prepare,validate_transition=contract.validate_transition,
                owner_hold_exists=denied)
