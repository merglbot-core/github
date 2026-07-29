"""Contract: the weekly scope refresh owns ONLY the dependabot scope mirror.

scripts/pr-assistant/target-repos.txt is a compatibility artifact rendered
from repo-policy-manifest.json (``repo-policy-manifest.py sync-target-repos
--write``) and enforced by the verify-manifest CI gate — pr-assistant
enrollment is a curated policy decision, not a raw org scan. The 2026-07
regression: ent-scope-refresh.yml regenerated the file from the org scan,
so every weekly scope PR failed the manifest gate and the deploy-v3 /
improvement-digest pre-flights aborted. These tests pin the ownership
boundary so the regeneration cannot silently come back.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ent-scope-refresh.yml"


def _non_comment_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_scope_refresh_never_touches_target_repos_txt() -> None:
    # The manifest-rendered artifact may appear only in explanatory comments.
    # Any executable reference (regen block, git add, git diff path) is a
    # boundary violation and must fail this contract.
    offending = [
        line
        for line in _non_comment_lines(WORKFLOW.read_text(encoding="utf-8"))
        if "target-repos.txt" in line
    ]
    assert offending == [], (
        "ent-scope-refresh.yml must not touch the manifest-rendered "
        f"target-repos.txt outside comments: {offending}"
    )


def test_scope_refresh_still_owns_dependabot_scope_mirror() -> None:
    # Guard the other direction: the workflow keeps regenerating the
    # dependabot scope mirror (deleting the whole regen step would also
    # make the first test pass vacuously).
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/dependabot/ent_repository_scope.txt" in text
