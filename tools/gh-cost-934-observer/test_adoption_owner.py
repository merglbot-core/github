import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('cost934_owner',Path(__file__).with_name('adoption_owner.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class OwnerEntryTests(unittest.TestCase):
    def test_default_is_readonly_and_binding_is_explicit(self):
        args=m.parser().parse_args(['--plan','/p','--binding','a'*64,'--native-bundle','/b'])
        self.assertEqual(args.mode,'preflight');self.assertIsNone(args.confirmation)

    def test_bounded_read_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();p=base/'file';p.write_bytes(b'12345')
            self.assertEqual(m.read_bytes(p),b'12345')
            with self.assertRaisesRegex(ValueError,'bounded_input'):m.read_bytes(p,limit=4)
            link=base/'link';link.symlink_to(p)
            with self.assertRaisesRegex(ValueError,'symlink'):m.read_bytes(link)

    def test_hold_directory_missing_is_not_absence_of_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp).resolve()
            with self.assertRaises(FileNotFoundError):m.hold_exists(home)
            directory=home/'.claude/merglbot-preauth';directory.mkdir(parents=True)
            self.assertFalse(m.hold_exists(home))
            (directory/'OWNER_HOLD').write_text('hold')
            self.assertTrue(m.hold_exists(home))
