import copy
import datetime as dt
from pathlib import Path
import sys
import tempfile
import json
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci-pilot"))
import automatic as a
import controller as c

REAL_OBSERVE = a.measurements.observe

NOW = c.instant("2026-09-10T20:00:00Z")


class FakeGitHub:
    def __init__(self):
        self.values = {repo: {} for repo in c.REPOS}
        self.writes = []
        self.fail_write = None

    def selectors(self, repo):
        return dict(self.values[repo])

    def mutate(self, repo, name, value=None):
        self.writes.append((repo, name, value))
        if value is not None and name == self.fail_write:
            raise c.Gap("write_failure")
        if value is None:
            self.values[repo].pop(name, None)
        else:
            self.values[repo][name] = value

    def pages(self, *args):
        return []


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()
        self.state = {"counted_prs": ["historical#1", "historical#2"], "history": [],
                      "experiment": {"version": 1, "cases": [], "active": None}}
        self.selection = {"repo": a.REPO, "pr": 12, "kind": "synthetic", "mode": "delay",
                          "protection_sha256": "d" * 64}
        self.snapshot = patch.object(a, "snapshot", return_value=({"base": "b" * 40}, "test-branch"))
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        self.observation = patch.object(a.measurements, "observe", return_value={"unfinished_runs": 0, "data_gaps": 0})
        self.observation.start()
        self.addCleanup(self.observation.stop)

    def tick(self, receipt=None, now=NOW, **kw):
        return a.experiment_tick(self.gh, self.state, now, True, receipt,
                                 clock=lambda: now, supervisor_ready=kw.pop("supervisor_ready", lambda: True), **kw)

    def test_stop_includes_attempt_arriving_during_selector_cleanup(self):
        self.tick(self.selection)
        arrival, finished = NOW + dt.timedelta(seconds=1), NOW + dt.timedelta(seconds=2)
        current, cleanup = [NOW], c.cleanup
        def retire(gh, apply):
            clean = cleanup(gh, apply)
            current[0] = finished
            return clean
        def measure(gh, case, observed_at):
            return {"unfinished_runs": int(arrival < c.instant(case["stopped_at"])), "data_gaps": 0}
        with patch.object(c, "cleanup", side_effect=retire), patch.object(a.measurements, "observe", side_effect=measure):
            result = a.experiment_tick(self.gh, self.state, NOW, True, {"stop": True}, clock=lambda: current[0])
        self.assertEqual(finished.isoformat(), self.state["experiment"]["cases"][0]["stopped_at"])
        self.assertEqual("drain_admitted_runs", result["action"])
        self.assertEqual(1, result["history"]["unfinished_runs"])

    def test_first_event_wait_does_not_cancel_new_selection(self):
        with patch.object(a.measurements, "observe", side_effect=REAL_OBSERVE):
            result = self.tick(self.selection)
            self.assertEqual("active", result["status"])
            self.assertEqual(1, result["history"]["unfinished_runs"])
            self.assertEqual(0, result["history"]["data_gaps"])
            self.assertTrue(any(self.gh.values.values()))
            result = self.tick({"stop": True})
            self.assertEqual("drain_admitted_runs", result["action"])
            self.assertEqual(1, result["history"]["data_gaps"])
            self.assertFalse(any(self.gh.values.values()))
            self.assertEqual("previous_phase_incomplete", self.tick(self.selection)["reason"])

    def test_recovery_runs_real_controller_with_five_historical_cases(self):
        import runtime
        now = dt.datetime.now(dt.timezone.utc)
        self.state["counted_prs"] = ["historical#" + str(i) for i in range(5)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c.atomic(root / "state.json", self.state)
            c.atomic(root / "runtime.json", {"stopped": True})
            def run_controller(path):
                with patch.object(sys, "argv", ["controller.py", "tick", "--state-dir", str(path), "--apply"]):
                    self.assertEqual(c.main(), 0)
            with patch.object(runtime, "supervisor_unloaded", return_value=True), patch.object(runtime, "GitHub", return_value=self.gh), patch.object(c, "GitHub", return_value=self.gh):
                runtime.recover_experiment(root, now)
                with patch.object(runtime, "run_controller", side_effect=run_controller), patch.object(runtime, "unload") as unload:
                    self.assertEqual(runtime.wake(root, now), 0)
                    unload.assert_not_called()
            result = json.loads((root / "next_action.json").read_text())
            plan = json.loads((root / "runtime.json").read_text())
            self.assertEqual("await_experiment_selection", result["action"])
            self.assertTrue(plan["healthy"])
            self.assertFalse(plan["stopped"])
            self.assertEqual(self.state["counted_prs"], json.loads((root / "state.json").read_text())["counted_prs"])
            # The successor still enforces its own three-identity bound.
            for number in (12, 13, 14):
                self.assertEqual("active", self.tick({**self.selection, "pr": number})["status"])
                self.tick({"stop": True})
            self.assertEqual("experiment_kind_limit", self.tick({**self.selection, "pr": 15})["reason"])

    def test_missing_or_lost_supervisor_never_leaves_selectors(self):
        result = a.experiment_tick(self.gh, self.state, NOW, True, self.selection, clock=lambda: NOW)
        self.assertEqual("supervisor_not_ready", result["reason"])
        self.assertFalse(any(value is not None for _, _, value in self.gh.writes))
        for readiness in ([False], [True, False], [True, True, False]):
            self.gh.writes.clear()
            checks = iter(readiness)
            result = self.tick(self.selection, supervisor_ready=lambda: next(checks))
            self.assertEqual("supervisor_not_ready", result["reason"])
            self.assertFalse(any(self.gh.values.values()))
            self.assertIsNone(self.state["experiment"]["active"])

    def test_selection_has_durable_intent_and_global_readback(self):
        saved = []
        result = self.tick(self.selection, persist=lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual("active", result["status"])
        self.assertEqual("activating", saved[0]["experiment"]["cases"][0]["phase"])
        self.assertEqual([(a.REPO, a.AUTO_MODE, "delay"), (a.REPO, a.AUTO_PR, "12")], self.gh.writes)
        self.assertEqual(["historical#1", "historical#2"], self.state["counted_prs"])

    def test_second_selection_and_partial_write_cleanup(self):
        self.gh.fail_write = a.AUTO_PR
        result = self.tick(self.selection)
        self.assertEqual("cleanup_verified", result["action"])
        self.assertFalse(any(self.gh.values.values()))
        self.assertIsNone(self.state["experiment"]["active"])

    def test_restart_does_not_complete_partial_activation(self):
        self.tick(self.selection)
        self.state["experiment"]["cases"][0]["phase"] = "activating"
        self.assertEqual("partial_activation", self.tick()["reason"])
        self.assertFalse(any(self.gh.values.values()))

    def test_stop_and_expiry_preserve_history(self):
        self.tick(self.selection)
        self.assertEqual("selection_complete", self.tick({"stop": True})["reason"])
        self.assertEqual(1, len(self.state["experiment"]["cases"]))
        self.assertFalse(any(self.gh.values.values()))
        result = self.tick(now=c.instant(c.DEADLINE))
        self.assertEqual("deadline", result["reason"])

    def test_new_phase_waits_for_terminal_complete_previous_measurements(self):
        self.tick(self.selection)
        self.tick({"stop": True})
        for pending, gaps in ((1, 0), (0, 1)):
            with self.subTest(pending=pending, gaps=gaps):
                self.gh.writes.clear()
                with patch.object(a.measurements, "observe", return_value={
                        "unfinished_runs": pending, "data_gaps": gaps}):
                    result = self.tick({**self.selection, "mode": "baseline"})
                self.assertEqual("previous_phase_incomplete", result["reason"])
                self.assertEqual("drain_admitted_runs", result["action"])
                self.assertEqual(1, len(self.state["experiment"]["cases"]))
                self.assertFalse(any(value is not None for _, _, value in self.gh.writes))
                self.assertFalse(any(self.gh.values.values()))
        with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 0, "data_gaps": 0}):
            result = self.tick({**self.selection, "mode": "baseline"})
        self.assertEqual("active", result["status"])
        self.assertEqual(2, len(self.state["experiment"]["cases"]))

    def test_deadline_keeps_runtime_until_admitted_jobs_are_terminal(self):
        import runtime
        self.tick(self.selection)
        with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 1, "data_gaps": 0}):
            result = self.tick(now=c.instant(c.DEADLINE))
        self.assertEqual("drain_admitted_runs", result["action"])
        self.assertTrue(result["cleanup_verified"])
        self.assertFalse(runtime.schedule(result, c.instant(c.DEADLINE))["stopped"])
        self.assertFalse(any(self.gh.values.values()))

    def test_hold_and_changed_scope_cleanup(self):
        self.tick(self.selection)
        with patch.object(a, "snapshot", side_effect=c.Gap("excluded_experiment_scope")):
            self.assertEqual("excluded_experiment_scope", self.tick()["reason"])
        self.tick(self.selection)
        self.assertEqual("owner_hold", self.tick(hold=True)["reason"])
        self.assertFalse(any(self.gh.values.values()))

    def test_case_kind_cannot_change_and_limit_does_not_stop_whole_runtime(self):
        self.tick(self.selection)
        self.tick({"stop": True})
        self.assertEqual("case_provenance_changed", self.tick({**self.selection, "kind": "natural"})["reason"])
        for number in (13, 14):
            self.tick({**self.selection, "pr": number})
            self.tick({"stop": True})
        self.assertEqual("experiment_kind_limit", self.tick({**self.selection, "pr": 15})["reason"])

    def test_measurement_gap_cleans_selection_without_claiming_zero(self):
        with patch.object(a.measurements, "observe", side_effect=c.Gap("api_failure")):
            result = self.tick(self.selection)
        self.assertEqual("measurement_gap", result["reason"])
        self.assertEqual(1, result["history"]["data_gaps"])
        self.assertFalse(any(self.gh.values.values()))

    def test_begin_does_not_reset_old_cases_or_accept_active_old_receipt(self):
        del self.state["experiment"]
        self.state["receipt"] = {"old": True}
        with self.assertRaisesRegex(c.Gap, "old_admission_active"):
            self.tick({"begin": True})
        del self.state["receipt"]
        self.assertEqual("inactive", self.tick({"begin": True})["status"])
        self.assertEqual(["historical#1", "historical#2"], self.state["counted_prs"])



if __name__ == "__main__":
    unittest.main()
