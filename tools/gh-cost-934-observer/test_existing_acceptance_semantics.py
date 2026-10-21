"""Offline date-gate and terminal-state checks for the #934 wake-up."""

import datetime as dt
import importlib.util
from pathlib import Path
from unittest.mock import patch
import unittest


SPEC = importlib.util.spec_from_file_location(
    'acceptance_observer', Path(__file__).resolve().parent/'acceptance_observer.py')
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


class AcceptanceObserverTests(unittest.TestCase):
    def at(self, year, month, day, hour=2, minute=10):
        return dt.datetime(year, month, day, hour, minute, tzinfo=observer.PRAGUE)

    def test_no_launch_before_closed_window(self):
        self.assertIsNone(observer.due_stage(self.at(2026, 10, 14, 2, 9), {}))
        self.assertEqual(observer.due_stage(self.at(2026, 10, 14), {}), 'post_window')

    def test_old_window_does_not_consume_bounded_attempts(self):
        for day in (11, 12, 13):
            state = {'stages': {}}
            self.assertIsNone(observer.due_stage(self.at(2026, 10, day), state))
            self.assertEqual(state, {'stages': {}})

    def test_scheduled_prompt_keeps_scope_and_revalidates_window(self):
        text = observer.prompt_for('post_window')
        self.assertIn(str(observer.OBJECTIVE), text)
        self.assertIn('latest relevant configuration merge', text)
        self.assertIn('only if it has closed', text)
        self.assertIn('Do not duplicate', text)
        self.assertIn('financial_acceptance as status PROVEN', text)
        self.assertIn('only after live settled net acceptance', text)

    def test_once_per_stage_then_next_date(self):
        state = {'stages': {'post_window': {'status': 'done'}}}
        self.assertIsNone(observer.due_stage(self.at(2026, 11, 2, 2, 9), state))
        self.assertEqual(observer.due_stage(self.at(2026, 11, 2), state), 'closed_month')

    def test_retry_requires_day_and_is_bounded(self):
        state = {'stages': {'post_window': {
            'attempts': 1, 'last_attempt_at_prague': self.at(2026, 10, 14).isoformat()}}}
        self.assertIsNone(observer.due_stage(self.at(2026, 10, 15, 2, 9), state))
        self.assertEqual(observer.due_stage(self.at(2026, 10, 15), state), 'post_window')
        state['stages']['post_window']['attempts'] = 2
        self.assertIsNone(observer.due_stage(self.at(2026, 10, 16), state))

    def test_completion_and_guard_halt_prevent_launch(self):
        for state in ({'complete': True}, {'halted': 'CAPPED_RUN_KILLED_NO_AUTO_RETRY'}):
            self.assertIsNone(observer.due_stage(self.at(2026, 12, 3), state))

    def test_unresolved_exhaustion_is_explicit_but_guard_reason_survives(self):
        state = {'stages': {name: {'attempts': 2} for name, _ in observer.STAGES}}
        self.assertTrue(observer.exhausted_without_completion(self.at(2026, 12, 3), state))
        state['halted'] = 'CAPPED_RUN_KILLED_NO_AUTO_RETRY'
        self.assertFalse(observer.exhausted_without_completion(self.at(2026, 12, 3), state))

    def test_successful_stages_without_financial_proof_end_unresolved(self):
        state = {'stages': {name: {'attempts': 1, 'status': 'done'}
                            for name, _ in observer.STAGES}}
        self.assertFalse(observer.exhausted_without_completion(self.at(2026, 12, 2, 2, 9), state))
        self.assertTrue(observer.exhausted_without_completion(self.at(2026, 12, 2), state))
        state['complete'] = True
        self.assertFalse(observer.exhausted_without_completion(self.at(2026, 12, 3), state))

    def test_child_proof_readback_prevents_false_unresolved_state(self):
        state = {'stages': {name: {'attempts': 1, 'status': 'done'}
                            for name, _ in observer.STAGES}}
        with patch.object(observer, 'AUDIT') as audit, patch.object(
                observer, 'read_json', return_value={'financial_acceptance': {'status': 'PROVEN', 'evidence': ['settled-readback']}}):
            audit.exists.return_value = True
            observer.refresh_completion_from_audit(state)
        self.assertTrue(state['complete'])
        self.assertFalse(observer.exhausted_without_completion(self.at(2026, 12, 3), state))

    def test_technical_completion_does_not_cancel_financial_followup(self):
        for payload in ({'goal_completion': 'PROVEN'},
                        {'financial_acceptance': {'status': 'PROVEN', 'evidence': []}}):
            state = {}
            with patch.object(observer, 'AUDIT') as audit, patch.object(
                    observer, 'read_json', return_value=payload):
                audit.exists.return_value = True
                observer.refresh_completion_from_audit(state)
            self.assertNotIn('complete', state)
            self.assertEqual(observer.due_stage(self.at(2026, 10, 14), state), 'post_window')


if __name__ == '__main__':
    unittest.main()
