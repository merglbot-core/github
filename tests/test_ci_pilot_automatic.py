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
        self.spec = {"repo": a.REPO, "pr": 12, "kind": "synthetic", "mode": "delay",
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

    def test_compatibility_case_cannot_be_synthetic_or_baseline(self):
        for kind, mode in (("synthetic", "delay"), ("natural", "baseline")):
            result = self.tick({**self.spec, "pr": a.COMPATIBILITY_PR,
                                "kind": kind, "mode": mode})
            self.assertEqual("compatibility_requires_natural_delay", result["reason"])
            self.assertFalse(any(value is not None for _, _, value in self.gh.writes))
        result = self.tick({**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"})
        self.assertEqual("active", result["status"])

    def test_natural_no_event_times_out_at_24h_and_cannot_reactivate(self):
        import runtime
        spec = {**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"}
        self.tick(spec)
        self.assertEqual("active", self.tick(now=NOW + dt.timedelta(hours=24, seconds=-1))["status"])
        result = self.tick(now=NOW + dt.timedelta(hours=24))
        self.assertEqual("compatibility_no_event_24h", result["reason"])
        self.assertFalse(any(self.gh.values.values()))
        self.assertTrue(runtime.schedule(result, NOW)["stopped"])
        self.assertIsNone(self.state["experiment"]["active"])
        self.assertEqual("compatibility_no_event_24h", self.tick(spec)["reason"])

    def test_natural_event_within_window_keeps_selection(self):
        self.tick({**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"})
        case = self.state["experiment"]["cases"][0]
        case["measurements"] = {"head": {"latest": {"observations": [
            {"attempt": 1, "created_at": (NOW + dt.timedelta(hours=1)).isoformat()}
        ]}}}
        self.assertEqual("active", self.tick(now=NOW + dt.timedelta(hours=24))["status"])

    def test_late_event_or_rerun_cannot_rescue_expired_window(self):
        for offset, attempt in [(24, 1), (-1, 1), (1, 2)]:
            with self.subTest(offset=offset, attempt=attempt):
                self.state = {"experiment": {"version": 1, "cases": [], "active": None}}
                self.gh.values = {repo: {} for repo in c.REPOS}
                self.tick({**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"})
                case = self.state["experiment"]["cases"][0]
                case["measurements"] = {"head": {"latest": {"observations": [
                    {"attempt": attempt, "created_at": (NOW + dt.timedelta(hours=offset)).isoformat()}
                ]}}}
                with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 1, "data_gaps": 0}):
                    result = self.tick(now=NOW + dt.timedelta(hours=24))
                self.assertEqual("drain_admitted_runs", result["action"])
                self.assertFalse(any(self.gh.values.values()))

    def test_timeout_empty_inventory_is_complete_but_read_failure_is_not(self):
        case = {"branch": "test", "pr": a.COMPATIBILITY_PR,
                "started_at": NOW.isoformat(), "stopped_at": (NOW + dt.timedelta(hours=24)).isoformat(),
                "reason": "compatibility_no_event_24h", "initial_base": "b" * 40}
        result = REAL_OBSERVE(self.gh, case, NOW + dt.timedelta(hours=24))
        self.assertEqual(0, result["data_gaps"])
        self.assertEqual(0, result["unfinished_runs"])
        self.observation.stop()
        with patch.object(self.gh, "pages", side_effect=RuntimeError("API unavailable")):
            result = a.measurements.histories(self.gh, {"cases": [case]}, NOW)
        self.assertEqual(1, result["data_gaps"])

    def test_other_pr_refused_by_real_snapshot_before_any_api(self):
        self.snapshot.stop()
        with self.assertRaisesRegex(c.Gap, "unsupported_compatibility_pr"):
            a.snapshot(self.gh, a.COMPATIBILITY_PR + 1, "a" * 64)

    def test_closed_compatibility_case_drains_then_stops_without_reactivation(self):
        import runtime
        spec = {**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"}
        self.tick(spec)
        with patch.object(a, "snapshot", side_effect=c.Gap("compatibility_case_closed")):
            with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 1, "data_gaps": 0}):
                result = self.tick()
                self.assertEqual("drain_admitted_runs", result["action"])
                self.assertFalse(runtime.schedule(result, NOW)["stopped"])
            result = self.tick()
            self.assertEqual("cleanup_verified", result["action"])
            self.assertTrue(runtime.schedule(result, NOW)["stopped"])
        self.gh.writes.clear()
        self.assertEqual("compatibility_case_closed", self.tick(spec)["reason"])
        self.assertFalse(any(value is not None for _, _, value in self.gh.writes))

    def test_scope_expansion_then_contraction_cannot_reselect_case(self):
        import runtime
        spec = {**self.spec, "pr": a.COMPATIBILITY_PR, "kind": "natural"}
        self.tick(spec)
        with patch.object(a, "snapshot", side_effect=c.Gap("compatibility_scope_expanded")):
            result = self.tick()
        self.assertEqual("compatibility_scope_expanded", result["reason"])
        self.assertFalse(any(self.gh.values.values()))
        self.assertTrue(runtime.schedule(result, NOW)["stopped"])
        self.gh.writes.clear()
        # The ordinary valid snapshot is restored, representing scope contraction.
        self.assertEqual("compatibility_scope_expanded", self.tick(spec)["reason"])
        self.assertFalse(any(value is not None for _, _, value in self.gh.writes))

    def test_cleanup_keeps_racing_attempt(self):
        self.tick(self.spec)
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

    def test_recovery_with_five_old_cases(self):
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
            for number in (12, 13, 14):
                self.assertEqual("active", self.tick({**self.spec, "pr": number})["status"])
                self.tick({"stop": True})
            self.assertEqual("experiment_kind_limit", self.tick({**self.spec, "pr": 15})["reason"])

    def test_retry_requires_zero_writes(self):
        with patch.object(a.measurements, "observe", side_effect=REAL_OBSERVE):
            result = a.experiment_tick(self.gh, self.state, NOW, True, self.spec, clock=lambda: NOW)
            self.assertEqual("supervisor_not_ready", result["reason"])
            self.assertEqual([], self.state["experiment"]["cases"])
            readiness = iter([True, False])
            result = self.tick(self.spec, supervisor_ready=lambda: next(readiness))
            self.assertEqual("aborted_no_write", self.state["experiment"]["cases"][0]["phase"])
            self.assertFalse(any(value is not None for _, _, value in self.gh.writes))
            result = self.tick(self.spec)
            self.assertEqual(("active", 1), (result["status"], result["history"]["unfinished_runs"]))
            self.assertTrue(any(self.gh.values.values()))
            result = self.tick({"stop": True})
            self.assertEqual(("drain_admitted_runs", 1), (result["action"], result["history"]["data_gaps"]))
            self.assertFalse(any(self.gh.values.values()))
            self.assertEqual("previous_phase_incomplete", self.tick(self.spec)["reason"])
        # A failed write attempt remains uncertain.
        self.state["experiment"] = {"version": 1, "cases": [], "active": None}
        self.gh.fail_write = a.AUTO_MODE
        with patch.object(a.measurements, "observe", side_effect=REAL_OBSERVE):
            self.tick(self.spec)
            self.assertTrue(self.state["experiment"]["cases"][0]["write_attempted"])
            self.assertEqual("previous_phase_incomplete", self.tick(self.spec)["reason"])

    def test_durable_selection_and_readback(self):
        saved = []
        result = self.tick(self.spec, persist=lambda s: saved.append(copy.deepcopy(s)))
        self.assertEqual("active", result["status"])
        self.assertEqual("activating", saved[0]["experiment"]["cases"][0]["phase"])
        self.assertEqual([(a.REPO, a.AUTO_MODE, "delay"), (a.REPO, a.AUTO_PR, "12")], self.gh.writes)
        self.assertEqual(["historical#1", "historical#2"], self.state["counted_prs"])

    def test_second_selection_and_partial_write_cleanup(self):
        self.gh.fail_write = a.AUTO_PR
        result = self.tick(self.spec)
        self.assertEqual("cleanup_verified", result["action"])
        self.assertFalse(any(self.gh.values.values()))
        self.assertIsNone(self.state["experiment"]["active"])

    def test_restart_does_not_complete_partial_activation(self):
        self.tick(self.spec)
        self.state["experiment"]["cases"][0]["phase"] = "activating"
        self.assertEqual("partial_activation", self.tick()["reason"])
        self.assertFalse(any(self.gh.values.values()))

    def test_stop_and_expiry_preserve_history(self):
        self.tick(self.spec)
        self.assertEqual("selection_complete", self.tick({"stop": True})["reason"])
        self.assertEqual(1, len(self.state["experiment"]["cases"]))
        self.assertFalse(any(self.gh.values.values()))
        result = self.tick(now=c.instant(c.DEADLINE))
        self.assertEqual("deadline", result["reason"])

    def test_previous_phase_must_be_complete(self):
        self.tick(self.spec)
        self.tick({"stop": True})
        for pending, gaps in ((1, 0), (0, 1)):
            with self.subTest(pending=pending, gaps=gaps):
                self.gh.writes.clear()
                with patch.object(a.measurements, "observe", return_value={
                        "unfinished_runs": pending, "data_gaps": gaps}):
                    result = self.tick({**self.spec, "mode": "baseline"})
                self.assertEqual("previous_phase_incomplete", result["reason"])
                self.assertEqual("drain_admitted_runs", result["action"])
                self.assertEqual(1, len(self.state["experiment"]["cases"]))
                self.assertFalse(any(value is not None for _, _, value in self.gh.writes))
                self.assertFalse(any(self.gh.values.values()))
        with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 0, "data_gaps": 0}):
            result = self.tick({**self.spec, "mode": "baseline"})
        self.assertEqual("active", result["status"])
        self.assertEqual(2, len(self.state["experiment"]["cases"]))

    def test_deadline_drains_admitted_jobs(self):
        import runtime
        self.tick(self.spec)
        with patch.object(a.measurements, "observe", return_value={"unfinished_runs": 1, "data_gaps": 0}):
            result = self.tick(now=c.instant(c.DEADLINE))
        self.assertEqual("drain_admitted_runs", result["action"])
        self.assertTrue(result["cleanup_verified"])
        self.assertFalse(runtime.schedule(result, c.instant(c.DEADLINE))["stopped"])
        self.assertFalse(any(self.gh.values.values()))

    def test_hold_and_changed_scope_cleanup(self):
        self.tick(self.spec)
        with patch.object(a, "snapshot", side_effect=c.Gap("excluded_experiment_scope")):
            self.assertEqual("excluded_experiment_scope", self.tick()["reason"])
        self.tick(self.spec)
        self.assertEqual("owner_hold", self.tick(hold=True)["reason"])
        self.assertFalse(any(self.gh.values.values()))

    def test_kind_and_identity_limits(self):
        self.tick(self.spec)
        self.tick({"stop": True})
        self.assertEqual("case_provenance_changed", self.tick({**self.spec, "kind": "natural"})["reason"])
        for number in (13, 14):
            self.tick({**self.spec, "pr": number})
            self.tick({"stop": True})
        self.assertEqual("experiment_kind_limit", self.tick({**self.spec, "pr": 15})["reason"])

    def test_measurement_gap_cleans_selection_without_claiming_zero(self):
        with patch.object(a.measurements, "observe", side_effect=c.Gap("api_failure")):
            result = self.tick(self.spec)
        self.assertEqual("measurement_gap", result["reason"])
        self.assertEqual(1, result["history"]["data_gaps"])
        self.assertFalse(any(self.gh.values.values()))

    def test_begin_preserves_old_cases(self):
        del self.state["experiment"]
        self.state["receipt"] = {"old": True}
        with self.assertRaisesRegex(c.Gap, "old_admission_active"):
            self.tick({"begin": True})
        del self.state["receipt"]
        self.assertEqual("inactive", self.tick({"begin": True})["status"])
        self.assertEqual(["historical#1", "historical#2"], self.state["counted_prs"])



class LiveMainSourceTests(unittest.TestCase):
    def fixture(self, race=False, bad_workflow=False):
        import base64
        from test_ci_pilot import Fake
        gh = Fake()
        for side in ("head", "base"):
            gh.snap["pr"][side]["repo"]["full_name"] = a.REPO
        gh.snap["pr"]["head"]["ref"] = "business-branch"
        gh.snap["paths"] = ["scripts/measure-job-coverage-live.py"]
        gh.snap["workflow_sha256"] = c.digest("workflow")
        gh.snap["protection"]["required_status_checks"]["checks"] = [
            {"context": "Merglbot PR Assistant v6", "app_id": 3518182},
            {"context": "Unit tests", "app_id": 15368}]
        protection = c.digest(json.dumps({"protection": gh.snap["protection"], "rules": []}, sort_keys=True))
        calls = []
        def api(path):
            calls.append(path)
            if path.endswith("/pulls/2733"):
                return copy.deepcopy(gh.snap["pr"])
            if path.endswith("/git/ref/heads/main"):
                reads = sum(x.endswith("/git/ref/heads/main") for x in calls)
                return {"object": {"sha": ("e" if race and reads > 1 else "d") * 40}}
            if "?ref=" + "d" * 40 in path:
                value = "classifier" if "ci-delay-admission.py" in path else ("changed" if bad_workflow else "workflow")
                return {"content": base64.b64encode(value.encode()).decode()}
            raise AssertionError(path)
        gh.api = api
        return gh, protection, calls

    def test_lagging_pr_base_keeps_identity_and_checks_live_source(self):
        gh, protection, calls = self.fixture()
        with patch.object(a, "WORKFLOW_HASH", c.digest("workflow")), patch.object(a, "CLASSIFIER_HASH", c.digest("classifier")):
            receipt, _ = a.snapshot(gh, a.COMPATIBILITY_PR, protection)
        self.assertEqual("b" * 40, receipt["base"])
        self.assertEqual("d" * 40, receipt["source_base"])
        self.assertEqual(2, sum(x.endswith("/git/ref/heads/main") for x in calls))

    def test_source_movement_and_changed_live_workflow_refuse(self):
        for kwargs, reason in (({"race": True}, "main_source_race"),
                               ({"bad_workflow": True}, "unverified_main_workflow")):
            gh, protection, _ = self.fixture(**kwargs)
            with self.subTest(reason=reason), patch.object(a, "WORKFLOW_HASH", c.digest("workflow")), patch.object(a, "CLASSIFIER_HASH", c.digest("classifier")):
                with self.assertRaisesRegex(c.Gap, reason):
                    a.snapshot(gh, a.COMPATIBILITY_PR, protection)


if __name__ == "__main__":
    unittest.main()
