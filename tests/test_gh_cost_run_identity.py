"""Regression cases for falsely complete workflow-success denominators."""

import copy
import importlib.util
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "tools/gh-cost-cps-identity/run_identity.py"
SPEC = importlib.util.spec_from_file_location("gh_cost_run_identity", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
validate = MODULE.snapshot_identity_error


class RunIdentityTest(unittest.TestCase):
    def setUp(self):
        self.repo = "example-org/example-repo"
        self.snapshot = {"repo": self.repo, "runs": [
            {"id": 101, "status": "completed", "conclusion": "success"},
            {"id": 102, "status": "completed", "conclusion": "success"},
        ]}

    def test_distinct_successes_are_valid_and_input_is_unchanged(self):
        original = copy.deepcopy(self.snapshot)
        self.assertIsNone(validate(self.snapshot, self.repo))
        self.assertEqual(self.snapshot, original)

    def test_empty_run_census_is_valid_identity_not_a_success_metric(self):
        self.assertIsNone(validate({"repo": self.repo, "runs": []}, self.repo))

    def test_duplicated_success_cannot_reduce_cost_per_success(self):
        self.snapshot["runs"].append(copy.deepcopy(self.snapshot["runs"][0]))
        self.assertEqual(validate(self.snapshot, self.repo), "RUN_ID_DUPLICATE")

    def test_rerun_attempt_is_not_another_created_workflow_run(self):
        self.snapshot["runs"].append(dict(self.snapshot["runs"][0], run_attempt=2))
        self.assertEqual(validate(self.snapshot, self.repo), "RUN_ID_DUPLICATE")

    def test_file_contents_must_match_the_inventory_repository(self):
        self.snapshot["repo"] = "different-org/different-repo"
        self.assertEqual(validate(self.snapshot, self.repo), "RUN_REPOSITORY_IDENTITY_MISMATCH")

    def test_missing_repository_is_not_complete_evidence(self):
        del self.snapshot["repo"]
        self.assertEqual(validate(self.snapshot, self.repo), "RUN_REPOSITORY_IDENTITY_MISMATCH")

    def test_run_id_is_a_positive_integer_not_bool_or_numeric_string(self):
        for invalid in (True, False, None, 0, -1, 1.0, "101", [], {}):
            with self.subTest(value=invalid):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["runs"][0]["id"] = invalid
                self.assertEqual(validate(snapshot, self.repo), "RUN_ID_INVALID")

    def test_non_record_run_is_rejected(self):
        self.snapshot["runs"].append(None)
        self.assertEqual(validate(self.snapshot, self.repo), "RUN_ID_INVALID")

    def test_malformed_snapshot_or_runs_is_not_an_empty_census(self):
        for malformed in (None, [], {"repo": self.repo}, {"repo": self.repo, "runs": {}}):
            with self.subTest(snapshot=malformed):
                self.assertEqual(validate(malformed, self.repo), "RUN_RECORDS_INVALID")


if __name__ == "__main__":
    unittest.main()
