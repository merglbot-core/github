"""Publish the two generated Markdown files and reconcile their same-repo PR."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def git(*args, **options):
    return subprocess.check_output(["git", *args], **options).decode().strip()


def remote_tip(branch):
    ref = "refs/heads/" + branch
    result = subprocess.run(["git", "ls-remote", "--exit-code", "origin", ref],
                            capture_output=True, text=True)
    if result.returncode == 2 and not result.stdout:
        return None
    result.check_returncode()
    match = re.fullmatch(r"([0-9a-f]{40})\t" + re.escape(ref) + r"\n", result.stdout)
    if not match:
        raise ValueError("Unexpected digest branch response")
    return match[1]


def prepare_commit(parent, paths, date):
    # The report was generated on the workflow checkout. Base the new tree on
    # the published branch independently so checkout cannot clobber the report.
    with tempfile.TemporaryDirectory() as scratch:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(scratch) / "index"))
        git("read-tree", parent, env=env)
        for path in paths:
            if path.resolve() != Path.cwd() / path or not path.is_file():
                raise ValueError("Expected a generated regular report file")
            blob = git("hash-object", "-w", "--no-filters", "--", str(path))
            git("update-index", "--add", "--cacheinfo", "100644", blob, str(path), env=env)
        tree = git("write-tree", env=env)
    changed = set(git("diff", "--no-renames", "--name-only", parent, tree).splitlines())
    if not changed.issubset({str(path) for path in paths}):
        raise ValueError("Digest update would change unrelated paths")
    if not changed:
        return parent
    return git("-c", "user.name=merglbot-bot", "-c", "user.email=bot@merglbot.ai",
               "commit-tree", tree, "-p", parent, "-m", f"chore(pr-assistant): improvement digest {date}")


def lookup(repo, branch):
    fields = "number,state,baseRefName,headRefName,headRefOid,isCrossRepository"
    raw = subprocess.check_output(["gh", "pr", "list", "--repo", repo, "--head", branch,
                                   "--base", "main", "--state", "all", "--limit", "100", "--json", fields])
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) >= 100:
        raise ValueError("Incomplete PR lookup")
    same_repo = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("isCrossRepository")) is not bool:
            raise ValueError("Invalid PR origin")
        if row["isCrossRepository"]:
            continue
        if (row.get("headRefName") != branch or row.get("baseRefName") != "main"
                or row.get("state") not in {"OPEN", "CLOSED", "MERGED"}
                or type(row.get("number")) is not int or row["number"] <= 0
                or not re.fullmatch("[0-9a-f]{40}", row.get("headRefOid") or "")):
            raise ValueError("Invalid digest PR metadata")
        same_repo.append(row)
    if len(same_repo) > 1:
        raise ValueError("Digest branch has multiple PRs; inspect their disposition")
    return same_repo[0] if same_repo else None


def publish():
    repo = os.environ["GITHUB_REPOSITORY"]
    date = os.environ["DATE"]
    days = os.environ["DAYS_BACK"]
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date) or not re.fullmatch(r"[0-9]+", days):
        raise ValueError("Invalid digest date or lookback")
    directory = Path("docs/pr-assistant/improvement-digest")
    paths = [directory / f"{date}.md", directory / "LATEST.md"]
    branch = "chore/pr-assistant-improvement-digest-" + date
    previous = remote_tip(branch)
    pr = lookup(repo, branch)
    if pr and pr["state"] != "OPEN":
        raise ValueError("Existing digest PR is terminal; inspect before republishing")
    if pr and pr["headRefOid"] != previous:
        raise ValueError("Digest branch differs from the PR head")
    if previous:
        git("fetch", "--no-tags", "origin", "refs/heads/" + branch)
        parent = git("rev-parse", "FETCH_HEAD")
        if parent != previous:
            raise ValueError("Digest branch moved during publication")
    else:
        parent = git("rev-parse", "HEAD")
        if parent != remote_tip("main"):
            raise ValueError("New digest branch must start at the current main commit")

    candidate = prepare_commit(parent, paths, date)
    if previous is None and candidate == parent:
        print("Generated files already match the workflow checkout.")
        return
    if candidate != previous:
        git("push", "origin", f"{candidate}:refs/heads/{branch}")
    if remote_tip(branch) != candidate:
        raise ValueError("Published digest branch could not be verified")
    if pr is None:
        # A concurrent publisher or interrupted previous attempt may have opened
        # the PR already. Re-read after push and never retry an ambiguous create.
        pr = lookup(repo, branch)
        if pr is None:
            subprocess.run(["gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch,
                            "--title", f"chore(pr-assistant): improvement digest {date}",
                            "--body", f"Automated PR Assistant improvement digest for {date} ({days} days)."],
                           check=False)
    pr = lookup(repo, branch)
    if not pr or pr["state"] != "OPEN" or pr["headRefOid"] != candidate:
        raise ValueError("Publication incomplete: expected an open PR on the exact digest commit")
    print(f"Verified digest PR #{pr['number']} on {candidate}")


if __name__ == "__main__":
    publish()
