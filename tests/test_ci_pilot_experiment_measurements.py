import copy
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci-pilot"))
import experiment_measurements as a

NOW = a.instant("2026-09-10T20:00:00Z")


class FakeGitHub:
    def pages(self, *args):
        return []


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()

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
        case["started_at"] = (NOW - dt.timedelta(seconds=1)).isoformat()
        a.observe(self.gh, case, NOW)
        self.assertEqual(4, len(reads))
        self.assertEqual(case["started_at"], reads[-1][1])
        del case["measurements"]["a" * 40]["since"]
        a.observe(self.gh, case, NOW)
        self.assertEqual(5, len(reads))
        a.observe(self.gh, case, NOW)
        self.assertEqual(5, len(reads))

    def test_previous_phase_run_rerun_is_discovered_in_current_phase(self):
        case = {"pr": 12, "branch": "test-branch", "started_at": NOW.isoformat(),
                "initial_base": "b" * 40}
        run = {"id": 9, "run_attempt": 2, "head_sha": "a" * 40,
               "head_branch": "test-branch", "pull_requests": [{"number": 12}],
               "created_at": (NOW - dt.timedelta(hours=1)).isoformat(),
               "run_started_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
               "status": "in_progress"}
        # Reproduce GitHub's original-creation filter, which hides a later rerun.
        self.gh.pages = lambda query, *args: [] if "&created=" in query else [run]
        measurements = []
        def measure(receipt, since, until):
            measurements.append((receipt["head"], since, until))
            return {"observations": [{"run_id": 9, "attempt": 2, "status": "in_progress"}],
                    "runner_evidence_gaps": 0}
        self.gh.measurements = measure
        result = a.observe(self.gh, case, NOW)
        self.assertEqual(1, result["unfinished_runs"])
        self.assertEqual([("a" * 40, NOW.isoformat(), None)], measurements)
        self.assertEqual(2, case["measurements"]["a" * 40]["latest"]["observations"][0]["attempt"])


if __name__ == "__main__":
    unittest.main()
