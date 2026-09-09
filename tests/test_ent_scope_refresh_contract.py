"""Contract: the weekly scope refresh owns ONLY the dependabot scope mirror.

scripts/pr-assistant/target-repos.txt is a compatibility artifact rendered
from repo-policy-manifest.json (``repo-policy-manifest.py sync-target-repos
--write``) and enforced by the verify-manifest CI gate — pr-assistant
enrollment is a curated policy decision, not a raw org scan. The 2026-07
regression: ent-scope-refresh.yml regenerated the file from the org scan,
so every weekly scope PR failed the manifest gate and the deploy-v3 /
improvement-digest pre-flights aborted. These tests pin the ownership
boundary so the regeneration cannot silently come back.

unittest.TestCase style on purpose: the CI runner uses
``python3 -m unittest discover``, which collects only TestCase classes.
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ent-scope-refresh.yml"

# The regeneration write site for the file the workflow DOES own. Anchoring on
# BOTH the path construction and the write call (not just the path, which also
# appears in git add/diff lines) keeps the reverse assertion non-vacuous:
# deleting either half of the regen step removes an anchor even if
# bookkeeping lines linger.
SCOPE_MIRROR_PATH_ANCHOR = "pathlib.Path('scripts/dependabot/ent_repository_scope.txt')"
SCOPE_MIRROR_WRITE_CALL_ANCHOR = "path.write_text("


def _non_comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


class EntScopeRefreshOwnershipContract(unittest.TestCase):
    def setUp(self) -> None:
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_never_touches_target_repos_txt(self) -> None:
        # The manifest-rendered artifact may appear only in explanatory
        # comments. Any executable reference (regen block, git add,
        # git diff path, PR body) is a boundary violation.
        offending = [
            line
            for line in _non_comment_lines(self.text)
            if "target-repos.txt" in line
        ]
        self.assertEqual(
            offending,
            [],
            "ent-scope-refresh.yml must not touch the manifest-rendered "
            f"target-repos.txt outside comments: {offending}",
        )

    def test_still_regenerates_dependabot_scope_mirror(self) -> None:
        # Guard the other direction non-vacuously: BOTH halves of the
        # regeneration write site (the target path construction AND the
        # write call) must exist as executable workflow content — deleting
        # the regen step, or just its write, would otherwise make the
        # boundary test pass while silently killing the weekly refresh.
        lines = _non_comment_lines(self.text)
        for anchor in (SCOPE_MIRROR_PATH_ANCHOR, SCOPE_MIRROR_WRITE_CALL_ANCHOR):
            self.assertTrue(
                [line for line in lines if anchor in line],
                "ent-scope-refresh.yml no longer contains the scope-mirror "
                f"regeneration anchor {anchor!r}",
            )


if __name__ == "__main__":
    unittest.main()
