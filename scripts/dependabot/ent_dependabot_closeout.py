#!/usr/bin/env python3
"""Weekly autonomous Dependabot closeout for Merglbot ENT repositories.

The script is intentionally conservative about evidence and aggressive only
after the configured gates are proven on the live PR head. It emits one strict
JSON object to stdout and writes audit artifacts into the selected output dir.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


DEPENDABOT_LOGINS = {"dependabot[bot]", "app/dependabot"}
REPOSITORY_RE = re.compile(r"\[`([^`]+/[^`]+)`\]\(https://github.com/[^)]+\).*\|\s*Active\s*\|")
MERGLBOT_REVIEW_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_WAIT_SECONDS", "1500"))
MERGLBOT_REVIEW_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_POLL_SECONDS", "60"))
REBASE_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_WAIT_SECONDS", "600"))
REBASE_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_POLL_SECONDS", "60"))


class GhError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_cmd(args: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        check=False,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise GhError(proc.stderr.strip() or proc.stdout.strip() or f"{' '.join(args)} failed")
    return proc


def gh_json(args: list[str]) -> Any:
    proc = run_cmd(["gh", *args])
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise GhError(f"gh {' '.join(args)} returned non-JSON output: {exc}") from exc


def gh_api_json(endpoint: str, *extra: str) -> Any:
    return gh_json(["api", "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint, *extra])


def gh_api_text(endpoint: str, *extra: str) -> str:
    return run_cmd(["gh", "api", "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint, *extra]).stdout


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_repository_map(text: str) -> list[str]:
    repos: list[str] = []
    for line in text.splitlines():
        match = REPOSITORY_RE.search(line)
        if match:
            repos.append(match.group(1))
    return sorted(dict.fromkeys(repos))


def fetch_repository_map() -> str:
    content = gh_api_json("repos/merglbot-public/docs/contents/REPOSITORY_MAP.md?ref=main")
    encoded = content.get("content")
    if not encoded:
        raise GhError("REPOSITORY_MAP.md content missing from GitHub API response")
    return base64.b64decode(encoded).decode("utf-8")


def load_repo_scope(scope_file: Path | None) -> list[str]:
    if scope_file and scope_file.exists():
        text = scope_file.read_text(encoding="utf-8")
    else:
        text = fetch_repository_map()
    repos = parse_repository_map(text)
    if len(repos) != 42:
        raise GhError(f"expected 42 in-scope active repositories, got {len(repos)}")
    if any(repo.startswith("Merglevsky-cz/") or repo.startswith("merglevsky-cz/") for repo in repos):
        raise GhError("out-of-scope Merglevsky-cz repository appeared in scope")
    return repos


def repo_endpoint(repo: str, suffix: str) -> str:
    return f"repos/{repo}/{suffix.lstrip('/')}"


def issue_comment_endpoint(repo: str, number: int) -> str:
    return repo_endpoint(repo, f"issues/{number}/comments")


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    author: str
    head_sha: str
    base_ref: str
    head_ref: str
    is_draft: bool
    merge_state: str
    updated_at: str


@dataclass
class ItemReceipt:
    repo: str
    pr_number: int
    url: str
    action: str
    classification: str
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    head_sha: str | None = None
    merged_sha: str | None = None
    comment_url: str | None = None
    cursor_status: str | None = None
    merglbot_receipt: dict[str, Any] | None = None
    post_merge: dict[str, Any] | None = None


def list_dependabot_prs(repo: str) -> list[PullRequest]:
    data = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,url,author,headRefOid,baseRefName,headRefName,isDraft,mergeStateStatus,updatedAt",
        ]
    )
    prs: list[PullRequest] = []
    for item in data:
        author = (item.get("author") or {}).get("login") or ""
        if author not in DEPENDABOT_LOGINS:
            continue
        prs.append(
            PullRequest(
                repo=repo,
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                author=author,
                head_sha=str(item.get("headRefOid") or ""),
                base_ref=str(item.get("baseRefName") or "main"),
                head_ref=str(item.get("headRefName") or ""),
                is_draft=bool(item.get("isDraft")),
                merge_state=str(item.get("mergeStateStatus") or "UNKNOWN"),
                updated_at=str(item.get("updatedAt") or ""),
            )
        )
    return prs


def pr_files(repo: str, number: int) -> list[str]:
    proc = run_cmd(["gh", "pr", "diff", str(number), "--repo", repo, "--name-only"], check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def refresh_pr(repo: str, number: int) -> PullRequest:
    item = gh_json(
        [
            "pr",
            "view",
            str(number),
            "--repo",
            repo,
            "--json",
            "number,title,url,author,headRefOid,baseRefName,headRefName,isDraft,mergeStateStatus,updatedAt,state",
        ]
    )
    if item.get("state") != "OPEN":
        raise GhError(f"{repo}#{number} is no longer open")
    author = (item.get("author") or {}).get("login") or ""
    return PullRequest(
        repo=repo,
        number=int(item["number"]),
        title=str(item.get("title") or ""),
        url=str(item.get("url") or ""),
        author=author,
        head_sha=str(item.get("headRefOid") or ""),
        base_ref=str(item.get("baseRefName") or "main"),
        head_ref=str(item.get("headRefName") or ""),
        is_draft=bool(item.get("isDraft")),
        merge_state=str(item.get("mergeStateStatus") or "UNKNOWN"),
        updated_at=str(item.get("updatedAt") or ""),
    )


def required_checks(repo: str, number: int) -> tuple[bool, list[dict[str, Any]], list[str]]:
    proc = run_cmd(
        [
            "gh",
            "pr",
            "checks",
            str(number),
            "--repo",
            repo,
            "--required",
            "--json",
            "name,bucket,state,completedAt,link",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return False, [], [proc.stderr.strip() or "required_checks_lookup_failed"]
    checks = json.loads(proc.stdout or "[]")
    blockers = [
        f"{check.get('name')}:{check.get('bucket') or check.get('state')}"
        for check in checks
        if check.get("bucket") != "pass"
    ]
    return len(blockers) == 0, checks, blockers


def all_checks(repo: str, number: int) -> list[dict[str, Any]]:
    proc = run_cmd(
        [
            "gh",
            "pr",
            "checks",
            str(number),
            "--repo",
            repo,
            "--json",
            "name,bucket,state,completedAt,link",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return []
    return json.loads(proc.stdout or "[]")


def cursor_status(repo: str, number: int) -> tuple[bool, str]:
    checks = [check for check in all_checks(repo, number) if str(check.get("name") or "") == "Cursor Bugbot"]
    if not checks:
        return True, "cursor_absent_not_required"
    latest = checks[-1]
    bucket = str(latest.get("bucket") or latest.get("state") or "unknown")
    if bucket == "pass":
        return True, "cursor_pass"
    if bucket in {"skipping", "neutral"}:
        return True, "cursor_no_current_bug_signal"
    return False, f"cursor_blocker:{bucket}"


def verify_merglbot(repo: str, number: int) -> dict[str, Any]:
    script = Path(__file__).resolve().parents[1] / "pr-assistant" / "verify-review-receipt.py"
    proc = run_cmd(["python3", str(script), "--repo", repo, "--pr", str(number)], check=False)
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"ok": False, "blockers": ["verify_review_receipt_non_json"], "stderr": proc.stderr}
    if proc.returncode != 0 and not payload.get("blockers"):
        payload["blockers"] = [proc.stderr.strip() or "verify_review_receipt_failed"]
    return payload


def trigger_merglbot_review(repo: str, number: int) -> str:
    proc = run_cmd(["gh", "pr", "comment", str(number), "--repo", repo, "--body", "@merglbot review --light"])
    return proc.stdout.strip()


def wait_for_merglbot(repo: str, number: int, *, apply: bool) -> dict[str, Any]:
    first = verify_merglbot(repo, number)
    if first.get("ok"):
        return first
    if not apply:
        return first
    trigger_merglbot_review(repo, number)
    deadline = time.time() + MERGLBOT_REVIEW_WAIT_SECONDS
    latest = first
    while time.time() <= deadline:
        time.sleep(min(MERGLBOT_REVIEW_POLL_SECONDS, max(0, deadline - time.time())))
        latest = verify_merglbot(repo, number)
        if latest.get("ok"):
            return latest
    latest.setdefault("blockers", []).append("merglbot_review_poll_timeout")
    return latest


def post_comment_with_stdin(repo: str, number: int, body: str) -> str:
    proc = run_cmd(
        ["gh", "api", issue_comment_endpoint(repo, number), "-X", "POST", "--input", "-"],
        input_text=json.dumps({"body": body}),
    )
    comment = json.loads(proc.stdout or "{}")
    return str(comment.get("html_url") or comment.get("url") or "")


def close_pr(repo: str, number: int, body: str) -> str:
    comment_url = post_comment_with_stdin(repo, number, body)
    run_cmd(["gh", "pr", "close", str(number), "--repo", repo])
    return comment_url


def request_dependabot_rebase(repo: str, number: int, *, apply: bool) -> None:
    if not apply:
        return
    post_comment_with_stdin(repo, number, "@dependabot rebase")
    deadline = time.time() + REBASE_WAIT_SECONDS
    initial = refresh_pr(repo, number).head_sha
    while time.time() <= deadline:
        time.sleep(min(REBASE_POLL_SECONDS, max(0, deadline - time.time())))
        current = refresh_pr(repo, number).head_sha
        if current != initial:
            return


def default_branch(repo: str) -> str:
    return str(gh_api_json(f"repos/{repo}")["default_branch"])


def branch_protection(repo: str, branch: str) -> dict[str, Any] | None:
    encoded = quote(branch, safe="")
    proc = run_cmd(["gh", "api", repo_endpoint(repo, f"branches/{encoded}/protection")], check=False)
    if proc.returncode != 0:
        return None
    return json.loads(proc.stdout or "{}")


def align_review_gate(repo: str, output_dir: Path, *, apply: bool) -> dict[str, Any]:
    branch = default_branch(repo)
    before = branch_protection(repo, branch)
    if not before:
        return {"ok": False, "repo": repo, "branch": branch, "blockers": ["branch_protection_missing"]}
    reviews = before.get("required_pull_request_reviews") or {}
    current_count = int(reviews.get("required_approving_review_count") or 0)
    dismiss_stale = bool(reviews.get("dismiss_stale_reviews") or False)
    snapshot_path = output_dir / "policy" / repo.replace("/", "__") / f"{branch}-before.json"
    write_json(snapshot_path, before)
    rollback = (
        f"gh api repos/{repo}/branches/{quote(branch, safe='')}/protection/required_pull_request_reviews "
        f"-X PATCH -F required_approving_review_count={current_count} -F dismiss_stale_reviews={'true' if dismiss_stale else 'false'}"
    )
    if current_count == 0:
        return {
            "ok": True,
            "repo": repo,
            "branch": branch,
            "changed": False,
            "before_snapshot": str(snapshot_path),
            "rollback": rollback,
        }
    if not apply:
        return {
            "ok": True,
            "repo": repo,
            "branch": branch,
            "changed": False,
            "dry_run": True,
            "before_snapshot": str(snapshot_path),
            "intended_mutation": "set required_approving_review_count=0 for evidence-gated Dependabot lane",
            "rollback": rollback,
        }
    endpoint = repo_endpoint(repo, f"branches/{quote(branch, safe='')}/protection/required_pull_request_reviews")
    run_cmd(["gh", "api", endpoint, "-X", "PATCH", "-F", "required_approving_review_count=0", "-F", "dismiss_stale_reviews=true"])
    after = branch_protection(repo, branch)
    after_count = int(((after or {}).get("required_pull_request_reviews") or {}).get("required_approving_review_count") or 0)
    if after_count != 0:
        return {
            "ok": False,
            "repo": repo,
            "branch": branch,
            "changed": True,
            "before_snapshot": str(snapshot_path),
            "rollback": rollback,
            "blockers": [f"post_verify_required_review_count={after_count}"],
        }
    write_json(output_dir / "policy" / repo.replace("/", "__") / f"{branch}-after.json", after)
    return {
        "ok": True,
        "repo": repo,
        "branch": branch,
        "changed": True,
        "before_snapshot": str(snapshot_path),
        "post_verify": "passed",
        "rollback": rollback,
        "reason": "Dependabot evidence-gated autonomous lane",
    }


def merge_pr(repo: str, number: int, head_sha: str) -> dict[str, Any]:
    run_cmd(["gh", "pr", "merge", str(number), "--repo", repo, "--squash", "--match-head-commit", head_sha])
    pr = gh_json(["pr", "view", str(number), "--repo", repo, "--json", "state,mergeCommit,url"])
    merge_commit = pr.get("mergeCommit") or {}
    oid = merge_commit.get("oid")
    reachable = False
    if oid:
        proc = run_cmd(["gh", "api", repo_endpoint(repo, f"commits/{oid}/branches-where-head")], check=False)
        reachable = proc.returncode == 0
    return {
        "state": pr.get("state"),
        "merge_commit": oid,
        "merge_commit_url": merge_commit.get("url"),
        "reachable_lookup_ok": reachable,
    }


def close_comment(pr: PullRequest, classification: str, evidence: list[str], workflow_url: str) -> str:
    return "\n".join(
        [
            "Dependabot PR closed by ENT weekly autonomous closeout.",
            "",
            f"- Classification: `{classification}`",
            f"- Evidence: {'; '.join(evidence)}",
            "- Reopen condition: reopen or create a new Dependabot PR if the dependency update is still needed on current main.",
            f"- Workflow run: {workflow_url or 'not available'}",
        ]
    )


def process_pr(
    pr: PullRequest,
    *,
    mode: str,
    output_dir: Path,
    allow_policy_alignment: bool,
    workflow_url: str,
) -> ItemReceipt:
    apply = mode == "apply"
    receipt = ItemReceipt(repo=pr.repo, pr_number=pr.number, url=pr.url, action="blocked", classification="BLOCKED", head_sha=pr.head_sha)
    if pr.author not in DEPENDABOT_LOGINS:
        receipt.blockers.append("not_dependabot_author")
        return receipt
    if pr.is_draft:
        receipt.blockers.append("draft_pr")
        return receipt
    files = pr_files(pr.repo, pr.number)
    if not files:
        receipt.action = "would_close" if not apply else "closed"
        receipt.classification = "AUTO_CLOSE_EMPTY_DIFF"
        receipt.evidence.append("current PR diff has no changed files")
        if apply:
            receipt.comment_url = close_pr(pr.repo, pr.number, close_comment(pr, receipt.classification, receipt.evidence, workflow_url))
        return receipt

    refreshed = refresh_pr(pr.repo, pr.number)
    receipt.head_sha = refreshed.head_sha
    if refreshed.merge_state == "BEHIND":
        receipt.evidence.append("PR was behind base; requested Dependabot rebase")
        request_dependabot_rebase(pr.repo, pr.number, apply=apply)
        refreshed = refresh_pr(pr.repo, pr.number)
        receipt.head_sha = refreshed.head_sha

    checks_ok, checks, check_blockers = required_checks(pr.repo, pr.number)
    if not checks_ok:
        receipt.blockers.extend([f"required_check:{blocker}" for blocker in check_blockers])
        receipt.evidence.append(f"required_checks={len(checks)}")
        return receipt

    merglbot = wait_for_merglbot(pr.repo, pr.number, apply=apply)
    receipt.merglbot_receipt = merglbot
    if not merglbot.get("ok"):
        receipt.blockers.extend([f"merglbot:{blocker}" for blocker in merglbot.get("blockers", [])])
        return receipt

    cursor_ok, cursor = cursor_status(pr.repo, pr.number)
    receipt.cursor_status = cursor
    if not cursor_ok:
        receipt.blockers.append(cursor)
        return receipt

    refreshed = refresh_pr(pr.repo, pr.number)
    if refreshed.head_sha != receipt.head_sha:
        receipt.blockers.append("head_changed_after_review")
        receipt.head_sha = refreshed.head_sha
        return receipt

    if refreshed.merge_state == "REVIEW_REQUIRED" and allow_policy_alignment:
        alignment = align_review_gate(pr.repo, output_dir, apply=apply)
        receipt.evidence.append(f"policy_alignment={alignment.get('ok')}")
        if not alignment.get("ok"):
            receipt.blockers.extend([f"policy_alignment:{blocker}" for blocker in alignment.get("blockers", [])])
            write_json(output_dir / "policy" / f"{pr.repo.replace('/', '__')}-failed.json", alignment)
            return receipt
    elif refreshed.merge_state == "REVIEW_REQUIRED":
        receipt.blockers.append("review_required_policy_alignment_disabled")
        return receipt

    if not apply:
        receipt.action = "would_merge"
        receipt.classification = "MERGE_ELIGIBLE_MAXIMUM_AUTONOMY"
        receipt.evidence.extend(["required checks green", "Merglbot current-head approved", receipt.cursor_status or "cursor_unknown"])
        return receipt

    receipt.post_merge = merge_pr(pr.repo, pr.number, refreshed.head_sha)
    if receipt.post_merge.get("state") != "MERGED":
        receipt.blockers.append("post_merge_state_not_merged")
        return receipt
    receipt.action = "merged"
    receipt.classification = "MERGED_MAXIMUM_AUTONOMY"
    receipt.merged_sha = receipt.post_merge.get("merge_commit")
    receipt.evidence.extend(["exact-head squash merge completed", "post-merge PR state verified"])
    return receipt


def process_repo(
    repo: str,
    *,
    mode: str,
    output_dir: Path,
    max_prs_per_repo: int,
    allow_policy_alignment: bool,
    workflow_url: str,
) -> dict[str, Any]:
    repo_result: dict[str, Any] = {
        "repo": repo,
        "ok": True,
        "dependabot_prs_before": 0,
        "merged": [],
        "closed": [],
        "blocked": [],
        "would_merge": [],
        "would_close": [],
        "warnings": [],
    }
    processed = 0
    seen_without_action: set[int] = set()
    while True:
        try:
            prs = list_dependabot_prs(repo)
        except Exception as exc:
            repo_result["ok"] = False
            repo_result["warnings"].append(f"list_dependabot_prs_failed:{exc}")
            return repo_result
        if repo_result["dependabot_prs_before"] == 0:
            repo_result["dependabot_prs_before"] = len(prs)
        if not prs:
            return repo_result
        if max_prs_per_repo and processed >= max_prs_per_repo:
            repo_result["warnings"].append("max_prs_per_repo_reached")
            return repo_result

        took_action = False
        for pr in prs:
            if pr.number in seen_without_action:
                continue
            receipt = process_pr(pr, mode=mode, output_dir=output_dir, allow_policy_alignment=allow_policy_alignment, workflow_url=workflow_url)
            processed += 1
            key = receipt.action
            if key == "merged":
                repo_result["merged"].append(receipt.__dict__)
                took_action = True
                break
            if key == "closed":
                repo_result["closed"].append(receipt.__dict__)
                took_action = True
                break
            if key == "would_merge":
                repo_result["would_merge"].append(receipt.__dict__)
            elif key == "would_close":
                repo_result["would_close"].append(receipt.__dict__)
            else:
                repo_result["blocked"].append(receipt.__dict__)
            seen_without_action.add(pr.number)
            if max_prs_per_repo and processed >= max_prs_per_repo:
                repo_result["warnings"].append("max_prs_per_repo_reached")
                return repo_result
        if not took_action:
            return repo_result


def post_tracking_report(tracking_issue: str, report: dict[str, Any], summary_markdown: str) -> str | None:
    if not tracking_issue:
        return None
    match = re.match(r"https://github.com/([^/]+/[^/]+)/issues/(\d+)$", tracking_issue)
    if not match:
        raise GhError(f"invalid tracking issue URL: {tracking_issue}")
    repo, number = match.group(1), int(match.group(2))
    body = "\n".join(
        [
            "## ENT Dependabot Weekly Autonomous Closeout",
            "",
            summary_markdown,
            "",
            "<details><summary>Machine receipt</summary>",
            "",
            "```json",
            json.dumps(report, indent=2, sort_keys=True),
            "```",
            "",
            "</details>",
        ]
    )
    return post_comment_with_stdin(repo, number, body)


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"- Mode: `{report['mode']}`",
        f"- Repos scanned: `{report['repos_scanned']}`",
        f"- Dependabot PRs before: `{report['dependabot_prs_before']}`",
        f"- Merged: `{len(report['merged_prs'])}`",
        f"- Closed: `{len(report['closed_prs'])}`",
        f"- Blocked: `{len(report['blocked_prs'])}`",
        "",
        "| Repo | Before | Merged | Closed | Blocked | Would merge | Would close |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["repo_table"]:
        lines.append(
            "| {repo} | {before} | {merged} | {closed} | {blocked} | {would_merge} | {would_close} |".format(
                repo=row["repo"],
                before=row["dependabot_prs_before"],
                merged=row["merged"],
                closed=row["closed"],
                blocked=row["blocked"],
                would_merge=row["would_merge"],
                would_close=row["would_close"],
            )
        )
    return "\n".join(lines)


def build_report(mode: str, repos: list[str], repo_results: list[dict[str, Any]], tracking_comment_url: str | None = None) -> dict[str, Any]:
    merged = [item for result in repo_results for item in result.get("merged", [])]
    closed = [item for result in repo_results for item in result.get("closed", [])]
    blocked = [item for result in repo_results for item in result.get("blocked", [])]
    would_merge = [item for result in repo_results for item in result.get("would_merge", [])]
    would_close = [item for result in repo_results for item in result.get("would_close", [])]
    repo_table = [
        {
            "repo": result["repo"],
            "dependabot_prs_before": result["dependabot_prs_before"],
            "merged": len(result.get("merged", [])),
            "closed": len(result.get("closed", [])),
            "blocked": len(result.get("blocked", [])),
            "would_merge": len(result.get("would_merge", [])),
            "would_close": len(result.get("would_close", [])),
            "warnings": result.get("warnings", []),
        }
        for result in repo_results
    ]
    report = {
        "ok": True,
        "final_verdict": "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_COMPLETE",
        "generated_at": utc_now(),
        "mode": mode,
        "repos_scanned": len(repos),
        "dependabot_prs_before": sum(row["dependabot_prs_before"] for row in repo_table),
        "merged_prs": merged,
        "closed_prs": closed,
        "blocked_prs": blocked,
        "would_merge_prs": would_merge,
        "would_close_prs": would_close,
        "remaining_dependabot_prs": len(blocked) + (len(would_merge) if mode == "dry-run" else 0) + (len(would_close) if mode == "dry-run" else 0),
        "repo_table": repo_table,
        "tracking_comment_url": tracking_comment_url,
        "remaining_blockers": [warning for result in repo_results for warning in result.get("warnings", [])],
    }
    if report["remaining_blockers"]:
        report["ok"] = False
        report["final_verdict"] = "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_BLOCKED"
    return report


def self_test() -> int:
    sample = """
| [`merglbot-core/github`](https://github.com/merglbot-core/github) | Shared | GitHub Actions | Active |
| [`merglbot-denatura/denatura-btf-data`](https://github.com/merglbot-denatura/denatura-btf-data) | Old | Python | Archived |
| [`merglbot-public/docs`](https://github.com/merglbot-public/docs) | Docs | Markdown | Active |
"""
    assert parse_repository_map(sample) == ["merglbot-core/github", "merglbot-public/docs"]
    report = build_report(
        "dry-run",
        ["merglbot-core/github"],
        [
            {
                "repo": "merglbot-core/github",
                "ok": True,
                "dependabot_prs_before": 1,
                "merged": [],
                "closed": [],
                "blocked": [],
                "would_merge": [{"repo": "merglbot-core/github", "pr_number": 1}],
                "would_close": [],
                "warnings": [],
            }
        ],
    )
    assert report["repos_scanned"] == 1
    assert report["dependabot_prs_before"] == 1
    assert report["remaining_dependabot_prs"] == 1
    assert "MERGLBOT" not in close_comment(
        PullRequest("o/r", 1, "x", "u", "dependabot[bot]", "a" * 40, "main", "dependabot/x", False, "CLEAN", utc_now()),
        "AUTO_CLOSE_EMPTY_DIFF",
        ["empty diff"],
        "https://github.com/o/r/actions/runs/1",
    )
    print(json.dumps({"ok": True, "self_test": "passed"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["dry-run", "apply"])
    parser.add_argument("--repo-scope", choices=["all", "cohort", "single_repo"], default="all")
    parser.add_argument("--single-repo")
    parser.add_argument("--cohort-file", type=Path)
    parser.add_argument("--scope-file", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/ent-dependabot-weekly"))
    parser.add_argument("--max-parallel-repos", type=int, default=3)
    parser.add_argument("--max-prs-per-repo", type=int, default=0)
    parser.add_argument("--allow-policy-alignment", action="store_true")
    parser.add_argument("--comment-report", action="store_true")
    parser.add_argument("--tracking-issue", default="")
    parser.add_argument("--workflow-url", default=os.environ.get("GITHUB_SERVER_URL", "") + "/" + os.environ.get("GITHUB_REPOSITORY", "") + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.mode:
        parser.error("--mode is required unless --self-test is used")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        all_repos = load_repo_scope(args.scope_file)
        if args.repo_scope == "single_repo":
            if not args.single_repo:
                raise GhError("--single-repo is required with --repo-scope single_repo")
            repos = [args.single_repo]
            missing = set(repos) - set(all_repos)
            if missing:
                raise GhError(f"single repo is outside 42-repo scope: {', '.join(sorted(missing))}")
        elif args.repo_scope == "cohort":
            if not args.cohort_file:
                raise GhError("--cohort-file is required with --repo-scope cohort")
            repos = [line.strip() for line in args.cohort_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
            missing = set(repos) - set(all_repos)
            if missing:
                raise GhError(f"cohort contains repos outside 42-repo scope: {', '.join(sorted(missing))}")
        else:
            repos = all_repos

        repo_results = []
        for repo in repos:
            repo_results.append(
                process_repo(
                    repo,
                    mode=args.mode,
                    output_dir=args.output_dir,
                    max_prs_per_repo=args.max_prs_per_repo,
                    allow_policy_alignment=args.allow_policy_alignment,
                    workflow_url=args.workflow_url,
                )
            )
        report = build_report(args.mode, repos, repo_results)
        summary = markdown_summary(report)
        if args.comment_report and args.tracking_issue:
            report["tracking_comment_url"] = post_tracking_report(args.tracking_issue, report, summary)
        write_json(args.output_dir / "ent_dependabot_weekly_receipt.json", report)
        write_json(args.output_dir / "ent_dependabot_repo_results.json", repo_results)
        (args.output_dir / "summary.md").write_text(summary + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    except Exception as exc:
        failure = {
            "ok": False,
            "final_verdict": "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_BLOCKED",
            "generated_at": utc_now(),
            "error": str(exc),
            "remaining_blockers": [str(exc)],
        }
        write_json(args.output_dir / "ent_dependabot_weekly_receipt.json", failure)
        print(json.dumps(failure, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
