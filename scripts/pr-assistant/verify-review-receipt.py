#!/usr/bin/env python3
"""Verify the latest Merglbot PR Assistant current-head review receipt.

The script intentionally reads public GitHub PR/comment truth through `gh` and
prints one JSON object. It does not mutate GitHub state.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any


MARKER_RE = re.compile(r"<!--\s*(MERGLBOT_[A-Z0-9_]+)\s*:\s*([\s\S]*?)\s*-->")


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(["gh", *args], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(proc.stdout)


def parse_markers(body: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in MARKER_RE.findall(body or "")}


def latest_receipt(comments: list[dict[str, Any]]) -> tuple[dict[str, str] | None, str | None]:
    for comment in reversed(comments):
        user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
        if user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if "<!-- MERGLBOT_PR_ASSISTANT_V3 -->" not in body:
            continue
        markers = parse_markers(body)
        return markers, str(comment.get("html_url") or comment.get("url") or "")
    return None, None


def verify(repo: str, pr_number: int) -> dict[str, Any]:
    pr = gh_json(["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid,url,state"])
    head_sha = str(pr.get("headRefOid") or "")
    comments_pages = gh_json(["api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr_number}/comments?per_page=100"])
    comments = [
        item
        for page in (comments_pages if isinstance(comments_pages, list) else [])
        for item in (page if isinstance(page, list) else [page])
    ]
    markers, comment_url = latest_receipt(comments)

    blockers: list[str] = []
    if not markers:
        blockers.append("missing_merglbot_review_receipt")
        markers = {}

    review_head_sha = markers.get("MERGLBOT_REVIEW_HEAD_SHA", "")
    verdict = markers.get("MERGLBOT_REVIEW_VERDICT", "")
    status = markers.get("MERGLBOT_REVIEW_STATUS", "")
    schema_version = markers.get("MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION", "")
    pr_check_surface = markers.get("MERGLBOT_PR_CHECK_SURFACE", "")
    run_id = markers.get("MERGLBOT_RUN_ID", "")
    run_url = markers.get("MERGLBOT_RUN_URL", "")

    current_head_match = bool(head_sha and review_head_sha and head_sha == review_head_sha)
    if not current_head_match:
        blockers.append("merglbot_review_head_sha_mismatch")
    if schema_version != "1":
        blockers.append("unsupported_or_missing_receipt_schema")
    if status not in {"success", "blocked", "failed"}:
        blockers.append("missing_or_invalid_review_status")
    if verdict not in {"approved_for_closeout", "changes_required", "blocked_missing_authority", "review_generation_failed"}:
        blockers.append("missing_or_invalid_review_verdict")
    if status == "success" and verdict != "approved_for_closeout":
        blockers.append("review_status_verdict_mismatch")
    if status == "failed" and verdict != "review_generation_failed":
        blockers.append("review_status_verdict_mismatch")
    if pr_check_surface != "verified":
        blockers.append("pr_check_surface_not_verified")
    if not run_id:
        blockers.append("missing_review_run_id")
    if not run_url:
        blockers.append("missing_review_run_url")
    elif run_id and run_url != f"https://github.com/{repo}/actions/runs/{run_id}":
        blockers.append("review_run_url_mismatch")

    return {
        "ok": len(blockers) == 0,
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr.get("url"),
        "head_sha": head_sha or None,
        "review_head_sha": review_head_sha or None,
        "current_head_match": current_head_match,
        "schema_version": schema_version or None,
        "verdict": verdict or None,
        "status": status or None,
        "pr_check_surface": pr_check_surface or None,
        "comment_url": comment_url,
        "run_id": run_id or None,
        "run_url": run_url or None,
        "blockers": blockers,
    }


def self_test() -> int:
    body = "\n".join(
        [
            "<!-- MERGLBOT_PR_ASSISTANT_V3 -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: 42 -->",
            "<!-- MERGLBOT_RUN_URL: https://github.com/o/r/actions/runs/42 -->",
        ]
    )
    markers = parse_markers(body)
    assert markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    assert markers["MERGLBOT_REVIEW_STATUS"] == "success"
    assert markers["MERGLBOT_RUN_ID"] == "42"
    trusted_markers, _ = latest_receipt([{"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}])
    assert trusted_markers and trusted_markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    spoofed_markers, _ = latest_receipt([{"body": body, "user": {"login": "octocat", "type": "User"}}])
    assert spoofed_markers is None
    failed = parse_markers(
        "\n".join(
            [
                "<!-- MERGLBOT_REVIEW_VERDICT: review_generation_failed -->",
                "<!-- MERGLBOT_REVIEW_STATUS: failed -->",
            ]
        )
    )
    assert failed["MERGLBOT_REVIEW_VERDICT"] == "review_generation_failed"
    assert failed["MERGLBOT_REVIEW_STATUS"] == "failed"
    print(json.dumps({"ok": True, "self_test": "passed"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Repository in owner/name form")
    parser.add_argument("--pr", type=int, help="Pull request number")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.repo or not args.pr:
        parser.error("--repo and --pr are required unless --self-test is used")

    result = verify(args.repo, args.pr)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
