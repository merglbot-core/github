import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("pilot", Path(__file__).resolve().parents[1] / "scripts/ci-pilot/controller.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
NOW = c.instant("2026-09-10T20:00:00Z")


class Fake:
    def __init__(self):
        self.values = {r: {} for r in c.REPOS}
        self.writes = []
        self.fail_snapshot = False
        self.race = False
        self.fail_pr = False
        self.snap = {
            "pr": {"state": "open", "draft": False, "merged": False, "labels": [],
                   "head": {"sha": "a" * 40, "repo": {"full_name": c.REPOS[0]}},
                   "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": c.REPOS[0]}}},
            "paths": ["src/app.py"], "diff_sha256": "c" * 64,
            "workflow_sha256": "e" * 64, "selector_supported": True,
            "protection": {"required_status_checks": {"checks": [
                {"context": "Merglbot PR Assistant v6", "app_id": None},
                {"context": "unit-tests (3.11)", "app_id": 15368},
                {"context": "unit-tests (3.12)", "app_id": 15368}]}},
            "rules": [], "environment_empty": True,
            "environment": {"protection_rules": [{"type": "wait_timer", "wait_timer": 10}, {"type": "branch_policy"}],
                            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}},
            "policies": [{"name": "refs/pull/*/merge", "type": "branch"}], "custom": {"total_count": 0}}
        self.receipt = {"repo": c.REPOS[0], "pr": 123, "head": "a" * 40, "base": "b" * 40,
                        "paths": ["src/app.py"], "diff_sha256": "c" * 64, "workflow_sha256": "e" * 64,
                        "eligible": True, "assessment": "Full diff reviewed: ordinary application fix; no excluded changes.",
                        "protection_sha256": c.digest(json.dumps({"protection": self.snap["protection"], "rules": []}, sort_keys=True))}

    def selectors(self, repo):
        return self.values[repo].copy()

    def snapshot(self, receipt):
        if self.fail_snapshot:
            raise c.Gap("api_failure")
        if self.race and self.writes:
            self.snap["pr"]["head"]["sha"] = "d" * 40
        return copy.deepcopy(self.snap)

    def mutate(self, repo, name, value=None):
        self.writes.append((repo, name, value))
        if self.fail_pr and name == c.PR_VAR and value is not None:
            raise c.Gap("write_failure")
        if value is None:
            self.values[repo].pop(name, None)
        else:
            self.values[repo][name] = value

    def measurements(self, receipt, since):
        return {"runs": 1, "runner_seconds": 0, "pending_environment_observations": 0,
                "cancelled_without_runner": 1}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.gh, self.state = Fake(), {}

    def activate(self):
        return c.tick(self.gh, self.state, NOW, True, self.gh.receipt, clock=lambda: NOW)

    def assert_clean(self, result):
        self.assertEqual(result["action"], "cleanup_verified")
        self.assertFalse(any(self.gh.values.values()))

    def test_readonly_never_installs(self):
        result = c.tick(self.gh, self.state, NOW, receipt=self.gh.receipt)
        self.assertEqual(result["action"], "activation_available")
        self.assertEqual(self.gh.writes, [])

    def test_activation_order_and_restart(self):
        self.assertEqual(self.activate()["action"], "observe")
        self.assertEqual([w[1] for w in self.gh.writes], [c.SHA_VAR, c.PR_VAR])
        recovered = json.loads(json.dumps(self.state))
        self.assertEqual(c.tick(self.gh, recovered, NOW, True)["action"], "observe")
        self.assertEqual(recovered["counted_prs"], [])

    def test_head_base_closed_merge_hold_and_paths_cleanup(self):
        mutations = [lambda s: s["pr"]["head"].update(sha="d" * 40),
                     lambda s: s["pr"]["base"].update(sha="d" * 40),
                     lambda s: s["pr"].update(state="closed"),
                     lambda s: s["pr"].update(merged=True),
                     lambda s: s["pr"].update(labels=[{"name": "OWNER_HOLD"}]),
                     lambda s: s.update(paths=["infra/main.tf"]),
                     lambda s: s.update(selector_supported=False),
                     lambda s: s["pr"]["head"]["repo"].update(full_name="attacker/fork")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.setUp()
                self.activate()
                mutation(self.gh.snap)
                self.assert_clean(c.tick(self.gh, self.state, NOW, True))
                self.assertEqual([w[1] for w in self.gh.writes[-2:]], [c.PR_VAR, c.SHA_VAR])

    def test_api_error_cleans(self):
        self.activate()
        self.gh.fail_snapshot = True
        self.assert_clean(c.tick(self.gh, self.state, NOW, True))

    def test_expiry_and_no_delay(self):
        for when in (c.instant(c.DEADLINE), NOW + dt.timedelta(hours=24)):
            self.setUp()
            self.activate()
            self.assert_clean(c.tick(self.gh, self.state, when, True))

    def test_partial_activation_and_race_rollback(self):
        for fault in ("fail_pr", "race"):
            self.setUp()
            setattr(self.gh, fault, True)
            self.assert_clean(self.activate())
            self.assertEqual(len(self.state["history"]), 1)

    def test_restart_partial_and_multiple_selectors(self):
        self.activate()
        self.gh.values[c.REPOS[0]].pop(c.PR_VAR)
        self.assert_clean(c.tick(self.gh, self.state, NOW, True))
        self.setUp()
        self.activate()
        self.gh.values[c.REPOS[1]][c.SHA_VAR] = "a" * 40
        self.assert_clean(c.tick(self.gh, self.state, NOW, True))

    def test_receipt_and_case_limits(self):
        self.state["counted_prs"] = [str(i) for i in range(5)]
        self.assert_clean(self.activate())
        self.assertEqual(self.gh.writes, [])
        self.setUp()
        self.gh.receipt["eligible"] = False
        self.assert_clean(self.activate())
        self.assertEqual(self.gh.writes, [])

    def test_read_failure_still_attempts_cleanup(self):
        self.activate()
        self.gh.selectors = lambda repo: (_ for _ in ()).throw(c.Gap("read_failed"))
        result = c.tick(self.gh, self.state, NOW, True)
        self.assertEqual(result["status"], "unverified")
        self.assertFalse(any(self.gh.values.values()))
        self.assertEqual(len(self.gh.writes), 8)

    def test_pagination_uses_actual_thirty_item_cap(self):
        gh = c.GitHub()
        calls = []
        def api(path):
            calls.append(path)
            if "page=1" in path:
                return {"variables": [{"name": str(i), "value": ""} for i in range(30)], "total_count": 31}
            return {"variables": [{"name": c.PR_VAR, "value": "123"}], "total_count": 31}
        gh.api = api
        self.assertEqual(gh.selectors(c.REPOS[0]), {c.PR_VAR: "123"})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("per_page=30" in path for path in calls))

    def test_delay_counts_distinct_pr_and_preserves_old_head_observations(self):
        def measured(receipt, since):
            return {"pending_environment_observations": 1,
                    "observations": [{"run_id": 7, "status": "in_progress"}]}
        self.gh.measurements = measured
        self.activate()
        self.assertEqual(self.state["counted_prs"], [f"{c.REPOS[0]}#123"])
        self.gh.snap["pr"]["state"] = "closed"
        result = c.tick(self.gh, self.state, NOW, True)
        self.assertEqual(result["history"]["unfinished_runs"], 1)
        self.assertEqual(len(self.state["measurements"]), 1)
        self.gh.snap["pr"]["state"] = "open"
        self.activate()
        self.assertEqual(len(self.state["counted_prs"]), 1)

    def test_fifth_observed_pr_cleans_immediately(self):
        self.state["counted_prs"] = [str(i) for i in range(4)]
        self.gh.measurements = lambda *args: {"pending_environment_observations": 1, "observations": []}
        self.assert_clean(self.activate())
        self.assertEqual(len(self.state["counted_prs"]), 5)

    def test_wait_first_seen_after_close_counts_and_survives_completion(self):
        self.activate()
        self.gh.snap["pr"]["state"] = "closed"
        self.gh.measurements = lambda *args: {"pending_environment_observations": 1,
                                             "observations": [{"run_id": 7, "status": "waiting"}]}
        self.assert_clean(c.tick(self.gh, self.state, NOW, True))
        self.assertEqual(len(self.state["counted_prs"]), 1)
        self.gh.measurements = lambda *args: {"pending_environment_observations": 0,
                                             "observations": [{"run_id": 7, "status": "completed"}]}
        c.tick(self.gh, self.state, NOW, True)
        evidence = next(iter(self.state["measurements"].values()))
        self.assertTrue(evidence["ever_observed_delay"])
        self.assertEqual(len(evidence["snapshots"]), 2)

    def test_deadline_and_hold_between_selector_writes(self):
        times = iter([NOW, c.instant(c.DEADLINE)])
        self.assert_clean(c.tick(self.gh, self.state, NOW, True, self.gh.receipt, clock=lambda: next(times)))
        self.assertFalse(any(w[1] == c.PR_VAR and w[2] for w in self.gh.writes))
        self.setUp()
        holds = iter([False, True])
        self.assert_clean(c.tick(self.gh, self.state, NOW, True, self.gh.receipt,
                                 clock=lambda: NOW, hold_check=lambda: next(holds)))

    def test_run_must_bind_exact_pr_and_base(self):
        gh = c.GitHub()
        run = {"created_at": NOW.isoformat(), "path": c.WORKFLOWS[c.REPOS[0]],
               "event": "pull_request", "pull_requests": [{"number": 999}]}
        gh.pages = lambda *args: [run]
        self.assertEqual(gh.measurements(self.gh.receipt, NOW.isoformat())["runs"], 0)
        run["pull_requests"] = []
        with self.assertRaises(c.Gap):
            gh.measurements(self.gh.receipt, NOW.isoformat())
        run["pull_requests"] = [{"number": 123, "head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}}]
        with self.assertRaises(c.Gap):
            gh.measurements(self.gh.receipt, NOW.isoformat())


if __name__ == "__main__":
    unittest.main()
