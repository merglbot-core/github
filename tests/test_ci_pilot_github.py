import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("pilot_api", Path(__file__).resolve().parents[1] / "scripts/ci-pilot/github_client.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
NOW = c.instant("2026-09-10T20:00:00Z")
RECEIPT = {"repo": c.REPOS[0], "pr": 123, "head": "a" * 40, "base": "b" * 40}


class GitHubTests(unittest.TestCase):
    def test_mutation_is_scoped_and_guarded(self):
        gh = c.GitHub()
        gh.command = MagicMock()
        gh.mutate(c.REPOS[0], c.SHA_VAR, "a" * 40)
        args, options = gh.command.call_args.args[0], gh.command.call_args.kwargs
        self.assertEqual(args[:4], ["/bin/bash", str(c.GUARD), "gh", "variable"])
        self.assertIn("set", args)
        self.assertEqual(options["env"]["CODEX_GUARD_ALLOW_GITHUB_MUTATION"], "1")
        gh.mutate(c.REPOS[0], c.PR_VAR)
        self.assertIn("delete", gh.command.call_args.args[0])
        for repo, name in (("other/repo", c.PR_VAR), (c.REPOS[0], "UNRELATED_VARIABLE")):
            with self.assertRaises(c.Gap):
                gh.mutate(repo, name)
        self.assertEqual(gh.command.call_count, 2)

    def test_api_error_is_a_gap_without_raw_output(self):
        failed = subprocess.CompletedProcess([], 1, "private stdout", "private stderr")
        with patch.object(c.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(c.Gap, "^github_command_failed$"):
                c.GitHub().api("repos/example/example")
        invalid = subprocess.CompletedProcess([], 0, "not JSON", "")
        with patch.object(c.subprocess, "run", return_value=invalid):
            with self.assertRaisesRegex(c.Gap, "^invalid_api_json$"):
                c.GitHub().api("repos/example/example")

    def test_partial_pagination_and_file_list_are_gaps(self):
        gh = c.GitHub()
        gh.api = lambda *args: {"variables": [], "total_count": 1}
        with self.assertRaisesRegex(c.Gap, "incomplete_pagination"):
            gh.selectors(c.REPOS[0])
        gh.api = lambda *args: {"changed_files": 2}
        gh.pages = lambda *args: [{"filename": "one.py"}]
        with self.assertRaisesRegex(c.Gap, "incomplete_files"):
            gh.snapshot(RECEIPT)

    def test_wait_timer_and_runner_zero_evidence(self):
        gh = c.GitHub()
        run = {"id": 7, "run_attempt": 1, "created_at": NOW.isoformat(), "head_sha": RECEIPT["head"],
               "path": c.WORKFLOWS[c.REPOS[0]], "event": "pull_request", "status": "completed",
               "pull_requests": [{"number": 123, "head": {"sha": RECEIPT["head"]}, "base": {"sha": RECEIPT["base"]}}]}
        job = {"id": 9, "name": "unit-tests", "runner_id": 0, "steps": [], "conclusion": "cancelled",
               "started_at": None, "completed_at": None}
        gh.pages = lambda path, *args: [job] if "/jobs?" in path else [run]
        gh.api = lambda *args: [{"environment": {"name": c.ENVIRONMENT}, "wait_timer": 10,
                                 "wait_timer_started_at": NOW.isoformat()}]
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual(metrics["cancelled_without_runner"], 1)
        self.assertEqual(metrics["runner_seconds"], 0)
        observation = metrics["observations"][0]
        self.assertEqual(observation["wait_timers"][0]["started_at"], NOW.isoformat())
        self.assertEqual(observation["wait_timers"][0]["wait_timer"], 10)
        self.assertEqual(observation["jobs"][0]["steps_count"], 0)
        self.assertEqual(observation["jobs"][0]["name"], "unit-tests")

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

    def test_run_must_bind_exact_pr_and_base(self):
        gh = c.GitHub()
        run = {"created_at": NOW.isoformat(), "path": c.WORKFLOWS[c.REPOS[0]],
               "event": "pull_request", "pull_requests": [{"number": 999}]}
        gh.pages = lambda *args: [run]
        self.assertEqual(gh.measurements(RECEIPT, NOW.isoformat())["runs"], 0)
        run["pull_requests"] = []
        with self.assertRaises(c.Gap):
            gh.measurements(RECEIPT, NOW.isoformat())
        run["pull_requests"] = [{"number": 123, "head": {"sha": "a" * 40}, "base": {"sha": "d" * 40}}]
        with self.assertRaises(c.Gap):
            gh.measurements(RECEIPT, NOW.isoformat())


if __name__ == "__main__":
    unittest.main()
