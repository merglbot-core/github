import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("cost_semantic", HERE / "semantic.py")
semantic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(semantic)


class Hub(unittest.TestCase):
    def test_actual_main_workflow(self):
        source = (HERE.parent.parent / ".github" / "workflows" / "pr-gate.yml").read_text()
        self.assertTrue(semantic.hub_slim_configured(source))
        conflicting = source.replace("        default: 'ubuntu-slim'", "        default: 'ubuntu-slim'\n        default: 'ubuntu-24.04'", 1)
        self.assertFalse(semantic.hub_slim_configured(conflicting))
        self.assertFalse(semantic.hub_slim_configured(source.replace("default: 'ubuntu-slim'", "default: 'ubuntu-24.04'")))
        self.assertFalse(semantic.hub_slim_configured(source.replace("    runs-on: " + semantic.SLIM_RUNNER,
                                                                "    runs-on: ubuntu-24.04")))
        self.assertFalse(semantic.hub_slim_configured(source.replace("    timeout-minutes: " + semantic.SLIM_TIMEOUT,
                                                                "    timeout-minutes: 360")))
        self.assertFalse(semantic.hub_slim_configured("# default: ubuntu-slim\n# runs-on: " + semantic.SLIM_RUNNER))
        spoofed = source.replace("        default: 'ubuntu-slim'", "        default: 'ubuntu-24.04'")
        spoofed = spoofed.replace("        description: >-", "        description: >-\n          default: ubuntu-slim", 1)
        self.assertFalse(semantic.hub_slim_configured(spoofed))

    def test_duplicate_mapping_is_not_a_pass(self):
        source = (HERE.parent.parent / ".github" / "workflows" / "pr-gate.yml").read_text()
        self.assertFalse(semantic.hub_slim_configured(source + "\non:\n  workflow_call:\n"))


class PushRun(unittest.TestCase):
    def test_delayed_old_parent_is_excluded_but_new_head_is_counted(self):
        since = "2026-09-23T21:05:07Z"
        old = {"id": 1, "event": "push", "created_at": "2026-09-23T21:14:29Z", "head_sha": "old"}
        new = {"id": 2, "event": "push", "created_at": "2026-09-24T09:00:00Z", "head_sha": "new"}
        got = semantic.push_run_counts({"total_count": 2, "workflow_runs": [old, new]}, since, {"old"})
        self.assertEqual(got["post_merge_pushes"], 1)
        self.assertEqual(got["excluded_premerge_run_ids"], [1])

    def test_incomplete_page_or_unknown_sha_cannot_prove_zero(self):
        row = {"id": 3, "event": "push", "created_at": "2026-09-24T09:00:00Z"}
        self.assertIsNone(semantic.push_run_counts({"total_count": 2, "workflow_runs": [row]}, "2026-09-23", set()))
        self.assertEqual(semantic.push_run_counts({"total_count": 1, "workflow_runs": [row]},
                                                  "2026-09-23", set())["post_merge_pushes"], 1)
        self.assertIsNone(semantic.push_run_counts({"total_count": 2, "workflow_runs": [row, row]},
                                                    "2026-09-23", set()))
        self.assertIsNone(semantic.push_run_counts({"total_count": 1, "workflow_runs": [{"id": 4, "event": "push"}]},
                                                    "2026-09-23", set()))
        self.assertIsNone(semantic.push_run_counts({"total_count": 1, "workflow_runs": [{"id": 4, "created_at": "2026-09-24"}]},
                                                    "2026-09-23", set()))

    def test_current_main_trigger_contract(self):
        source = "on:\n  pull_request:\n    branches: [main]\n  schedule:\n    - cron: '15 3 * * 1'\n"
        self.assertTrue(semantic.main_trigger_contract(source))
        self.assertTrue(semantic.main_trigger_contract(source, require_schedule=True))
        self.assertFalse(semantic.main_trigger_contract(source + "on:\n  push:\n"))
        self.assertFalse(semantic.main_trigger_contract("on:\n  push:\n  pull_request:\n"))
        self.assertFalse(semantic.main_trigger_contract("on:\n  pull_request:\n  push: {branches: [main]}\n"))
        self.assertFalse(semantic.main_trigger_contract("on:\n  pull_request:\n  'push':\n"))
        self.assertTrue(semantic.main_trigger_contract("on:\n  schedule:\n    - cron: '30 2 * * 1'\n",
                                                       require_schedule=True))


if __name__ == "__main__":
    unittest.main()
