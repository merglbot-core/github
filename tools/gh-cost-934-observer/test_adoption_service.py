import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('cost934_service', Path(__file__).with_name('adoption_service.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture(runs=1, code=0):
    return (f'gui/501/{m.LABEL} = {{\n\tpath = /synthetic/caller.plist\n'
            '\tstate = not running\n\tprogram = /synthetic/python\n'
            '\targuments = {\n\t\t/synthetic/python\n\t\t/synthetic/code\n\t}\n'
            f'\truns = {runs}\n\tlast exit code = {code}\n'
            '\tresource coalition = {\n\t\tstate = active\n\t}\n}\n')


class ServiceTests(unittest.TestCase):
    def test_bootout_rechecks_identity_and_idle_after_prior_verification(self):
        for changed in [fixture().replace('/synthetic/code', '/foreign/code'),
                        fixture().replace('state = not running', 'state = running'),
                        fixture().replace('\truns = 1', '\tpid = 123\n\truns = 1'),
                        fixture().replace('/synthetic/caller.plist', '/foreign/caller.plist')]:
            with self.subTest(snapshot=changed):
                service=self.service('unused')
                snapshots=iter([fixture(), changed])
                service.read=lambda: m.parse_print(next(snapshots), 'gui/501')
                service.verify_old_idle()
                # self.service's runner rejects any non-read command.
                with self.assertRaises(ValueError):
                    service.stop_and_verify_absent()

    def test_bootout_accepts_only_current_admitted_idle_generation(self):
        calls=[]
        service=self.service('unused')
        snapshots=iter([m.parse_print(fixture(), 'gui/501'), None])
        service.read=lambda: next(snapshots)
        def run(argv, timeout):
            calls.append(argv)
            return SimpleNamespace(returncode=0)
        service.run=run
        service.stop_and_verify_absent()
        self.assertEqual(calls,[['/bin/launchctl','bootout',service.target]])

    def test_recovery_does_not_admit_old_generation_before_rollback(self):
        service=self.service('unused')
        service.new_args=['/synthetic/python','/synthetic/new-code']
        new=fixture().replace('/synthetic/code','/synthetic/new-code')
        snapshots=iter([m.parse_print(new,'gui/501'),m.parse_print(fixture(),'gui/501')])
        service.read=lambda: next(snapshots)
        service.verify_current_idle_or_absent()
        with self.assertRaisesRegex(ValueError,'loaded_caller_mismatch'):
            service.stop_and_verify_absent()

    def service(self, text, returncode=0, stderr=''):
        def run(args, timeout):
            self.assertEqual(args[1], 'print')  # Every test here is read-only.
            return SimpleNamespace(returncode=returncode, stdout=text, stderr=stderr)
        return m.Service(501, '/synthetic/caller.plist', ['/synthetic/python', '/synthetic/code'],
                         ['/synthetic/python', '/synthetic/code'], run=run,
                         monotonic=lambda: 0, sleep=lambda seconds: None)

    def test_nested_active_state_does_not_override_top_level_idle(self):
        row = m.parse_print(fixture(), 'gui/501')
        self.assertEqual(row['state'], 'not running')
        self.service(fixture()).verify_bootstrap_completed()

    def test_no_pid_without_completed_run_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'completion_unproven'):
            self.service(fixture(runs=0)).verify_bootstrap_completed()

    def test_nonzero_exit_and_multiple_runs_refused(self):
        for text in (fixture(code=2), fixture(runs=2)):
            with self.assertRaises(ValueError):
                self.service(text).verify_bootstrap_completed()

    def test_only_specific_absent_error_means_absent(self):
        missing=f'Bad request.\nCould not find service "{m.LABEL}" in domain for user gui: 501\n'
        self.assertIsNone(self.service('', 113, missing).read())
        for stderr in ('Permission denied', missing.replace(m.LABEL, 'foreign')):
            with self.assertRaisesRegex(ValueError, 'read_failed'):
                self.service('', 113, stderr).read()

    def test_duplicate_or_foreign_fields_refused(self):
        for text in (fixture().replace('gui/501/', 'gui/502/', 1),
                     fixture().replace('\truns = 1', '\tstate = running\n\truns = 1')):
            with self.assertRaises(ValueError):
                m.parse_print(text, 'gui/501')

    def test_never_exited_is_pending_until_numeric_completion(self):
        snapshots = iter([fixture(code='(never exited)'), fixture(code=0)])
        service = self.service('unused')
        service.read = lambda: m.parse_print(next(snapshots), 'gui/501')
        service.verify_bootstrap_completed()
        self.assertIsNone(m.parse_print(fixture(code='(never exited)'), 'gui/501')['last exit code'])
        with self.assertRaisesRegex(ValueError, 'completion_unproven'):
            self.service(fixture(code='(never exited)')).verify_bootstrap_completed()

    def test_non_numeric_pid_runs_and_unknown_exit_remain_refused(self):
        for text in [fixture(runs='(never exited)'), fixture(code='unknown'),
                     fixture().replace('\truns = 1', '\tpid = unknown\n\truns = 1')]:
            with self.assertRaisesRegex(ValueError, 'unparseable_launchd_number'):
                m.parse_print(text, 'gui/501')

    def test_recovery_requires_admitted_idle_service_or_proven_absence(self):
        self.service(fixture()).verify_current_idle_or_absent()
        for text in [fixture().replace('\tstate = not running', '\tstate = running'),
                     fixture().replace('\truns = 1', '\tpid = 123\n\truns = 1'),
                     fixture().replace('/synthetic/code', '/foreign/code')]:
            with self.assertRaises(ValueError):
                self.service(text).verify_current_idle_or_absent()
        missing = f'Could not find service "{m.LABEL}" in domain for user gui: 501\n'
        self.service('', 113, missing).verify_current_idle_or_absent()
