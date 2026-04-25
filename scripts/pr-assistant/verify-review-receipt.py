#!/usr/bin/env python3
"""Verify the latest Merglbot PR Assistant current-head review receipt.

The script intentionally reads public GitHub PR/comment truth through `gh` and
prints one JSON object. It does not mutate GitHub state.
"""

# Managed rollout artifact copied into repositories with different Black configs.
# fmt: off

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any

MARKER_RE = re.compile(r"<!--\s*(MERGLBOT_[A-Z0-9_]+)\s*:\s*([\s\S]*?)\s*-->")
SECTION_HEADER_RE = re.compile(r"^#{2,6}\s+")
ZAVER_SECTION_HEADER_RE = re.compile(r"^##\s+")
MACHINE_TOKEN_STRIP_RE = re.compile(r"[^a-z0-9_]+")
PR_ASSISTANT_WORKFLOW_PATHS = {
    ".github/workflows/merglbot-pr-assistant-v3-on-demand.yml",
    ".github/workflows/merglbot-pr-v3-on-demand.yml",
}


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(proc.stdout)


def parse_markers(body: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in MARKER_RE.findall(body or "")}


def normalize_machine_token(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", value.strip().lower())
    normalized = MACHINE_TOKEN_STRIP_RE.sub("", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def normalize_heading(value: str) -> str:
    heading = re.sub(r"^[#\s]+", "", value.strip())
    heading = re.sub(r"[*_`\s]+", "", heading)
    return heading.lower()


def docs_state_blocks_closeout(verdict: str, docs_state: str) -> bool:
    return verdict == "approved_for_closeout" and docs_state in {"missing", "unknown"}


def extract_zaver_field(body: str, field_name: str) -> str:
    in_zaver = False
    in_code = False
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if SECTION_HEADER_RE.match(line):
            heading = normalize_heading(line)
            if (
                not in_zaver
                and ZAVER_SECTION_HEADER_RE.match(line)
                and heading in ("zaver", "závěr")
            ):
                in_zaver = True
                in_code = False
                continue
            if in_zaver:
                break
            continue
        if not in_zaver:
            continue
        cleaned = re.sub(r"^[\s>\-*+]*", "", line)
        parts = cleaned.split(":", 1)
        field_key = (
            parts[0].replace("*", "").replace("_", "").replace("`", "").strip()
            if parts
            else ""
        )
        if len(parts) == 2 and field_key.lower() == field_name.lower():
            return normalize_machine_token(parts[1])
    return ""


def latest_receipt(
    comments: list[dict[str, Any]],
) -> tuple[dict[str, str] | None, str | None, str]:
    for comment in reversed(comments):
        user = comment.get("user")
        if not isinstance(user, dict):
            continue
        if user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if "<!-- MERGLBOT_PR_ASSISTANT_V3 -->" not in body:
            continue
        markers = parse_markers(body)
        return markers, str(comment.get("html_url") or comment.get("url") or ""), body
    return None, None, ""


def expected_run_url(pr_url: str, run_id: str) -> str:
    if "/pull/" not in pr_url:
        return ""
    return f"{pr_url.split('/pull/', 1)[0]}/actions/runs/{run_id}"


def verify(repo: str, pr_number: int) -> dict[str, Any]:
    pr = gh_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid,url,state"]
    )
    head_sha = str(pr.get("headRefOid") or "")
    comments_pages = gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
        ]
    )
    comments = [
        item
        for page in (comments_pages if isinstance(comments_pages, list) else [])
        for item in (page if isinstance(page, list) else [page])
    ]
    markers, comment_url, receipt_body = latest_receipt(comments)

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
    docs_state_marker_present = "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE" in markers
    docs_state = markers.get("MERGLBOT_DOCUMENTATION_OBLIGATION_STATE", "")

    current_head_match = bool(
        head_sha and review_head_sha and head_sha == review_head_sha
    )
    if not current_head_match:
        blockers.append("merglbot_review_head_sha_mismatch")
    if schema_version != "1":
        blockers.append("unsupported_or_missing_receipt_schema")
    if status not in {"success", "blocked", "failed"}:
        blockers.append("missing_or_invalid_review_status")
    valid_verdicts = {
        "approved_for_closeout",
        "changes_required",
        "blocked_missing_authority",
        "review_generation_failed",
    }
    if verdict not in valid_verdicts:
        blockers.append("missing_or_invalid_review_verdict")
    visible_verdict = extract_zaver_field(receipt_body, "Verdict")
    if visible_verdict in valid_verdicts and verdict and visible_verdict != verdict:
        blockers.append("review_visible_verdict_marker_mismatch")
    valid_docs_states = {"satisfied", "not_required", "missing", "unknown"}
    if not docs_state_marker_present:
        docs_state = "unknown"
        blockers.append("missing_or_invalid_documentation_obligation_state")
    elif docs_state not in valid_docs_states:
        blockers.append("missing_or_invalid_documentation_obligation_state")
    visible_docs_state = extract_zaver_field(receipt_body, "Documentation Obligation State")
    if visible_docs_state in valid_docs_states and docs_state and visible_docs_state != docs_state:
        blockers.append("review_visible_docs_state_marker_mismatch")
    if docs_state_blocks_closeout(verdict, docs_state):
        blockers.append("review_docs_state_blocks_closeout")
    if status != "success" or verdict != "approved_for_closeout":
        blockers.append("review_not_approved_for_closeout")
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
    elif run_id and run_url != expected_run_url(str(pr.get("url") or ""), run_id):
        blockers.append("review_run_url_mismatch")
    run_path = ""
    if run_id:
        try:
            run = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
            run_path = str(run.get("path") or "")
        except Exception as exc:  # pragma: no cover - exercised through live CLI usage.
            blockers.append(f"review_run_lookup_failed:{exc}")
        if run_path and run_path not in PR_ASSISTANT_WORKFLOW_PATHS:
            blockers.append("review_run_not_from_pr_assistant_workflow")

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
        "documentation_obligation_state": docs_state or None,
        "pr_check_surface": pr_check_surface or None,
        "comment_url": comment_url,
        "run_id": run_id or None,
        "run_url": run_url or None,
        "run_path": run_path or None,
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
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: not_required -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: 42 -->",
            "<!-- MERGLBOT_RUN_URL: https://github.com/o/r/actions/runs/42 -->",
        ]
    )
    markers = parse_markers(body)
    assert markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    assert markers["MERGLBOT_REVIEW_STATUS"] == "success"
    assert markers["MERGLBOT_RUN_ID"] == "42"
    trusted_markers, _, trusted_body = latest_receipt(
        [{"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}]
    )
    assert trusted_markers and trusted_markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    assert extract_zaver_field(trusted_body, "Verdict") == ""
    assert normalize_machine_token("Review V4 Failed!") == "review_v4_failed"
    assert normalize_machine_token("approved-for-closeout") == "approved_for_closeout"
    assert normalize_machine_token("approved\tfor\ncloseout") == "approved_for_closeout"
    assert docs_state_blocks_closeout("approved_for_closeout", "missing")
    assert docs_state_blocks_closeout("approved_for_closeout", "unknown")
    assert not docs_state_blocks_closeout("approved_for_closeout", "not_required")
    assert not docs_state_blocks_closeout("changes_required", "unknown")
    assert (
        extract_zaver_field("## Zaver\n_Verdict_: approved-for-closeout", "Verdict")
        == "approved_for_closeout"
    )
    assert extract_zaver_field("### Zaver\n* _Verdict_ : approved-for-closeout", "Verdict") == ""
    assert (
        extract_zaver_field(
            "## Zaver\n### Details\nVerdict: approved_for_closeout",
            "Verdict",
        )
        == ""
    )
    assert (
        extract_zaver_field(
            "\n".join(
                [
                    "```markdown",
                    "## Zaver",
                    "Verdict: changes_required",
                    "```",
                    "## Zaver",
                    "```",
                    "## Spoofed",
                    "Verdict: changes_required",
                    "```",
                    "Verdict: approved_for_closeout",
                ]
            ),
            "Verdict",
        )
        == "approved_for_closeout"
    )
    assert (
        extract_zaver_field(
            "\n".join(
                [
                    "~~~markdown",
                    "## Zaver",
                    "Verdict: changes_required",
                    "~~~",
                    "## Zaver",
                    "Verdict: approved_for_closeout",
                ]
            ),
            "Verdict",
        )
        == "approved_for_closeout"
    )
    spoofed_markers, _, _ = latest_receipt(
        [{"body": body, "user": {"login": "octocat", "type": "User"}}]
    )
    assert spoofed_markers is None
    mismatched_body = "\n".join(
        [
            "## **Zaver**",
            "Verdict: approved_for_closeout",
            "",
            "<!-- MERGLBOT_PR_ASSISTANT_V3 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: blocked_missing_authority -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: unknown -->",
        ]
    )
    assert extract_zaver_field(mismatched_body, "Verdict") == "approved_for_closeout"
    mismatched_markers = parse_markers(mismatched_body)
    assert mismatched_markers["MERGLBOT_REVIEW_VERDICT"] != extract_zaver_field(
        mismatched_body,
        "Verdict",
    )
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
    missing_docs_state_markers = parse_markers(
        "\n".join(
            [
                "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
                "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            ]
        )
    )
    docs_state_marker_present = (
        "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE" in missing_docs_state_markers
    )
    docs_state = missing_docs_state_markers.get(
        "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE",
        "",
    )
    blockers = []
    if not docs_state_marker_present:
        docs_state = "unknown"
        blockers.append("missing_or_invalid_documentation_obligation_state")
    if docs_state_blocks_closeout(
        missing_docs_state_markers["MERGLBOT_REVIEW_VERDICT"],
        docs_state,
    ):
        blockers.append("review_docs_state_blocks_closeout")
    assert blockers == [
        "missing_or_invalid_documentation_obligation_state",
        "review_docs_state_blocks_closeout",
    ]
    blockers: list[str] = []
    status = "blocked"
    verdict = "changes_required"
    if status != "success" or verdict != "approved_for_closeout":
        blockers.append("review_not_approved_for_closeout")
    assert blockers == ["review_not_approved_for_closeout"]
    assert expected_run_url("https://github.enterprise.example/o/r/pull/42", "123") == (
        "https://github.enterprise.example/o/r/actions/runs/123"
    )
    assert (
        ".github/workflows/merglbot-pr-assistant-v3-on-demand.yml"
        in PR_ASSISTANT_WORKFLOW_PATHS
    )
    assert (
        ".github/workflows/merglbot-pr-v3-on-demand.yml" in PR_ASSISTANT_WORKFLOW_PATHS
    )
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
