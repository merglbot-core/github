"""Owner CLI integration with temporary files and a simulated launchd/native port.

The separate native temporary-file probe covers real native transactions. This
suite covers glue and journals, and never claims actual launchd activation.
"""
import hashlib
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj


def scenario(objective_drift=False):
    owner=module('adoption_owner');plan=module('adoption_plan');contract=module('adoption_contract')
    with tempfile.TemporaryDirectory() as temp:
        home=Path(temp).resolve();base=home/'.merglbot/gh-cost-wave3';base.mkdir(parents=True)
        (home/'.claude/merglbot-preauth').mkdir(parents=True)
        code=base/'acceptance_observer.py';code.write_bytes(b'original synthetic observer')
        state=base/'acceptance_observer_state.json';state.write_bytes(b'{"stages":{},"halted":"STOP"}')
        (base/'acceptance_observer.lock').write_bytes(b'')
        caller=home/'Library/LaunchAgents/ai.merglbot.gh-cost-wave3-acceptance.plist';caller.parent.mkdir(parents=True)
        caller.write_bytes(plistlib.dumps({'Label':contract.LABEL,'RunAtLoad':True,
            'ProgramArguments':['/opt/homebrew/bin/python3',str(code)]}))
        objective=home/'objective';objective.write_bytes(b'private synthetic objective')
        def snapshot(p):
            p=Path(p);return {'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        def expect(p,facts):
            if snapshot(p)!=facts:raise ValueError('fixture drift')
        def install(rows,backup_parent,invariant_check,journal):
            backup=Path(backup_parent)/'session-context-fixture';backup.mkdir()
            receipt={'state':'prepared','backup_dir':str(backup),'rows':[]}
            for index,row in enumerate(rows):
                invariant_check();expect(row['target'],row['previous'])
                saved=backup/str(index);saved.write_bytes(Path(row['target']).read_bytes())
                receipt['rows'].append({'target':row['target'],'backup':str(saved),
                    'previous':row['previous'],'candidate':{'sha256':row['source_sha256']}})
            journal(receipt)
            for row in rows:
                invariant_check();Path(row['target']).write_bytes(row['source_bytes'])
            receipt['state']='installed_verified';journal(receipt);return receipt
        def rollback_preflight(receipt):
            for row in receipt['rows']:
                expect(row['target'],row['candidate']);expect(row['backup'],row['previous'])
        def rollback(receipt,invariant_check,journal):
            rollback_preflight(receipt)
            for row in receipt['rows']:
                invariant_check();Path(row['target']).write_bytes(Path(row['backup']).read_bytes())
            result={'state':'rollback_verified','rows':receipt['rows']};journal(result);return result
        tx=SimpleNamespace(snapshot=snapshot,install=install,rollback_preflight=rollback_preflight,rollback=rollback)
        source=Path(__file__).with_name('acceptance_observer.py').read_bytes()
        manifest,binding,_=plan.prepare(home=str(home),objective=str(objective),
            objective_sha256=plan.digest(objective.read_bytes()),code_bytes=source,
            caller_bytes=caller.read_bytes(),state_bytes=state.read_bytes(),
            preimages={k:{'path':str(p),'snapshot':snapshot(p)} for k,p in [('code',code),('caller',caller)]},
            validate_transition=contract.validate_transition)
        plan_path=home/'plan.json';plan_path.write_text(json.dumps(manifest))
        original_code=code.read_bytes();original_caller=caller.read_bytes();original_state=state.read_bytes()
        loaded=True;calls=[]
        def run(argv,**kwargs):
            nonlocal loaded
            assert argv[0]=='/bin/launchctl';calls.append(argv[1])
            if argv[1]=='bootout':loaded=False;return SimpleNamespace(returncode=0,stdout='',stderr='')
            if argv[1]=='bootstrap':loaded=True;return SimpleNamespace(returncode=0,stdout='',stderr='')
            assert argv[1]=='print'
            if not loaded:return SimpleNamespace(returncode=113,stdout='',stderr=f'Could not find service "{contract.LABEL}" in domain for user gui: 501\n')
            arguments=plistlib.loads(caller.read_bytes())['ProgramArguments']
            output=f'gui/501/{contract.LABEL} = {{\n\tpath = {caller}\n\tstate = not running\n\tprogram = {arguments[0]}\n\targuments = {{\n'
            output+=''.join('\t\t'+a+'\n' for a in arguments)+'\t}\n\truns = 1\n\tlast exit code = 0\n}\n'
            return SimpleNamespace(returncode=0,stdout=output,stderr='')
        real_helper=owner.helper
        def helper(name):
            if name=='adoption_bundle':return SimpleNamespace(load=lambda root:tx)
            if name=='adoption_lifecycle' and objective_drift:
                result=real_helper(name)
                original_install=result.install
                def install_with_drift(**kwargs):
                    original_lock=kwargs['lock']
                    @contextmanager
                    def lock_with_drift():
                        with original_lock():
                            objective.write_bytes(b'changed after preflight and first guard')
                            yield
                    kwargs['lock']=lock_with_drift
                    return original_install(**kwargs)
                result.install=install_with_drift
                return result
            if name=='adoption_contract':
                # This glue fixture is synthetic, not proof of the real legacy CLI.
                result=real_helper(name)
                result.PREVIOUS_NO_OBJECTIVE_SHA256=hashlib.sha256(original_code).hexdigest()
                return result
            return real_helper(name)
        args=['--plan',str(plan_path),'--binding',binding,'--native-bundle',str(home/'unused')]
        with patch.object(owner,'helper',side_effect=helper),patch.object(owner.Path,'home',return_value=home),patch.object(owner.os,'getuid',return_value=501),patch.object(owner.os,'geteuid',return_value=501),patch.object(owner.subprocess,'run',side_effect=run):
            assert owner.main(args)==0
            assert not (base/'adoptions').exists()
            if objective_drift:
                try:
                    owner.main(args+['--mode','install','POTVRZUJI'])
                except ValueError as exc:
                    assert str(exc)=='objective_pin_changed', str(exc)
                else:
                    raise AssertionError('objective drift was admitted')
                assert code.read_bytes()==original_code and caller.read_bytes()==original_caller
                assert state.read_bytes()==original_state and loaded
                assert 'bootout' not in calls and 'bootstrap' not in calls
                print('OBJECTIVE_LOCK_DRIFT_REFUSED_WITHOUT_SERVICE_MUTATION')
                return
            assert owner.main(args+['--mode','install','POTVRZUJI'])==0
            assert code.read_bytes()==source and state.read_bytes()==original_state
            journal_path=base/'adoptions'/binding/'journal.jsonl'
            recovery={'schema':'cost934.adoption.recovery.v1','original_plan':manifest,'original_binding':binding,
                'journal':{'path':str(journal_path),'sha256':plan.digest(journal_path.read_bytes())},
                'current_state_sha256':plan.digest(state.read_bytes())}
            recovery_path=home/'recovery.json';recovery_path.write_text(json.dumps(recovery))
            recovery_binding=plan.digest(plan.canonical(recovery))
            assert owner.main(['--mode','rollback','--plan',str(recovery_path),'--binding',recovery_binding,'--native-bundle',str(home/'unused'),'POTVRZUJI'])==0
            assert code.read_bytes()==original_code and caller.read_bytes()==original_caller
            assert state.read_bytes()==original_state
        print('OWNER_ENTRY_INTEGRATION_PASS synthetic_home simulated_launchd')


class OwnerIntegrationTests(unittest.TestCase):
    def test_isolated_entrypoint_preflight_install_and_recovery(self):
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'--scenario'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('OWNER_ENTRY_INTEGRATION_PASS',result.stdout)

    def test_objective_drift_under_install_lock_refuses_before_stop(self):
        result=subprocess.run([sys.executable,'-I',str(Path(__file__).resolve()),'--objective-drift'],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('OBJECTIVE_LOCK_DRIFT_REFUSED_WITHOUT_SERVICE_MUTATION',result.stdout)


if __name__=='__main__':
    if sys.argv[1:]==['--scenario']:scenario()
    elif sys.argv[1:]==['--objective-drift']:scenario(objective_drift=True)
    else:unittest.main()
