import importlib.util
from pathlib import Path
import unittest
import copy


def module(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj
m=module('adoption_recovery')


class RecoveryBindingTests(unittest.TestCase):
    def setUp(self):
        self.home=Path('/synthetic-owner');self.state=b'newest STOP attempts=3';self.journal=b'pinned journal'
        self.original={'schema':'cost934.adoption.plan.v1','preimages':{
            'code':{'path':'/code','snapshot':{'sha256':'old-code'}},
            'caller':{'path':'/caller','snapshot':{'sha256':'old-caller'}}},
            'postimage_sha256':{'code':'new-code','caller':'new-caller'}}
        binding=m.digest(m.canonical(self.original))
        self.install=self.home/'.merglbot/gh-cost-wave3/adoptions'/binding
        backup=self.install/'session-context-test'
        self.receipt={'backup_dir':str(backup),'rows':[
            {'target':'/'+key,'previous':self.original['preimages'][key]['snapshot'],
             'candidate':{'sha256':'new-'+key},'backup':str(backup/str(index))}
            for index,key in enumerate(('code','caller'))]}
        self.recovery={'schema':'cost934.adoption.recovery.v1','original_plan':self.original,
            'original_binding':binding,'journal':{'path':str(self.install/'journal.jsonl'),'sha256':m.digest(self.journal)},
            'current_state_sha256':m.digest(self.state)}

    def verify(self):
        return m.prepare(self.recovery,m.digest(m.canonical(self.recovery)),home=self.home,
            journal_bytes=self.journal,state_bytes=self.state,
            parse_journal=lambda raw,binding:([{'phase':'native_transaction','receipt':self.receipt}],None))

    def test_pinned_receipt_retains_current_state(self):
        before=self.state;original,receipt,directory=self.verify()
        self.assertEqual(directory,self.install);self.assertEqual(self.state,before)
        self.assertEqual(receipt,self.receipt)

    def test_changed_journal_or_current_state_refused(self):
        for field in ('journal','state'):
            with self.subTest(field=field):
                old=getattr(self,field);setattr(self,field,b'changed')
                with self.assertRaises(ValueError):self.verify()
                setattr(self,field,old)

    def test_foreign_backup_or_candidate_refused(self):
        original=copy.deepcopy(self.receipt)
        self.receipt['rows'][0]['backup']='/unrelated/file'
        with self.assertRaisesRegex(ValueError,'foreign_backup'):self.verify()
        self.receipt=original
        self.receipt['rows'][0]['candidate']['sha256']='foreign'
        with self.assertRaisesRegex(ValueError,'foreign_installed'):self.verify()

    def test_incomplete_receipt_requires_manual_disposition(self):
        self.receipt['rows'].pop()
        with self.assertRaisesRegex(ValueError,'incomplete_pair'):self.verify()
