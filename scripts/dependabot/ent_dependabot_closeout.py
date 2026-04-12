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
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEPENDABOT_LOGINS = {"dependabot[bot]", "app/dependabot"}
REPOSITORY_RE = re.compile(r"\[`([^`]+/[^`]+)`\]\(https://github.com/[^)]+\).*\|\s*Active\s*\|")
MERGLBOT_REVIEW_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_WAIT_SECONDS", "1500"))
MERGLBOT_REVIEW_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_POLL_SECONDS", "60"))
REBASE_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_WAIT_SECONDS", "600"))
REBASE_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_POLL_SECONDS", "60"))
OPEN_ITEM_LIST_LIMIT = 1000

DEPENDENCY_FILE_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"(^|/)package-lock\.json$",
        r"(^|/)npm-shrinkwrap\.json$",
        r"(^|/)pnpm-lock\.yaml$",
        r"(^|/)yarn\.lock$",
        r"(^|/)bun\.lockb?$",
        r"(^|/)requirements[^/]*\.txt$",
        r"(^|/)constraints[^/]*\.txt$",
        r"(^|/)poetry\.lock$",
        r"(^|/)Pipfile\.lock$",
        r"(^|/)go\.sum$",
        r"(^|/)Gemfile\.lock$",
        r"(^|/)Cargo\.lock$",
        r"(^|/)composer\.lock$",
        r"(^|/)gradle\.lockfile$",
        r"(^|/)packages\.lock\.json$",
    ]
]

MIXED_PURPOSE_MANIFEST_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"(^|/)package\.json$",
        r"(^|/)pyproject\.toml$",
        r"(^|/)Pipfile$",
        r"(^|/)go\.mod$",
        r"(^|/)Gemfile$",
        r"(^|/)Cargo\.toml$",
        r"(^|/)composer\.json$",
        r"(^|/)pom\.xml$",
        r"(^|/)build\.gradle(\.kts)?$",
        r"(^|/)Directory\.Packages\.props$",
        r"(^|/)global\.json$",
    ]
]

SENSITIVE_FILE_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"^\.github/workflows/",
        r"(^|/)Dockerfile$",
        r"(^|/)docker-compose[^/]*\.ya?ml$",
        r"(^|/)cloudbuild\.ya?ml$",
        r"(^|/)deploy/",
        r"(^|/)deployment/",
        r"(^|/)k8s/",
        r"(^|/)helm/",
        r"(^|/)terraform/",
        r"(^|/)infra/",
        r"(^|/).*\.tf$",
        r"(^|/)\.terraform\.lock\.hcl$",
        r"(^|/)auth/",
        r"(^|/)iam/",
        r"(^|/)secrets?/",
    ]
]


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


def list_open_prs(repo: str) -> list[dict[str, Any]]:
    data = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(OPEN_ITEM_LIST_LIMIT),
            "--json",
            "number,title,url,author,headRefOid,isDraft,mergeStateStatus,updatedAt",
        ]
    )
    return list(data)


def list_open_issues(repo: str) -> list[dict[str, Any]]:
    data = gh_json(["issue", "list", "--repo", repo, "--state", "open", "--limit", str(OPEN_ITEM_LIST_LIMIT), "--json", "number,title,url,author,updatedAt"])
    return list(data)


def parse_pr_allowlist(raw: str) -> set[tuple[str, int]]:
    allowed: set[tuple[str, int]] = set()
    if not raw.strip():
        return allowed
    tokens = [part.strip() for part in re.split(r"[\s,]+", raw) if part.strip()]
    for token in tokens:
        match = re.search(r"(?:https?://)?github\.com/([^/]+/[^/]+)/pull/(\d+)(?:[/?#].*)?$", token)
        if not match:
            match = re.search(r"^([^#\s]+/[^#\s]+)#(\d+)$", token)
        if not match:
            raise GhError(f"invalid pr_allowlist token: {token}")
        allowed.add((match.group(1), int(match.group(2))))
    return allowed


ApprovalPacket = tuple[str, str]


def approval_packet_candidates(approval_note: str, approval_issue_url: str) -> list[ApprovalPacket]:
    actor = os.environ.get("GITHUB_TRIGGERING_ACTOR") or os.environ.get("GITHUB_ACTOR") or ""
    candidates: list[ApprovalPacket] = [(actor, approval_note)] if approval_note.strip() else []
    if not approval_issue_url:
        return candidates
    parsed = urlparse(approval_issue_url)
    if parsed.netloc != "github.com":
        raise GhError("approval_issue_url must be a github.com issue or pull request URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 4 or parts[2] not in {"issues", "pull"}:
        raise GhError("approval_issue_url must point to a GitHub issue or pull request")
    owner, repo, _, number = parts[:4]
    repo_slug = f"{owner}/{repo}".lower()
    trusted_repos = {
        item.strip().lower()
        for item in os.environ.get("ENT_DEPENDABOT_APPROVAL_REPOS", "merglbot-core/github,merglbot-public/docs").split(",")
        if item.strip()
    }
    if repo_slug not in trusted_repos:
        raise GhError(f"approval_issue_url must point to a trusted approval repo: {', '.join(sorted(trusted_repos))}")
    issue = gh_api_json(f"repos/{owner}/{repo}/issues/{number}")
    comments = gh_api_json(f"repos/{owner}/{repo}/issues/{number}/comments?per_page=100")
    issue_body = issue.get("body", "") or ""
    issue_author = ((issue.get("user") or {}).get("login") or "").lower()
    if issue_body.strip():
        candidates.append((issue_author, issue_body))
    for comment in comments:
        body = comment.get("body", "") or ""
        if body.strip():
            author = ((comment.get("user") or {}).get("login") or "").lower()
            candidates.append((author, body))
    return candidates


def parse_approval_packet_fields(packet: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(
        r"(?im)(approved_by|approved_at|expected_action|approval_scope|authorized_sha|authorized_run)\s*=\s*`?([^`\s]+)",
        packet,
    ):
        fields[match.group(1).lower()] = match.group(2).strip().strip("`")
    return fields


def validate_apply_approval(mode: str, pr_allowlist: set[tuple[str, int]], approval_note: str, approval_issue_url: str) -> None:
    if mode != "apply":
        return
    packets = approval_packet_candidates(approval_note, approval_issue_url)
    combined_material = "\n".join(packet.lower() for _, packet in packets)
    explicit_approval_lane = bool(pr_allowlist or approval_issue_url or "post_change_validation=true" in combined_material)
    if not explicit_approval_lane:
        return
    if not packets:
        raise GhError("apply approval requires approval_note or approval_issue_url")

    current_sha = os.environ.get("GITHUB_SHA", "").lower()
    current_run = os.environ.get("GITHUB_RUN_ID", "")
    trusted_approvers = {
        item.strip().lower()
        for item in os.environ.get("ENT_DEPENDABOT_TRUSTED_APPROVERS", "milhul6").split(",")
        if item.strip()
    }

    def packet_missing_markers(author: str, packet: str) -> list[str]:
        fields = parse_approval_packet_fields(packet)
        missing = [marker for marker in ["approved_by", "approved_at", "expected_action"] if not fields.get(marker)]
        approved_by = fields.get("approved_by", "").lower()
        if not author or author.lower() not in trusted_approvers:
            missing.append("trusted_author")
        if approved_by and author and approved_by != author.lower():
            missing.append("approved_by_matches_author")
        if not pr_allowlist and fields.get("approval_scope", "").lower() != "full_queue":
            missing.append("approval_scope=full_queue")
        sha_ok = bool(current_sha and fields.get("authorized_sha", "").lower() == current_sha)
        run_ok = bool(current_run and fields.get("authorized_run", "") == current_run)
        if not sha_ok and not run_ok:
            missing.append("authorized_sha/current or authorized_run/current")
        return missing

    if not any(not packet_missing_markers(author, packet) for author, packet in packets):
        unique_missing = sorted({marker for author, packet in packets for marker in packet_missing_markers(author, packet)})
        raise GhError("apply approval metadata missing from one complete packet: " + ", ".join(unique_missing))


def pr_files(repo: str, number: int) -> list[str]:
    proc = run_cmd(["gh", "pr", "diff", str(number), "--repo", repo, "--name-only"])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def is_dependency_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in DEPENDENCY_FILE_PATTERNS)


def is_mixed_purpose_manifest(path: str) -> bool:
    return any(pattern.search(path) for pattern in MIXED_PURPOSE_MANIFEST_PATTERNS)


def is_sensitive_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in SENSITIVE_FILE_PATTERNS)


def classify_change_scope(files: list[str]) -> tuple[bool, list[str], list[str]]:
    sensitive = [path for path in files if is_sensitive_file(path)]
    mixed = [path for path in files if is_mixed_purpose_manifest(path)]
    unsupported = [path for path in files if not is_dependency_file(path) and not is_mixed_purpose_manifest(path)]
    blockers: list[str] = []
    if sensitive:
        blockers.append("sensitive_file_scope:" + ",".join(sensitive))
    if mixed:
        blockers.append("mixed_purpose_manifest_requires_content_validation:" + ",".join(mixed))
    if unsupported:
        blockers.append("non_manifest_lockfile_scope:" + ",".join(unsupported))
    return not blockers, blockers, [path for path in files if is_dependency_file(path)]


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
    # GitHub branch protection review requirements are repository/branch scoped.
    # The weekly lane must not lower them unless a future ruleset integration can
    # prove Dependabot-only scoping. Until then, classify this as a policy blocker.
    return {
        "ok": False,
        "repo": repo,
        "branch": branch,
        "changed": False,
        "before_snapshot": str(snapshot_path),
        "post_verify": "not_mutated",
        "rollback": rollback,
        "blockers": ["review_required_no_dependabot_scoped_ruleset"],
        "reason": "Repository-wide review requirements cannot be safely lowered for a Dependabot-only lane.",
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
            f"- Reopen condition: reopen or create a new Dependabot PR if the dependency update is still needed on the current `{pr.base_ref}` branch.",
            f"- Workflow run: {workflow_url or 'not available'}",
        ]
    )


def validate_file_scope_for_current_head(pr: PullRequest, receipt: ItemReceipt) -> tuple[bool, PullRequest]:
    expected = refresh_pr(pr.repo, pr.number)
    receipt.head_sha = expected.head_sha
    files = pr_files(pr.repo, pr.number)
    after_scope = refresh_pr(pr.repo, pr.number)
    if after_scope.head_sha != expected.head_sha:
        receipt.blockers.append("head_changed_during_scope_check")
        receipt.head_sha = after_scope.head_sha
        return False, after_scope

    if not files:
        receipt.action = "would_close"
        receipt.classification = "AUTO_CLOSE_EMPTY_DIFF"
        receipt.evidence.append("current PR diff has no changed files")
        return False, after_scope

    scope_ok, scope_blockers, dependency_files = classify_change_scope(files)
    receipt.evidence.append(f"changed_files={len(files)}")
    receipt.evidence.append("dependency_files=" + ",".join(dependency_files))
    if not scope_ok:
        receipt.classification = "BLOCKED_CHANGE_SCOPE"
        receipt.blockers.extend([f"change_scope:{blocker}" for blocker in scope_blockers])
        return False, after_scope
    return True, after_scope


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
    scope_ok, refreshed = validate_file_scope_for_current_head(pr, receipt)
    if receipt.classification == "AUTO_CLOSE_EMPTY_DIFF":
        if apply:
            receipt.action = "closed"
            receipt.comment_url = close_pr(pr.repo, pr.number, close_comment(pr, receipt.classification, receipt.evidence, workflow_url))
        return receipt
    if not scope_ok:
        return receipt

    if refreshed.merge_state == "BEHIND":
        receipt.evidence.append("PR was behind base after scope validation; requested Dependabot rebase")
        request_dependabot_rebase(pr.repo, pr.number, apply=apply)
        rebased = refresh_pr(pr.repo, pr.number)
        if rebased.head_sha != refreshed.head_sha:
            scope_ok, refreshed = validate_file_scope_for_current_head(rebased, receipt)
            if receipt.classification == "AUTO_CLOSE_EMPTY_DIFF":
                if apply:
                    receipt.action = "closed"
                    receipt.comment_url = close_pr(pr.repo, pr.number, close_comment(rebased, receipt.classification, receipt.evidence, workflow_url))
                return receipt
            if not scope_ok:
                return receipt
        else:
            refreshed = rebased

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
    pr_allowlist: set[tuple[str, int]],
) -> dict[str, Any]:
    telemetry_warnings: list[str] = []
    try:
        open_prs = list_open_prs(repo)
    except Exception as exc:
        return {
            "repo": repo,
            "ok": False,
            "open_prs_before": 0,
            "open_issues_before": 0,
            "dependabot_prs_before": 0,
            "non_dependabot_prs_before": 0,
            "merged": [],
            "closed": [],
            "blocked": [],
            "would_merge": [],
            "would_close": [],
            "skipped_by_allowlist": [],
            "warnings": [f"list_open_prs_failed:{exc}"],
        }
    try:
        open_issues = list_open_issues(repo)
    except Exception as exc:
        open_issues = []
        telemetry_warnings.append(f"list_open_issues_failed:{exc}")
    open_dependabot_prs = [
        item
        for item in open_prs
        if ((item.get("author") or {}).get("login") or "") in DEPENDABOT_LOGINS
    ]
    repo_result: dict[str, Any] = {
        "repo": repo,
        "ok": True,
        "open_prs_before": len(open_prs),
        "open_issues_before": len(open_issues),
        "dependabot_prs_before": len(open_dependabot_prs),
        "non_dependabot_prs_before": len(open_prs) - len(open_dependabot_prs),
        "merged": [],
        "closed": [],
        "blocked": [],
        "would_merge": [],
        "would_close": [],
        "skipped_by_allowlist": [],
        "warnings": [],
        "telemetry_warnings": telemetry_warnings,
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
            if pr_allowlist and (pr.repo, pr.number) not in pr_allowlist:
                repo_result["skipped_by_allowlist"].append(
                    {
                        "repo": pr.repo,
                        "pr_number": pr.number,
                        "url": pr.url,
                        "action": "skipped",
                        "classification": "SKIPPED_NOT_IN_USER_APPROVED_ALLOWLIST",
                        "head_sha": pr.head_sha,
                    }
                )
                seen_without_action.add(pr.number)
                continue
            try:
                receipt = process_pr(pr, mode=mode, output_dir=output_dir, allow_policy_alignment=allow_policy_alignment, workflow_url=workflow_url)
            except GhError as exc:
                receipt = ItemReceipt(
                    repo=pr.repo,
                    pr_number=pr.number,
                    url=pr.url,
                    action="blocked",
                    classification="BLOCKED_RUNTIME_ERROR",
                    blockers=[f"process_pr_failed:{exc}"],
                    head_sha=pr.head_sha,
                )
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
        f"- Status: `{report['final_verdict']}`",
        f"- Repos scanned: `{report['repos_scanned']}`",
        f"- Open PRs: `{report['open_prs_total']}` (`{report['open_dependabot_prs_total']}` Dependabot / `{report['open_non_dependabot_prs_total']}` non-Dependabot)",
        f"- Open issues: `{report['open_issues_total']}`",
        f"- Dependabot PRs before: `{report['dependabot_prs_before']}`",
        f"- Merged: `{len(report['merged_prs'])}`",
        f"- Closed: `{len(report['closed_prs'])}`",
        f"- Blocked: `{len(report['blocked_prs'])}`",
        f"- Telemetry warnings: `{len(report.get('telemetry_warnings', []))}`",
        f"- Slack delivery: `{report.get('slack_delivery', {}).get('status', 'not_requested')}`",
        "",
        "| Repo | PRs | Dependabot | Non-Dependabot | Issues | Merged | Closed | Blocked | Would merge | Would close | Skipped |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["repo_table"]:
        lines.append(
            "| {repo} | {open_prs} | {before} | {non_dependabot} | {issues} | {merged} | {closed} | {blocked} | {would_merge} | {would_close} | {skipped} |".format(
                repo=row["repo"],
                open_prs=row["open_prs_before"],
                before=row["dependabot_prs_before"],
                non_dependabot=row["non_dependabot_prs_before"],
                issues=row["open_issues_before"],
                merged=row["merged"],
                closed=row["closed"],
                blocked=row["blocked"],
                would_merge=row["would_merge"],
                would_close=row["would_close"],
                skipped=row["skipped_by_allowlist"],
            )
        )
    return "\n".join(lines)


def blocker_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in items:
        for blocker in item.get("blockers", []):
            key = str(blocker).split(":", 1)[0]
            counts[key] = counts.get(key, 0) + 1
    return [{"reason": key, "count": value} for key, value in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]]


def build_report(
    mode: str,
    repos: list[str],
    repo_results: list[dict[str, Any]],
    *,
    pr_allowlist: set[tuple[str, int]],
    approval_note: str,
    approval_issue_url: str,
    workflow_url: str,
    tracking_comment_url: str | None = None,
) -> dict[str, Any]:
    merged = [item for result in repo_results for item in result.get("merged", [])]
    closed = [item for result in repo_results for item in result.get("closed", [])]
    blocked = [item for result in repo_results for item in result.get("blocked", [])]
    would_merge = [item for result in repo_results for item in result.get("would_merge", [])]
    would_close = [item for result in repo_results for item in result.get("would_close", [])]
    skipped_by_allowlist = [item for result in repo_results for item in result.get("skipped_by_allowlist", [])]
    repo_table = [
        {
            "repo": result["repo"],
            "open_prs_before": result["open_prs_before"],
            "open_issues_before": result["open_issues_before"],
            "dependabot_prs_before": result["dependabot_prs_before"],
            "non_dependabot_prs_before": result["non_dependabot_prs_before"],
            "merged": len(result.get("merged", [])),
            "closed": len(result.get("closed", [])),
            "blocked": len(result.get("blocked", [])),
            "would_merge": len(result.get("would_merge", [])),
            "would_close": len(result.get("would_close", [])),
            "skipped_by_allowlist": len(result.get("skipped_by_allowlist", [])),
            "warnings": result.get("warnings", []),
            "telemetry_warnings": result.get("telemetry_warnings", []),
        }
        for result in repo_results
    ]
    open_prs_total = sum(row["open_prs_before"] for row in repo_table)
    open_dependabot_prs_total = sum(row["dependabot_prs_before"] for row in repo_table)
    report = {
        "ok": True,
        "final_verdict": "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_COMPLETE",
        "generated_at": utc_now(),
        "mode": mode,
        "workflow_url": workflow_url,
        "repos_scanned": len(repos),
        "open_prs_total": open_prs_total,
        "open_dependabot_prs_total": open_dependabot_prs_total,
        "open_non_dependabot_prs_total": open_prs_total - open_dependabot_prs_total,
        "open_issues_total": sum(row["open_issues_before"] for row in repo_table),
        "dependabot_prs_before": open_dependabot_prs_total,
        "merged_prs": merged,
        "closed_prs": closed,
        "blocked_prs": blocked,
        "would_merge_prs": would_merge,
        "would_close_prs": would_close,
        "skipped_by_allowlist_prs": skipped_by_allowlist,
        "remaining_dependabot_prs": len(blocked)
        + len(skipped_by_allowlist)
        + (len(would_merge) if mode == "dry-run" else 0)
        + (len(would_close) if mode == "dry-run" else 0),
        "top_blocker_reasons": blocker_summary(blocked),
        "approval": {
            "pr_allowlist": [f"{repo}#{number}" for repo, number in sorted(pr_allowlist)],
            "approval_note": approval_note,
            "approval_issue_url": approval_issue_url,
        },
        "slack_delivery": {"status": "not_requested"},
        "telemetry_degraded": False,
        "repo_table": repo_table,
        "tracking_comment_url": tracking_comment_url,
        "telemetry_warnings": [warning for result in repo_results for warning in result.get("telemetry_warnings", [])],
        "remaining_blockers": [warning for result in repo_results for warning in result.get("warnings", [])],
    }
    if report["remaining_blockers"]:
        report["ok"] = False
        report["final_verdict"] = "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_BLOCKED"
    return report


def build_slack_payload(report: dict[str, Any], status: str) -> dict[str, Any]:
    top_blockers = report.get("top_blocker_reasons") or []
    blocker_text = ", ".join(f"{item['reason']}={item['count']}" for item in top_blockers[:5]) or "none"
    runtime_blockers = report.get("remaining_blockers") or []
    if runtime_blockers:
        blocker_text = ", ".join(str(item) for item in runtime_blockers[:5])
    workflow_url = report.get("workflow_url") or "not available"
    error_line = f"\nError: `{report['error']}`" if report.get("error") else ""
    summary = (
        f"*ENT Dependabot Weekly Closeout* `{status}`\n"
        f"Mode: `{report.get('mode', 'unknown')}` | Repos: `{report.get('repos_scanned', 0)}`\n"
        f"Dependabot PRs: `{report.get('dependabot_prs_before', 0)}` before, "
        f"`{len(report.get('merged_prs', []))}` merged, "
        f"`{len(report.get('closed_prs', []))}` closed, "
        f"`{len(report.get('blocked_prs', []))}` blocked, "
        f"`{report.get('remaining_dependabot_prs', 0)}` remaining\n"
        f"Open backlog: `{report.get('open_prs_total', 0)}` PRs "
        f"(`{report.get('open_dependabot_prs_total', 0)}` Dependabot / "
        f"`{report.get('open_non_dependabot_prs_total', 0)}` non-Dependabot), "
        f"`{report.get('open_issues_total', 0)}` issues\n"
        f"Top blockers: {blocker_text}\n"
        f"Run: {workflow_url}"
        f"{error_line}"
    )
    return {"text": summary}


def post_slack_report(webhook_url: str, report: dict[str, Any]) -> dict[str, Any]:
    if not webhook_url:
        return {"status": "not_configured", "ok": True}
    payload = build_slack_payload(report, "ok" if report.get("ok") else "blocked")
    request = Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", errors="replace")
            return {"status": "sent", "ok": 200 <= response.status < 300, "http_status": response.status, "response": body[:120]}
    except Exception as exc:
        return {"status": "post_failed", "ok": False, "error": exc.__class__.__name__}


def self_test() -> int:
    sample = """
| [`merglbot-core/github`](https://github.com/merglbot-core/github) | Shared | GitHub Actions | Active |
| [`merglbot-denatura/denatura-btf-data`](https://github.com/merglbot-denatura/denatura-btf-data) | Old | Python | Archived |
| [`merglbot-public/docs`](https://github.com/merglbot-public/docs) | Docs | Markdown | Active |
"""
    assert parse_repository_map(sample) == ["merglbot-core/github", "merglbot-public/docs"]
    assert classify_change_scope(["package-lock.json", "apps/web/yarn.lock"])[0] is True
    assert classify_change_scope(["apps/web/package.json"])[0] is False
    assert classify_change_scope(["docs/requirements/design.txt"])[0] is False
    assert classify_change_scope(["requirements-dev.txt"])[0] is True
    assert classify_change_scope([".github/workflows/ci.yml"])[0] is False
    assert classify_change_scope(["terraform/main.tf"])[0] is False
    report = build_report(
        "dry-run",
        ["merglbot-core/github"],
        [
            {
                "repo": "merglbot-core/github",
                "ok": True,
                "open_prs_before": 2,
                "open_issues_before": 3,
                "dependabot_prs_before": 1,
                "non_dependabot_prs_before": 1,
                "merged": [],
                "closed": [],
                "blocked": [],
                "would_merge": [{"repo": "merglbot-core/github", "pr_number": 1}],
                "would_close": [],
                "warnings": [],
            }
        ],
        pr_allowlist=set(),
        approval_note="",
        approval_issue_url="",
        workflow_url="https://github.com/o/r/actions/runs/1",
    )
    assert report["repos_scanned"] == 1
    assert report["dependabot_prs_before"] == 1
    assert report["open_non_dependabot_prs_total"] == 1
    assert build_slack_payload(report, "ok")["text"].count("Dependabot") >= 2
    assert parse_pr_allowlist("merglbot-core/github#1 https://github.com/merglbot-public/docs/pull/2") == {
        ("merglbot-core/github", 1),
        ("merglbot-public/docs", 2),
    }
    previous_env = {key: os.environ.get(key) for key in ["GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_ACTOR"]}
    os.environ.update({"GITHUB_SHA": "abc", "GITHUB_RUN_ID": "12345", "GITHUB_ACTOR": "milhul6"})
    try:
        validate_apply_approval(
            "apply",
            {("merglbot-core/github", 1)},
            "post_change_validation=true approved_by=milhul6 approved_at=2026-04-12T21:00:00Z authorized_sha=abc expected_action=merge",
            "",
        )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
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
    parser.add_argument("--slack-notify", action="store_true")
    parser.add_argument("--pr-allowlist", default="")
    parser.add_argument("--approval-note", default="")
    parser.add_argument("--approval-issue-url", default="")
    parser.add_argument("--workflow-url", default=os.environ.get("GITHUB_SERVER_URL", "") + "/" + os.environ.get("GITHUB_REPOSITORY", "") + "/actions/runs/" + os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.mode:
        parser.error("--mode is required unless --self-test is used")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        pr_allowlist = parse_pr_allowlist(args.pr_allowlist)
        validate_apply_approval(args.mode, pr_allowlist, args.approval_note, args.approval_issue_url)
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
                    pr_allowlist=pr_allowlist,
                )
            )
        report = build_report(
            args.mode,
            repos,
            repo_results,
            pr_allowlist=pr_allowlist,
            approval_note=args.approval_note,
            approval_issue_url=args.approval_issue_url,
            workflow_url=args.workflow_url,
        )
        if args.slack_notify:
            report["slack_delivery"] = post_slack_report(os.environ.get("SLACK_DEPENDABOT_WEBHOOK_URL", ""), report)
            if not report["slack_delivery"].get("ok"):
                report["telemetry_degraded"] = True
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
            "mode": args.mode,
            "workflow_url": args.workflow_url,
            "generated_at": utc_now(),
            "error": str(exc),
            "slack_delivery": {"status": "not_requested"},
            "remaining_blockers": [str(exc)],
        }
        if args.slack_notify:
            failure["slack_delivery"] = post_slack_report(os.environ.get("SLACK_DEPENDABOT_WEBHOOK_URL", ""), failure)
        write_json(args.output_dir / "ent_dependabot_weekly_receipt.json", failure)
        print(json.dumps(failure, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
