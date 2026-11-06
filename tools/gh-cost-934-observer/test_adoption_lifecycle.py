from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('cost934_lifecycle', Path(__file__).with_name('adoption_lifecycle.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.locked = False
        self.state = b'current attempts and STOP'
        self.events = []
        self.phases = []
        self.receipt = {'state': 'installed_verified'}
        self.change_state = False
        self.bootstrap_error = False

    def run_install(self):
        @contextmanager
        def lock():
            self.locked = True
            self.events.append('lock')
            try:
                yield
            finally:
                self.locked = False
                self.events.append('unlock')
        def bootstrap():
            self.assertTrue(self.locked)
            self.events.append('bootstrap')
        def completed():
            self.assertTrue(self.locked)
            self.events.append('completed')
            if self.change_state:
                self.state = b'new counter'
            if self.bootstrap_error:
                raise ValueError('no_proven_bootstrap_completion')
        def replace():
            self.assertTrue(self.locked)
            self.events.append('replace')
            return self.receipt
        service = SimpleNamespace(verify_old_idle=lambda: self.events.append('old_idle'),
            stop_and_verify_absent=lambda: self.events.append('stop'),
            stop_when_idle_and_verify_absent=lambda: self.events.append('stop'),
            verify_absent=lambda: self.events.append('absent'),
            bootstrap_new=bootstrap, verify_bootstrap_completed=completed)
        return m.install(service=service, lock=lock, guard=lambda: None,
            state_read=lambda: self.state, replace_files=replace,
            journal=lambda row: self.phases.append(row['phase']))

    def test_loaded_verification_precedes_unlock_and_is_not_financial_success(self):
        result = self.run_install()
        self.assertEqual(self.events, ['lock', 'old_idle', 'stop', 'absent', 'replace',
                                       'bootstrap', 'completed', 'unlock'])
        self.assertEqual(result['state'], 'installed_loaded_verified')
        self.assertEqual(result['financial_acceptance'], 'NOT_PROVEN')
        self.assertFalse(result['natural_run_verified'])

    def test_partial_replacement_never_bootstraps(self):
        self.receipt = {'state': 'partial_requires_live_readback'}
        result = self.run_install()
        self.assertNotIn('bootstrap', self.events)
        self.assertEqual(result['state'], 'partial_service_stopped')

    def test_unproven_bootstrap_stops_service_and_preserves_failure(self):
        self.bootstrap_error = True
        with self.assertRaisesRegex(ValueError, 'no_proven'):
            self.run_install()
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])
        self.assertEqual(self.phases[-1], 'recovery_required_service_stopped')

    def test_new_counter_never_reverted(self):
        self.change_state = True
        with self.assertRaisesRegex(ValueError, 'state_changed_during_bootstrap'):
            self.run_install()
        self.assertEqual(self.state, b'new counter')
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, partial=False, change_state=False):
        self.state = b'newest STOP attempts=3'
        self.events = []
        self.locked = False
        @contextmanager
        def lock():
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
                self.events.append('unlock')
        def restored():
            self.assertTrue(self.locked)
            if change_state:
                self.state = b'even newer STOP attempts=4'
            return {'state': 'partial' if partial else 'rollback_verified'}
        def bootstrap():
            self.assertTrue(self.locked)
            self.events.append('bootstrap_previous')
        service = SimpleNamespace(verify_current_idle_or_absent=lambda: self.events.append('current_idle'),
            stop_and_verify_absent=lambda: self.events.append('stop'),
            stop_when_idle_and_verify_absent=lambda: self.events.append('stop'),
            verify_absent=lambda: None, bootstrap_previous=bootstrap,
            verify_previous_bootstrap_completed=lambda: self.events.append('verified_previous'))
        return m.recover(service=service, lock=lock, guard=lambda: None,
            state_read=lambda: self.state, restore_files=restored, journal=lambda r: None)

    def test_recovery_bootstraps_restored_caller_under_lock(self):
        result = self.run_recovery()
        self.assertEqual(result['state'], 'rollback_loaded_verified')
        self.assertEqual(self.events, ['current_idle', 'stop', 'bootstrap_previous', 'verified_previous', 'unlock'])
        self.assertEqual(self.state, b'newest STOP attempts=3')

    def test_partial_recovery_never_starts_service(self):
        result = self.run_recovery(partial=True)
        self.assertEqual(result['state'], 'partial_recovery_service_stopped')
        self.assertNotIn('bootstrap_previous', self.events)

    def test_new_state_during_recovery_is_preserved(self):
        with self.assertRaisesRegex(ValueError, 'state_changed_during_recovery'):
            self.run_recovery(change_state=True)
        self.assertEqual(self.state, b'even newer STOP attempts=4')
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])

class RecoveryAdmissionTests(unittest.TestCase):
    def test_busy_or_foreign_service_never_booted_out(self):
        for reason in ('current_service_not_idle', 'loaded_caller_mismatch'):
            events = []
            @contextmanager
            def lock():
                events.append('lock')
                yield
            def refuse():
                raise ValueError(reason)
            service = SimpleNamespace(verify_current_idle_or_absent=refuse,
                                      stop_and_verify_absent=lambda: events.append('stop'))
            with self.assertRaisesRegex(ValueError, reason):
                m.recover(service=service, lock=lock, guard=lambda: None,
                          state_read=lambda: b'state', restore_files=lambda: events.append('restore'),
                          journal=lambda row: events.append('journal'))
            self.assertEqual(events, ['lock'])

    def test_lock_contention_prevents_service_mutation(self):
        events = []
        @contextmanager
        def lock():
            raise ValueError('observer_lock_busy')
            yield
        service = SimpleNamespace(verify_current_idle_or_absent=lambda: events.append('read'),
                                  stop_and_verify_absent=lambda: events.append('stop'))
        with self.assertRaisesRegex(ValueError, 'observer_lock_busy'):
            m.recover(service=service, lock=lock, guard=lambda: None,
                      state_read=lambda: b'state', restore_files=lambda: events.append('restore'),
                      journal=lambda row: events.append('journal'))
        self.assertEqual(events, [])


class CleanupFailureTests(unittest.TestCase):
    def test_install_and_recovery_preserve_original_and_journal_unknown(self):
        for recovering in (False, True):
            with self.subTest(recovering=recovering):
                events = []
                original = RuntimeError('bootstrap_completion_unproven')
                cleanup = ValueError('cleanup_idle_unproven')
                @contextmanager
                def lock():
                    events.append('lock')
                    try:
                        yield
                    finally:
                        events.append('unlock')
                def fail():
                    raise original
                def cleanup_fail():
                    self.assertNotIn('unlock', events)
                    raise cleanup
                service = SimpleNamespace(
                    verify_old_idle=lambda: None, verify_current_idle_or_absent=lambda: None,
                    stop_and_verify_absent=lambda: None, verify_absent=lambda: None,
                    bootstrap_new=lambda: None, bootstrap_previous=lambda: None,
                    verify_bootstrap_completed=fail, verify_previous_bootstrap_completed=fail,
                    stop_when_idle_and_verify_absent=cleanup_fail)
                rows = []
                def journal(row):
                    self.assertNotIn('unlock', events)
                    rows.append(row)
                kwargs = dict(service=service, lock=lock, guard=lambda: None,
                              state_read=lambda: b'unchanged STOP', journal=journal)
                with self.assertRaises(RuntimeError) as raised:
                    if recovering:
                        m.recover(**kwargs, restore_files=lambda: {'state': 'rollback_verified'})
                    else:
                        m.install(**kwargs, replace_files=lambda: {'state': 'installed_verified'})
                self.assertIs(raised.exception, original)
                self.assertIs(raised.exception.__cause__, cleanup)
                self.assertEqual(rows[-1]['phase'], 'recovery_required_service_state_unknown')
                self.assertEqual(events[-1], 'unlock')

    def test_journal_failure_does_not_replace_original(self):
        original = RuntimeError('original')
        def broken_journal(row):
            raise OSError('journal unavailable')
        with self.assertRaises(RuntimeError) as raised:
            m.cleanup_failure(SimpleNamespace(stop_when_idle_and_verify_absent=lambda: None),
                              broken_journal, original, 'stopped')
        self.assertIs(raised.exception, original)
        self.assertIsInstance(raised.exception.__cause__, OSError)


class RealServiceCleanupTests(unittest.TestCase):
    def test_running_bootstrap_timeout_through_real_service_install_and_recover(self):
        service_spec = importlib.util.spec_from_file_location(
            'cleanup_service', Path(__file__).with_name('adoption_service.py'))
        services = importlib.util.module_from_spec(service_spec)
        service_spec.loader.exec_module(services)
        for recovering in (False, True):
            with self.subTest(recovering=recovering):
                old = ['/synthetic/python', '/synthetic/old']
                new = ['/synthetic/python', '/synthetic/new']
                live = {'loaded': True, 'running': False,
                        'args': new if recovering else old, 'bootouts': 0, 'locked': False}
                rows = []
                @contextmanager
                def lock():
                    live['locked'] = True
                    try:
                        yield
                    finally:
                        live['locked'] = False
                def run(argv, timeout):
                    op = argv[1]
                    if op == 'bootout':
                        self.assertTrue(live['locked'])
                        self.assertFalse(live['running'])
                        live['loaded'] = False
                        live['bootouts'] += 1
                    elif op == 'bootstrap':
                        live.update(loaded=True, running=True, args=old if recovering else new)
                    elif op == 'print':
                        if not live['loaded']:
                            return SimpleNamespace(returncode=113, stdout='', stderr=
                                f'Could not find service "{services.LABEL}" in domain for user gui: 501\n')
                        args = live['args']
                        text = f'gui/501/{services.LABEL} = {{\n\tpath = /synthetic/caller.plist\n'
                        text += '\tstate = ' + ('running' if live['running'] else 'not running') + '\n'
                        text += '\tprogram = ' + args[0] + '\n\targuments = {\n'
                        text += ''.join('\t\t' + a + '\n' for a in args) + '\t}\n\truns = 1\n'
                        text += '\tpid = 123\n' if live['running'] else '\tlast exit code = 0\n'
                        return SimpleNamespace(returncode=0, stdout=text + '}\n', stderr='')
                    else:
                        raise AssertionError(op)
                    return SimpleNamespace(returncode=0, stdout='', stderr='')
                service = services.Service(501, '/synthetic/caller.plist', old, new,
                    run=run, monotonic=lambda: 0, sleep=lambda seconds: None)
                def journal(row):
                    self.assertTrue(live['locked'])
                    rows.append(row)
                kwargs = dict(service=service, lock=lock, guard=lambda: None,
                              state_read=lambda: b'STOP unchanged', journal=journal)
                with self.assertRaisesRegex(ValueError, 'bootstrap_completion_unproven') as raised:
                    if recovering:
                        m.recover(**kwargs, restore_files=lambda: {'state': 'rollback_verified'})
                    else:
                        m.install(**kwargs, replace_files=lambda: {'state': 'installed_verified'})
                self.assertEqual(str(raised.exception.__cause__), 'cleanup_idle_unproven')
                self.assertEqual(rows[-1]['phase'], 'recovery_required_service_state_unknown')
                self.assertEqual(live['bootouts'], 1)
                self.assertTrue(live['loaded'] and live['running'])
                self.assertFalse(live['locked'])


class InitialStopFailureTests(unittest.TestCase):
    def test_initial_stop_and_poststop_journal_failures_use_cleanup_before_unlock(self):
        for recovering in (False, True):
            for failure_at in ('stop', 'journal'):
                for cleanup_fails in (False, True):
                    with self.subTest(recovering=recovering, failure_at=failure_at,
                                      cleanup_fails=cleanup_fails):
                        events, rows = [], []
                        original = RuntimeError('initial_' + failure_at + '_failed')
                        cleanup_error = ValueError('cleanup_state_unproven')
                        stopped = 'recovery_service_stopped' if recovering else 'service_stopped'
                        @contextmanager
                        def lock():
                            events.append('lock')
                            try:
                                yield
                            finally:
                                events.append('unlock')
                        def stop():
                            events.append('stop')
                            if failure_at == 'stop':
                                raise original
                        def cleanup():
                            self.assertNotIn('unlock', events)
                            events.append('cleanup')
                            if cleanup_fails:
                                raise cleanup_error
                        def journal(row):
                            self.assertNotIn('unlock', events)
                            if row['phase'] == stopped and failure_at == 'journal':
                                raise original
                            rows.append(row)
                        def forbidden():
                            self.fail('No file transaction or bootstrap after initial stop failure')
                        service = SimpleNamespace(verify_old_idle=lambda: None,
                            verify_current_idle_or_absent=lambda: None,
                            stop_and_verify_absent=stop, stop_when_idle_and_verify_absent=cleanup,
                            verify_absent=forbidden, bootstrap_new=forbidden,
                            bootstrap_previous=forbidden)
                        kwargs = dict(service=service, lock=lock, guard=lambda: None,
                            state_read=lambda: b'unchanged', journal=journal)
                        with self.assertRaises(RuntimeError) as raised:
                            if recovering:
                                m.recover(**kwargs, restore_files=forbidden)
                            else:
                                m.install(**kwargs, replace_files=forbidden)
                        self.assertIs(raised.exception, original)
                        self.assertEqual(events, ['lock', 'stop', 'cleanup', 'unlock'])
                        expected = ('recovery_required_service_state_unknown' if cleanup_fails else
                                    'recovery_failed_service_stopped' if recovering else
                                    'recovery_required_service_stopped')
                        self.assertEqual(rows[-1]['phase'], expected)
                        if cleanup_fails:
                            self.assertIs(raised.exception.__cause__, cleanup_error)
