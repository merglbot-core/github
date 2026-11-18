"""Run the digest entry point with paginated, offline gh responses."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


GENERATOR = Path(__file__).resolve().parents[1] / "scripts/pr-assistant/generate-improvement-digest.sh"
COMMENT = {"id": 12, "body": "Merglbot PR Assistant\nVerdict: APPROVE\nMERGLBOT-TEST-123", "issue_url": "https://example.test/issues/7",
           "created_at": "2026-01-01T00:00:00Z", "html_url": "https://example.test/issues/7#comment-12"}
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
assert args[:4] == ["api", "--method", "GET", "--paginate"], args
responses = json.loads(Path(os.environ["RESPONSES"]).read_text())
data, code = responses[args[4]]
sys.stdout.write(data)
sys.exit(code)
"""


class DigestReadsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        tools = self.root / "scripts/pr-assistant"
        tools.mkdir(parents=True)
        self.generator = tools / GENERATOR.name
        shutil.copyfile(GENERATOR, self.generator)
        # A fixed, single-repo manifest isolates this test from estate churn.
        (tools / "target-repos.txt").write_text("demo/app\n")
        (tools / "repo-policy-manifest.json").write_text("{}")
        (tools / "repo-policy-manifest.py").write_text("pass\n")
        gh = self.root / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.output = self.root / "report.md"
        self.responses = self.root / "responses.json"

    def generate(self, comments, reactions=("", 1)):
        self.responses.write_text(json.dumps({"repos/demo/app/issues/comments": comments,
                                              "repos/demo/app/issues/comments/12/reactions": reactions}))
        env = {"PATH": f"{self.root}:{os.environ['PATH']}", "HOME": str(self.root),
               "RESPONSES": str(self.responses), "LC_ALL": "C"}
        return subprocess.run(["bash", str(self.generator), "--output", str(self.output)],
                              env=env, capture_output=True, text=True, timeout=30)

    def test_successful_empty_comments(self):
        result = self.generate(("[]", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| demo/app | 0 | 0 | 0 |", self.output.read_text())

    def test_second_page_comments_and_reactions(self):
        pages = ('[]\n' + json.dumps([COMMENT]), 0)
        result = self.generate(pages, ('[{"content":"+1"}]\n[{"content":"-1"}]', 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.output.read_text()
        self.assertIn("| demo/app | 1 | 1 | 1 | 1 | 0 | 0 |", report)
        self.assertIn("1\tMERGLBOT-TEST-123", report)

    def test_empty_reactions_are_valid(self):
        result = self.generate((json.dumps([COMMENT]), 0), ("[]", 0))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("| demo/app | 1 | 0 | 0 | 1 | 0 | 0 |", self.output.read_text())

    def test_cannot_publish_partial_or_malformed_responses(self):
        valid_comments = (json.dumps([COMMENT]), 0)
        invalid = [("", 0), ("[]", 1), ("[]\n{}", 0), ("[]\n{", 0), ("[null]", 0)]
        for kind in ("comments", "reactions"):
            for reply in invalid + [('[{"other":1}]', 0)]:
                with self.subTest(kind=kind, reply=reply):
                    self.output.write_text("previous report\n")
                    result = self.generate(reply) if kind == "comments" else self.generate(valid_comments, reply)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Incomplete {kind} read", result.stderr)
                    self.assertEqual(self.output.read_text(), "previous report\n")


if __name__ == "__main__":
    unittest.main()
