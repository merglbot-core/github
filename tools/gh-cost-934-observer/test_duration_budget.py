"""Offline observer budget/state regressions; all child execution is mocked."""
import contextlib
import datetime
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('observer_candidate', HERE/'acceptance_observer.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
REAL_DATETIME = datetime.datetime


class FixedDateTime(REAL_DATETIME):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 2, 3, 0, tzinfo=m.PRAGUE)


class ObserverBudgetTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ('STATE', 'LOCK', 'AUDIT', 'OBJECTIVE', 'HANDOFF', 'CODEX', 'CAPPED'):
            self.stack.enter_context(patch.object(m, name, self.temp/name))
        self.stack.enter_context(patch.object(m, 'BASE', self.temp))
        self.stack.enter_context(patch.object(m, 'WORKSPACE', self.temp))
        for name in ('OBJECTIVE', 'HANDOFF', 'CODEX', 'CAPPED'):
            getattr(m, name).write_text('synthetic fixture only')
        self.stack.enter_context(patch.object(m, 'now_prague', return_value=FixedDateTime.now()))
        self.clock = self.stack.enter_context(patch.object(m, 'monotonic', return_value=100.0))
        self.child = self.stack.enter_context(patch.object(m.subprocess, 'run', return_value=Mock(returncode=0)))
        self.stack.enter_context(patch.object(m, 'prompt_for', return_value='synthetic prompt; no private input read'))
        self.state = {'schema_version': 1, 'stages': {'post_window': {'status': 'done', 'attempts': 1}}}
        self.save()

    def save(self):
        m.STATE.write_text(json.dumps(self.state))

    def load(self):
        return json.loads(m.STATE.read_text())

    def invoke(self, argv=None):
        argv = list(argv or [])
        if '--objective' not in argv:
            argv += ['--objective', str(self.temp/'OBJECTIVE')]
        with contextlib.redirect_stdout(io.StringIO()):
            return m.main(argv)

    def test_objective_argument_is_required_before_state_or_child_changes(self):
        before = m.STATE.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            m.main([])
        self.assertEqual(raised.exception.code, 2)
        self.child.assert_not_called()
        self.assertEqual(m.STATE.read_bytes(), before)

    def test_explicit_existing_objective_is_used_without_reading_its_contents(self):
        selected = self.temp/'selected-objective'
        selected.write_text('private synthetic fixture contents must not be loaded by observer')
        self.assertEqual(self.invoke(['--objective', str(selected)]), 0)
        self.assertEqual(m.OBJECTIVE, selected)
        self.child.assert_called_once()

    def test_missing_selected_objective_refuses_before_consuming_attempt(self):
        self.assertEqual(self.invoke(['--objective', str(self.temp/'does-not-exist')]), 2)
        self.child.assert_not_called()
        self.assertEqual(self.load()['halted'], 'MISSING_REQUIRED_LOCAL_INPUT')
        self.assertTrue(self.load()['needs_owner_review'])
        self.assertNotIn('closed_month', self.load()['stages'])

    def test_dry_run_refuses_missing_inputs_without_state_or_lock_writes(self):
        for name in ('OBJECTIVE', 'HANDOFF', 'CODEX', 'CAPPED'):
            with self.subTest(name=name):
                for required in ('OBJECTIVE', 'HANDOFF', 'CODEX', 'CAPPED'):
                    getattr(m, required).write_text('synthetic fixture only')
                selected = getattr(m, name)
                selected.unlink()
                before = m.STATE.read_bytes()
                before_files = set(self.temp.iterdir())
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = m.main(['--dry-run', '--objective', str(self.temp/'OBJECTIVE')])
                result = json.loads(output.getvalue())
                self.assertFalse(result['will_launch_codex'])
                self.assertEqual(code, 2)
                self.assertEqual(result['input_preflight_error'], 'MISSING_REQUIRED_LOCAL_INPUT')
                self.child.assert_not_called()
                self.assertEqual(m.STATE.read_bytes(), before)
                self.assertEqual(set(self.temp.iterdir()), before_files)
                selected.write_text('synthetic fixture only')

    def test_dry_run_reports_ready_with_existing_inputs_without_writes(self):
        before = m.STATE.read_bytes()
        before_files = set(self.temp.iterdir())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = m.main(['--dry-run', '--objective', str(self.temp/'OBJECTIVE')])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(result['will_launch_codex'])
        self.assertIsNone(result['input_preflight_error'])
        self.child.assert_not_called()
        self.assertEqual(m.STATE.read_bytes(), before)
        self.assertEqual(set(self.temp.iterdir()), before_files)

    def test_missing_dry_run_input_preserves_existing_stop_and_attempts(self):
        self.state['halted'] = 'EXISTING_STOP'
        self.state['stages']['closed_month'] = {'attempts': 2, 'status': 'failed_retry_bounded'}
        self.save()
        before = m.STATE.read_bytes()
        m.OBJECTIVE.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = m.main(['--dry-run', '--objective', str(self.temp/'OBJECTIVE')])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(result['will_launch_codex'])
        self.assertEqual(m.STATE.read_bytes(), before)
        self.child.assert_not_called()

    def test_relative_objective_is_rejected_before_state_access_including_dry_run(self):
        for argv in (['--objective', 'relative-objective'], ['--dry-run', '--objective', 'relative-objective']):
            with self.subTest(argv=argv):
                before = m.STATE.read_bytes()
                with patch.object(m, 'read_json', side_effect=AssertionError('must not access state')):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                        m.main(argv)
                self.assertEqual(raised.exception.code, 2)
                self.child.assert_not_called()
                self.assertEqual(m.STATE.read_bytes(), before)

    def test_clock_mocks_are_local_to_the_observer_module(self):
        self.assertIs(m.dt.datetime, REAL_DATETIME)
        self.assertEqual(m.now_prague(), FixedDateTime.now())

    def test_journal_flushes_file_then_replaces_then_flushes_directory(self):
        events = []
        real_fsync, real_replace = m.os.fsync, m.os.replace
        def sync(fd):
            events.append('sync')
            return real_fsync(fd)
        def replace(before, after):
            events.append('replace')
            return real_replace(before, after)
        with patch.object(m.os, 'fsync', side_effect=sync), patch.object(m.os, 'replace', side_effect=replace):
            m.atomic_json(m.STATE, self.state)
        self.assertEqual(events, ['sync', 'replace', 'sync'])
        self.assertEqual(self.load(), self.state)

    def test_journal_file_sync_failure_starts_no_child_and_preserves_old_state(self):
        before = m.STATE.read_bytes()
        with patch.object(m.os, 'fsync', side_effect=OSError('synthetic sync failure')):
            with self.assertRaises(OSError):
                self.invoke()
        self.child.assert_not_called()
        self.assertEqual(m.STATE.read_bytes(), before)
        self.assertEqual(list(self.temp.glob('.acceptance-state-*')), [])

    def test_journal_directory_sync_failure_starts_no_child_and_next_holder_halts(self):
        with patch.object(m.os, 'fsync', side_effect=[None, OSError('synthetic directory sync failure')]):
            with self.assertRaises(OSError):
                self.invoke()
        self.child.assert_not_called()
        self.assertEqual(self.load()['stages']['closed_month']['status'], 'running')
        self.assertEqual(self.invoke(), 2)
        self.child.assert_not_called()
        self.assertEqual(self.load()['halted'], 'PRIOR_STAGE_UNFINALIZED_NO_AUTO_RETRY')


    def test_elapsed_preflight_and_journal_time_reduce_child_and_parent_budget(self):
        self.clock.side_effect = [100.0, 110.0, 120.0, 121.0]
        self.assertEqual(self.invoke(), 0)
        args, kwargs = self.child.call_args
        self.assertEqual(args[0][1:6], ['--max-gb', '2', '--timeout', '569', '--'])
        self.assertEqual(kwargs['timeout'], 570.0)
        self.assertEqual(self.load()['stages']['closed_month']['attempts'], 1)
        self.assertFalse(self.load().get('complete', False))

    def test_guard_or_timeout_halts_without_replay(self):
        for code in (86, 137, -9, 'timeout'):
            with self.subTest(code=code):
                self.save()
                self.child.reset_mock()
                self.child.side_effect = subprocess.TimeoutExpired('mock', 1) if code=='timeout' else None
                self.child.return_value = Mock(returncode=code)
                self.assertEqual(self.invoke(), 3)
                first = self.load()
                self.assertEqual(first['halted'], 'CAPPED_RUN_KILLED_NO_AUTO_RETRY')
                self.assertTrue(first['needs_owner_review'])
                self.assertEqual(self.invoke(), 0)
                self.assertEqual(self.child.call_count, 1)
                self.assertEqual(self.load()['stages'], first['stages'])

    def test_parent_interruption_leaves_journal_and_next_holder_halts(self):
        self.child.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.invoke()
        before = self.load()['stages']['closed_month'].copy()
        self.assertEqual(before['status'], 'running')
        self.child.reset_mock()
        self.child.side_effect = None
        self.assertEqual(self.invoke(), 2)
        self.child.assert_not_called()
        self.assertEqual(self.load()['halted'], 'PRIOR_STAGE_UNFINALIZED_NO_AUTO_RETRY')
        self.assertEqual(self.load()['stages']['closed_month'], before)

    def test_child_start_failure_halts_and_keeps_attempt(self):
        self.child.side_effect = OSError('synthetic refusal')
        self.assertEqual(self.invoke(), 3)
        self.assertEqual(self.load()['halted'], 'CHILD_START_FAILED_NO_AUTO_RETRY')
        self.assertTrue(self.load()['needs_owner_review'])
        self.assertEqual(self.load()['stages']['closed_month']['attempts'], 1)
        self.child.reset_mock()
        self.assertEqual(self.invoke(), 0)
        self.child.assert_not_called()

    def test_busy_lock_is_read_only_even_with_running_record(self):
        self.state['stages']['closed_month'] = {'status': 'running', 'attempts': 1}
        self.save()
        before = m.STATE.read_bytes()
        with patch.object(m.fcntl, 'flock', side_effect=BlockingIOError):
            self.assertEqual(self.invoke(), 0)
        self.child.assert_not_called()
        self.assertEqual(m.STATE.read_bytes(), before)

    def test_dry_run_is_read_only_and_does_not_promote_running_state(self):
        self.state['stages']['closed_month'] = {'status': 'running', 'attempts': 1}
        self.save()
        m.AUDIT.write_text(json.dumps({'financial_acceptance': {'status': 'PROVEN', 'evidence': ['synthetic']}}))
        before = m.STATE.read_bytes()
        self.assertEqual(self.invoke(['--dry-run']), 0)
        self.child.assert_not_called()
        self.assertEqual(m.STATE.read_bytes(), before)

    def test_exhausted_preflight_budget_does_not_consume_attempt(self):
        self.clock.side_effect = [100.0, 699.0]
        self.assertEqual(self.invoke(), 2)
        self.child.assert_not_called()
        self.assertNotIn('closed_month', self.load()['stages'])
        self.assertTrue(self.load()['needs_owner_review'])

    def test_exhausted_journal_budget_preserves_consumed_attempt_without_child(self):
        self.clock.side_effect = [100.0, 110.0, 699.0]
        self.assertEqual(self.invoke(), 3)
        self.child.assert_not_called()
        stage = self.load()['stages']['closed_month']
        self.assertEqual(stage['attempts'], 1)
        self.assertEqual(stage['status'], 'not_started_time_budget_exhausted')
        self.assertTrue(self.load()['needs_owner_review'])

    def test_final_budget_expiry_refuses_child_instead_of_clamping_timeout(self):
        for final_time in (698.0, 699.0, 700.0, 701.0):
            with self.subTest(final_time=final_time):
                self.save()
                self.child.reset_mock()
                self.clock.side_effect = [100.0, 110.0, 120.0, final_time]
                self.assertEqual(self.invoke(), 3)
                self.child.assert_not_called()
                result = self.load()
                self.assertEqual(result['halted'], 'OBSERVER_TIME_BUDGET_EXHAUSTED_NO_AUTO_RETRY')
                self.assertTrue(result['needs_owner_review'])
                self.assertEqual(result['stages']['closed_month']['attempts'], 1)
                self.assertEqual(result['stages']['closed_month']['status'], 'not_started_time_budget_exhausted')

    def test_interrupted_stage_preserves_existing_halt_reason(self):
        self.state['halted'] = 'EXISTING_STOP_MUST_SURVIVE'
        self.state['stages']['closed_month'] = {'status': 'running', 'attempts': 1}
        self.save()
        self.assertEqual(self.invoke(), 2)
        self.child.assert_not_called()
        self.assertEqual(self.load()['halted'], 'EXISTING_STOP_MUST_SURVIVE')
        self.assertEqual(self.load()['stages'], self.state['stages'])

    def test_finalization_reserve_and_smallest_positive_child_budget(self):
        self.assertEqual(m.FINALIZATION_RESERVE_SECONDS, 10)
        self.clock.side_effect = [100.0, 110.0, 120.0, 689.0]
        self.assertEqual(self.invoke(), 0)
        args, kwargs = self.child.call_args
        self.assertEqual(args[0][4], '1')
        self.assertEqual(kwargs['timeout'], 2)

    def test_other_exit_codes_keep_original_bounded_retry_semantics(self):
        for code in (-15, 1, 2):
            with self.subTest(code=code):
                self.save()
                self.child.reset_mock()
                self.child.return_value = Mock(returncode=code)
                self.assertEqual(self.invoke(), 3)
                first = self.load()
                self.assertEqual(first['stages']['closed_month']['status'], 'failed_retry_bounded')
                self.assertNotIn('halted', first)
                self.assertEqual(self.invoke(), 0)
                self.assertEqual(self.child.call_count, 1)

    def test_existing_schedule_retry_limits_and_completion_semantics(self):
        self.assertEqual(m.CAP_SECONDS, 600)
        self.assertEqual(m.MAX_ATTEMPTS_PER_STAGE, 2)
        self.assertEqual(m.RETRY_AFTER, datetime.timedelta(hours=24))
        self.assertEqual([(n, t.isoformat()) for n,t in m.STAGES], [
            ('post_window', '2026-10-14T02:10:00+02:00'),
            ('closed_month', '2026-11-02T02:10:00+01:00'),
            ('final_settlement', '2026-12-02T02:10:00+01:00')])
        now = FixedDateTime.now()
        self.assertEqual(m.due_stage(now, self.state), 'closed_month')
        for record in ({'attempts': 2}, {'attempts': 1, 'last_attempt_at_prague': now.isoformat()}):
            self.state['stages']['closed_month'] = record
            self.assertIsNone(m.due_stage(now, self.state))
        for proof, expected in (({'status': 'PROVEN', 'evidence': []}, False),
                                ({'status': 'NOT_PROVEN', 'evidence': ['synthetic']}, False),
                                ({'status': 'PROVEN', 'evidence': ['synthetic']}, True)):
            state = {}
            m.AUDIT.write_text(json.dumps({'financial_acceptance': proof}))
            m.refresh_completion_from_audit(state)
            self.assertEqual(state.get('complete', False), expected)



if __name__ == '__main__':
    unittest.main()
