"""Guard: no workflow sends or references Slack (owner decision 16 Oct 2026, infra#3841).

The secrets SLACK_WEBHOOK_URL_FINAL_DATA_ALERTS and SLACK_DEPENDABOT_WEBHOOK_URL are
being deleted, so no workflow step may reference Slack at all. The only tolerated
occurrence is a full-line comment inside a comment block that records the removal
(the block says "removed" or "retired"; a multi-line note such as the one in
cost-monitoring.yml from #1007 counts as one block).
"""

import re
import unittest
from pathlib import Path

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
REMOVAL_WORDING = re.compile(r"\b(removed|retired)\b", re.IGNORECASE)


def _is_full_line_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _comment_block(lines, index):
    """Return the run of consecutive full-line comments that contains lines[index]."""
    start = index
    while start > 0 and _is_full_line_comment(lines[start - 1]):
        start -= 1
    end = index
    while end + 1 < len(lines) and _is_full_line_comment(lines[end + 1]):
        end += 1
    return lines[start : end + 1]


def slack_offenders(text: str, name: str = "<text>"):
    """Lines mentioning Slack outside a comment block that records the removal."""
    lines = text.splitlines()
    offenders = []
    for index, line in enumerate(lines):
        if "slack" not in line.lower():
            continue
        if _is_full_line_comment(line) and REMOVAL_WORDING.search("\n".join(_comment_block(lines, index))):
            continue
        offenders.append(f"{name}:{index + 1}: {line.strip()}")
    return offenders


class SlackOffendersHelperTests(unittest.TestCase):
    """Positive controls: the helper must flag what the guard is meant to catch."""

    def test_flags_steps_env_and_unrelated_comments(self):
        text = "\n".join(
            [
                "      - name: Notify Slack",
                "          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}",
                "      # Post the verdict to Slack",
                "      run: echo ok  # slack fallback",
            ]
        )
        self.assertEqual(len(slack_offenders(text)), 4)

    def test_accepts_removal_comment_blocks_only(self):
        text = "\n".join(
            [
                "          # Slack delivery retired 2026-10 (merglbot-core/infra#3826): without",
                "          # SLACK_WEBHOOK_URL both notifiers skip Slack; results stay in the",
                "          # step summary and the uploaded report artifact.",
                "          AI_USAGE_DATASET: \"ai_usage\"",
                "      # Slack alerts were removed (owner decision 16 Oct 2026):",
            ]
        )
        self.assertEqual(slack_offenders(text), [])


class NoSlackInWorkflowsTests(unittest.TestCase):
    def test_workflows_directory_exists(self):
        self.assertTrue(WORKFLOWS_DIR.is_dir(), WORKFLOWS_DIR)
        self.assertTrue(list(WORKFLOWS_DIR.glob("*.yml")), "no workflow files found")

    def test_no_workflow_mentions_slack_outside_a_removal_comment(self):
        offenders = []
        for path in sorted(WORKFLOWS_DIR.glob("*.y*ml")):
            offenders.extend(slack_offenders(path.read_text(encoding="utf-8"), path.name))
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
