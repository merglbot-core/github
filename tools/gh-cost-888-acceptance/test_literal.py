import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("literal", pathlib.Path(__file__).parent / "literal.py")
literal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(literal)
SINCE = "2026-09-23T11:00:00Z"


class TerraformAcceptance(unittest.TestCase):
    def test_path_filter_is_direct_pr_trigger_config(self):
        source = "on:\n  pull_request:\n    paths:\n      - 'terraform/**'\n  workflow_dispatch:\n"
        self.assertTrue(literal.terraform_paths_filtered(source))
        self.assertFalse(literal.terraform_paths_filtered("# paths: terraform/**\non:\n  pull_request:\n"))
        self.assertFalse(literal.terraform_paths_filtered("on:\n  push:\n    paths:\n      - 'terraform/**'\n"))

    def test_only_successful_first_attempts_in_exact_workflow(self):
        rows = [{"id": i, "event": "pull_request", "created_at": "2026-09-24T00:00:00Z",
                 "run_attempt": 1, "conclusion": "success"} for i in range(5)]
        rows.append({"id": 8, "event": "pull_request", "created_at": "2026-09-24T00:00:00Z",
                     "run_attempt": 2, "conclusion": "success"})
        rows.append({"id": 9, "event": "pull_request", "created_at": "2026-09-24T00:00:00Z",
                     "run_attempt": 1, "conclusion": "cancelled"})
        self.assertEqual(literal.qualifying_terraform_runs({"total_count": len(rows), "workflow_runs": rows}, SINCE), 5)

    def test_incomplete_or_duplicate_pages_are_unknown(self):
        row = {"id": 1, "event": "pull_request", "created_at": "2026-09-24T00:00:00Z",
               "run_attempt": 1, "conclusion": "success"}
        self.assertIsNone(literal.qualifying_terraform_runs({"total_count": 2, "workflow_runs": [row]}, SINCE))
        self.assertIsNone(literal.qualifying_terraform_runs({"total_count": 2, "workflow_runs": [row, row]}, SINCE))


if __name__ == "__main__":
    unittest.main()
