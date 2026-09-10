import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
import sys
import tempfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("pilot", Path(__file__).resolve().parents[1] / "scripts/ci-pilot/controller.py")
c = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci-pilot"))
spec.loader.exec_module(c)
sys.path.pop(0)
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

    def infra_fixture(self):
        repo = c.REPOS[1]
        self.gh.receipt["repo"] = repo
        for side in ("head", "base"):
            self.gh.snap["pr"][side]["repo"]["full_name"] = repo
        self.gh.snap["protection"]["required_status_checks"]["checks"] = [
            {"context": "Merglbot PR Assistant v6", "app_id": None},
            {"context": "Unit tests", "app_id": 15368}]
        self.gh.receipt["protection_sha256"] = c.digest(json.dumps(
            {"protection": self.gh.snap["protection"], "rules": []}, sort_keys=True))
        return repo

    def test_base_bound_admission_and_changed_base_cleanup(self):
        repo = self.infra_fixture()
        self.assertEqual(self.activate()["action"], "observe")
        self.assertEqual([w[1] for w in self.gh.writes], [c.BASE_VAR, c.SHA_VAR, c.PR_VAR])
        self.assertEqual(self.gh.values[repo][c.BASE_VAR], self.gh.receipt["base"])
        self.assertEqual(c.tick(self.gh, self.state, NOW, True)["action"], "observe")
        self.gh.snap["pr"]["base"]["sha"] = "d" * 40
        self.assert_clean(c.tick(self.gh, self.state, NOW, True))
        self.assertEqual([w[1] for w in self.gh.writes[-3:]], [c.PR_VAR, c.SHA_VAR, c.BASE_VAR])

    def test_partial_base_selector_restart_cleans_all_repos(self):
        for repo in c.REPOS:
            self.gh.values[repo] = {c.BASE_VAR: "b" * 40}
        self.assert_clean(c.tick(self.gh, {}, NOW, True))
        self.assertEqual(len(self.gh.writes), len(c.REPOS))

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
        self.assertEqual([w for w in self.gh.writes if w[2] is None],
                         [(repo, name, None) for repo in c.REPOS
                          for name in c.SELECTOR_NAMES])

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

    def test_active_tick_rechecks_deadline_and_hold_after_observations(self):
        for boundary in ("deadline", "hold"):
            self.setUp()
            self.activate()
            changed = []
            def measurements(*args):
                changed.append(True)
                return {"pending_environment_observations": 0}
            self.gh.measurements = measurements
            result = c.tick(self.gh, self.state, NOW, True,
                            clock=lambda: c.instant(c.DEADLINE) if changed and boundary == "deadline" else NOW,
                            hold_check=lambda: bool(changed) and boundary == "hold")
            self.assert_clean(result)
            self.assertEqual(result["reason"], "deadline" if boundary == "deadline" else "owner_hold")

    def change_head(self, when):
        self.gh.snap["pr"]["head"]["sha"] = "d" * 40
        self.assert_clean(c.tick(self.gh, self.state, when, True, clock=lambda: when))
        self.gh.receipt["head"] = "d" * 40

    def test_pr_window_survives_head_change_restart_and_keeps_metrics_start(self):
        self.activate()
        later = NOW + dt.timedelta(hours=23)
        self.change_head(later)
        self.state = json.loads(json.dumps(self.state))
        seen = []
        original = self.gh.measurements
        self.gh.measurements = lambda r, since: (seen.append((r["head"], since)) or original(r, since))
        self.assertEqual(c.tick(self.gh, self.state, later, True, self.gh.receipt,
                                clock=lambda: later)["action"], "observe")
        self.assertEqual(self.state["started_at"], later.isoformat())
        self.assertIn(("d" * 40, later.isoformat()), seen)
        result = c.tick(self.gh, self.state, NOW + dt.timedelta(hours=24), True,
                        clock=lambda: NOW + dt.timedelta(hours=24))
        self.assert_clean(result)
        self.assertEqual(result["reason"], "no_delay_24h")

    def test_new_head_after_pr_expiry_never_writes_selectors(self):
        self.activate()
        self.change_head(NOW + dt.timedelta(hours=23))
        self.gh.writes.clear()
        later = NOW + dt.timedelta(hours=25)
        result = c.tick(self.gh, self.state, later, True, self.gh.receipt, clock=lambda: later)
        self.assert_clean(result)
        self.assertEqual(result["reason"], "no_delay_24h")
        self.assertEqual(self.gh.writes, [])

    def test_legacy_earliest_admission_migration_and_counted_exemption(self):
        self.activate()
        self.change_head(NOW + dt.timedelta(hours=1))
        self.state.pop("pr_windows")
        self.state["counted_prs"] = [f"{c.REPOS[0]}#123"]
        later = NOW + dt.timedelta(hours=25)
        self.assertEqual(c.tick(self.gh, self.state, later, True, self.gh.receipt,
                                clock=lambda: later)["action"], "observe")
        window = self.state["pr_windows"][f"{c.REPOS[0]}#123"]
        self.assertEqual(window["first_selected_at"], NOW.isoformat())
        self.assertTrue(window["saw_delay"])

    def test_invalid_pr_window_repeated_cleanup_preserves_state(self):
        self.activate()
        for invalid in (None, *({f"{c.REPOS[0]}#123": {"first_selected_at": value, "saw_delay": False}}
                                for value in ("bad", "2026-09-10T20:00:00", (NOW + dt.timedelta(days=1)).isoformat()))):
            self.state["pr_windows"] = invalid
            before = copy.deepcopy(self.state)
            for _ in range(2):
                result = c.tick(self.gh, self.state, NOW, True)
                self.assertEqual(result["action"], "recovery_required")
                self.assertEqual(result["status"], "unverified")
                self.assertEqual(self.state, before)
                self.assertFalse(any(self.gh.values.values()))

    def test_corrupt_state_repeatedly_requires_recovery_without_reset(self):
        for original in ("{broken", '{"counted_prs": null, "history": []}'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / "state.json").write_text(original)
                argv = ["controller", "tick", "--state-dir", directory, "--apply"]
                with patch.object(sys, "argv", argv), patch.object(c, "GitHub", return_value=self.gh), patch("builtins.print"):
                    for _ in range(2):
                        self.assertEqual(c.main(), 1)
                        result = json.loads((path / "next_action.json").read_text())
                        self.assertEqual(result["action"], "recovery_required")
                        self.assertTrue(result["cleanup_verified"])
                        self.assertEqual((path / "state.json").read_text(), original)



if __name__ == "__main__":
    unittest.main()
