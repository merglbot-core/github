import copy
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci-pilot"))
import automatic as a
import controller as c

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

    def tick(self, receipt=None, now=NOW, **kw):
        return a.experiment_tick(self.gh, self.state, now, True, receipt,
                                 clock=lambda: now, **kw)

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

    def test_deadline_keeps_runtime_until_admitted_jobs_are_terminal(self):
        import runtime
        self.tick(self.selection)
        with patch.object(a, "observe", return_value={"unfinished_runs": 1, "data_gaps": 0}):
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
        with patch.object(a, "observe", side_effect=c.Gap("api_failure")):
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

    def test_completed_cache_requires_same_attempt_and_interval(self):
        case = {"pr": 12, "branch": "test-branch", "started_at": NOW.isoformat(),
                "initial_base": "b" * 40}
        run = {"id": 7, "run_attempt": 1, "head_sha": "a" * 40, "head_branch": "test-branch",
               "pull_requests": [{"number": 12}], "status": "completed", "updated_at": NOW.isoformat()}
        self.gh.pages = lambda *args: [copy.deepcopy(run)]
        reads = []
        def measurements(receipt, since, until):
            reads.append((receipt, since, until))
            return {"observations": [{"status": "completed", "attempt": run["run_attempt"]}],
                    "runner_evidence_gaps": 0}
        self.gh.measurements = measurements
        a.observe(self.gh, case, NOW)
        a.observe(self.gh, case, NOW)
        self.assertEqual(1, len(reads))
        run["run_attempt"] = 2
        a.observe(self.gh, case, NOW)
        self.assertEqual(2, len(reads))
        run["created_at"] = NOW.isoformat()
        case["stopped_at"] = (NOW + dt.timedelta(seconds=1)).isoformat()
        a.observe(self.gh, case, NOW)
        self.assertEqual(case["stopped_at"], reads[-1][2])
        self.assertEqual(3, len(reads))


if __name__ == "__main__":
    unittest.main()
