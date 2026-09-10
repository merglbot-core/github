import re
import unittest
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "forecast-d1-readiness.yml"
)


def workflow_source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


class ForecastD1ScheduleDecommissionTest(unittest.TestCase):
    def test_forecast_d1_workflow_is_manual_only(self):
        source = workflow_source()
        trigger_block = source.split("permissions:", maxsplit=1)[0]

        self.assertRegex(trigger_block, r"(?m)^  workflow_dispatch:$")
        self.assertNotRegex(trigger_block, r"(?m)^  schedule:$")
        self.assertNotRegex(trigger_block, r"(?m)^\s*-?\s*cron:")

    def test_manual_dispatch_keeps_safe_dry_run_default(self):
        source = workflow_source()
        trigger_block = source.split("permissions:", maxsplit=1)[0]

        dry_run = re.search(
            r"(?ms)^      dry_run:\n(?P<body>.*?)(?=^      [a-zA-Z0-9_]+:|^permissions:)",
            trigger_block,
        )
        self.assertIsNotNone(dry_run)
        assert dry_run is not None
        self.assertRegex(dry_run.group("body"), r"(?m)^        type: boolean$")
        self.assertRegex(dry_run.group("body"), r"(?m)^        default: true$")

    def test_workflow_permissions_remain_least_privilege(self):
        source = workflow_source()
        permissions = re.search(
            r"(?ms)^permissions:\n(?P<body>.*?)(?=^[a-zA-Z0-9_-]+:)", source
        )

        self.assertIsNotNone(permissions)
        assert permissions is not None
        entries = {
            key: value
            for key, value in re.findall(
                r"(?m)^  ([a-zA-Z0-9_-]+):\s*([^#\n]+?)\s*(?:#.*)?$",
                permissions.group("body"),
            )
        }
        self.assertEqual(entries, {"contents": "read", "id-token": "write"})
