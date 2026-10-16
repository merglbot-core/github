"""Guard: no workflow sends or references Slack (owner decision 16 Oct 2026, infra#3841).

The secrets SLACK_WEBHOOK_URL_FINAL_DATA_ALERTS and SLACK_DEPENDABOT_WEBHOOK_URL are
being deleted, so no workflow step may reference Slack at all. The only tolerated
occurrence is a YAML comment line that records the removal.
"""

import re
import unittest
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
REMOVAL_COMMENT = re.compile(r"^\s*#.*\b(removed|retired)\b", re.IGNORECASE)


class NoSlackInWorkflowsTests(unittest.TestCase):
    def test_workflows_directory_exists(self):
        self.assertTrue(WORKFLOWS_DIR.is_dir(), WORKFLOWS_DIR)
        self.assertTrue(list(WORKFLOWS_DIR.glob("*.yml")), "no workflow files found")

    def test_no_workflow_mentions_slack_outside_a_removal_comment(self):
        offenders = []
        for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "slack" not in line.lower():
                    continue
                if REMOVAL_COMMENT.match(line):
                    continue
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
        self.assertEqual(offenders, [], "Slack references left in workflows:\n" + "\n".join(offenders))

    def test_no_workflow_requires_a_slack_secret(self):
        pattern = re.compile(r"secrets\.SLACK_", re.IGNORECASE)
        offenders = [
            f"{path.name}:{lineno}"
            for path in sorted(WORKFLOWS_DIR.glob("*.y*ml"))
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
