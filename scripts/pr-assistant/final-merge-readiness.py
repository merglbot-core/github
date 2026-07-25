#!/usr/bin/env python3
"""Evaluate the Merglbot Final Merge Readiness policy for a pull request.

The command is intentionally read-only against GitHub. It consumes branch
protection, PR checks, changed paths, and PR Assistant receipt comments, then
prints and optionally writes one JSON receipt.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import pathlib
import re
import subprocess
from typing import Any, Callable


DEFAULT_POLICY_PATH = ".github/policies/final-merge-readiness.json"
MARKER_RE = re.compile(r"<!--\s*(MERGLBOT_[A-Z0-9_]+)\s*:\s*([\s\S]*?)\s*-->")
SECTION_HEADER_RE = re.compile(r"^#{2,6}\s+")
ZAVER_SECTION_HEADER_RE = re.compile(r"^##\s+")
MACHINE_TOKEN_STRIP_RE = re.compile(r"[^a-z0-9_]+")
TEXT_SCAN_LIMIT_BYTES = 1_000_000


class PolicyError(RuntimeError):
    """Raised when live GitHub state cannot be collected safely."""


def now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise PolicyError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"gh {' '.join(args)} returned invalid JSON") from exc


def flatten_paginated_slurp(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    flattened: list[Any] = []
    for page in value:
        if isinstance(page, list):
            flattened.extend(page)
        else:
            flattened.append(page)
    return flattened


def parse_markers(body: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in MARKER_RE.findall(body or "")}


def normalize_machine_token(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", value.strip().lower())
    normalized = MACHINE_TOKEN_STRIP_RE.sub("", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


def normalize_heading(value: str) -> str:
    heading = re.sub(r"^[#\s]+", "", value.strip())
    heading = re.sub(r"[*_`\s]+", "", heading)
    return heading.lower()


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
                and heading in {"zaver", "z\u00e1v\u011br"}
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


def path_matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def is_docs_path(path: str, policy: dict[str, Any]) -> bool:
    docs_patterns = policy.get("path_risk", {}).get("docs_only_patterns", [])
    return path_matches(path, docs_patterns)


def trusted_comment(comment: dict[str, Any], trusted_logins: set[str]) -> bool:
    user = comment.get("user")
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "")
    user_type = str(user.get("type") or "")
    return login in trusted_logins and user_type == "Bot"


def sorted_comments(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        comments,
        key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""),
    )


def latest_assistant_receipt(
    comments: list[dict[str, Any]],
    check_runs: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[dict[str, str] | None, str, str, str, str, bool]:
    assistant = policy["pr_assistant"]
    marker_names = list(assistant["allowed_markers"])
    trusted_logins = set(assistant["trusted_comment_logins"])
    check_names = set(assistant.get("allowed_check_names", []))
    candidates: list[dict[str, Any]] = []

    def marker_name_for_body(body: str, fallback_name: str = "") -> str:
        marker_name = next(
            (
                marker
                for marker in marker_names
                if re.search(rf"<!--\s*{re.escape(marker)}(?:\s*:|\s*-->)", body)
            ),
            "",
        )
        if marker_name:
            return marker_name
        if fallback_name in check_names:
            return str(assistant.get("check_name_markers", {}).get(fallback_name) or "")
        return ""

    for comment in sorted_comments(comments):
        if not trusted_comment(comment, trusted_logins):
            continue
        body = str(comment.get("body") or "")
        marker_name = marker_name_for_body(body)
        if not marker_name:
            continue
        candidates.append(
            {
                "markers": parse_markers(body),
                "url": str(comment.get("html_url") or comment.get("url") or ""),
                "body": body,
                "marker_name": marker_name,
                "source": "comment",
                "source_current_head": False,
                "sort_key": str(comment.get("created_at") or comment.get("updated_at") or ""),
            }
        )

    for run in sorted(
        check_runs,
        key=lambda item: str(
            item.get("completed_at")
            or item.get("started_at")
            or item.get("created_at")
            or item.get("updated_at")
            or ""
        ),
    ):
        name = status_check_name(run)
        output = run.get("output") if isinstance(run.get("output"), dict) else {}
        body = str(output.get("summary") or "")
        marker_name = marker_name_for_body(body, name)
        if not marker_name:
            continue
        candidates.append(
            {
                "markers": parse_markers(body),
                "url": str(run.get("html_url") or run.get("details_url") or ""),
                "body": body,
                "marker_name": marker_name,
                "source": "check_run",
                "source_current_head": True,
                "sort_key": str(
                    run.get("completed_at")
                    or run.get("started_at")
                    or run.get("created_at")
                    or run.get("updated_at")
                    or ""
                ),
            }
        )

    if not candidates:
        return None, "", "", "", "", False

    head_sha = str(policy.get("_current_head_sha") or "")
    marker_priority = {marker: index for index, marker in enumerate(marker_names)}

    def candidate_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
        marker_name = str(candidate.get("marker_name") or "")
        current_head_match = str(candidate.get("markers", {}).get("MERGLBOT_REVIEW_HEAD_SHA") or "") == head_sha
        current_head_v5 = marker_name == "MERGLBOT_PR_ASSISTANT_V5" and bool(candidate.get("source_current_head"))
        return (
            2 if current_head_v5 else 1 if current_head_match else 0,
            len(marker_names) - marker_priority.get(marker_name, len(marker_names)),
            str(candidate.get("sort_key") or ""),
        )

    selected = max(candidates, key=candidate_key)
    return (
        selected["markers"],
        selected["url"],
        selected["body"],
        selected["marker_name"],
        selected["source"],
        bool(selected["source_current_head"]),
    )


def expected_run_url(pr_url: str, run_id: str) -> str:
    if "/pull/" not in pr_url or not run_id:
        return ""
    return f"{pr_url.split('/pull/', 1)[0]}/actions/runs/{run_id}"


def marker_set(assistant: dict[str, Any], key: str) -> set[str]:
    value = assistant.get(key, [])
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def run_id_matches_prefix(run_id: str, prefixes: set[str]) -> bool:
    return bool(run_id) and any(run_id.startswith(prefix) for prefix in prefixes)


def add_decision(
    decisions: list[dict[str, Any]],
    decision_id: str,
    status: str,
    summary: str,
    *,
    evidence: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
) -> None:
    decision: dict[str, Any] = {
        "id": decision_id,
        "status": status,
        "summary": summary,
    }
    if evidence is not None:
        decision["evidence"] = evidence
    if blockers:
        decision["blockers"] = blockers
    decisions.append(decision)


def status_check_name(check: dict[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "")


def check_passed(check: dict[str, Any], success_conclusions: set[str]) -> bool:
    typename = check.get("__typename")
    if typename == "StatusContext" or "state" in check:
        return str(check.get("state") or "").upper() == "SUCCESS"
    status = str(check.get("status") or "").upper()
    conclusion = str(check.get("conclusion") or "").lower()
    return status == "COMPLETED" and conclusion in success_conclusions


def check_blocked(check: dict[str, Any], blocking_conclusions: set[str]) -> bool:
    typename = check.get("__typename")
    if typename == "StatusContext" or "state" in check:
        return str(check.get("state") or "").upper() in {"ERROR", "FAILURE"}
    conclusion = str(check.get("conclusion") or "").lower()
    return conclusion in blocking_conclusions


def local_content_loader(path: str) -> str:
    return content_loader_for_root(pathlib.Path.cwd())(path)


def content_loader_for_root(root: pathlib.Path) -> Callable[[str], str]:
    resolved_root = root.resolve()

    def load(path: str) -> str:
        rel = pathlib.PurePosixPath(path)
        if rel.is_absolute() or ".." in rel.parts:
            return ""
        candidate = (resolved_root / pathlib.Path(*rel.parts)).resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            return ""
        return read_text_candidate(candidate)

    return load


def read_text_candidate(candidate: pathlib.Path) -> str:
    if not candidate.is_file():
        return ""
    try:
        if candidate.stat().st_size > TEXT_SCAN_LIMIT_BYTES:
            return ""
        return candidate.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def content_policy_hits(
    changed_paths: list[str],
    policy: dict[str, Any],
    content_loader: Callable[[str], str] | None,
) -> list[dict[str, str]]:
    if content_loader is None:
        return []
    hits: list[dict[str, str]] = []
    for path in changed_paths:
        text = content_loader(path)
        if not text:
            continue
        for rule in policy.get("path_risk", {}).get("forbidden_content_patterns", []):
            pattern = str(rule.get("pattern") or "")
            flags = re.IGNORECASE if rule.get("ignore_case", True) else 0
            if pattern and re.search(pattern, text, flags):
                hits.append({"path": path, "rule_id": str(rule.get("id") or "unnamed_rule")})
    return hits


def evaluate_path_risk(
    policy: dict[str, Any],
    changed_paths: list[str],
    decisions: list[dict[str, Any]],
    content_loader: Callable[[str], str] | None,
) -> tuple[list[str], list[str], bool]:
    path_policy = policy["path_risk"]
    forbidden_patterns = list(path_policy.get("forbidden_path_patterns", []))
    high_risk_patterns = list(path_policy.get("high_risk_patterns", []))
    forbidden_paths = [path for path in changed_paths if path_matches(path, forbidden_patterns)]
    high_risk_paths = [path for path in changed_paths if path_matches(path, high_risk_patterns)]
    content_hits = content_policy_hits(changed_paths, policy, content_loader)
    blockers = [f"forbidden_path:{path}" for path in forbidden_paths]
    blockers.extend(f"forbidden_content:{hit['rule_id']}:{hit['path']}" for hit in content_hits)
    docs_only = bool(changed_paths) and all(is_docs_path(path, policy) for path in changed_paths)
    evidence = {
        "changed_path_count": len(changed_paths),
        "docs_only": docs_only,
        "high_risk_paths": high_risk_paths,
        "forbidden_paths": forbidden_paths,
        "forbidden_content_hits": content_hits,
    }
    add_decision(
        decisions,
        "path_risk_gate",
        "fail" if blockers else "pass",
        "Changed paths were classified against forbidden and high-risk policy patterns.",
        evidence=evidence,
        blockers=blockers,
    )
    return blockers, high_risk_paths, docs_only


def evaluate_pr_assistant(
    policy: dict[str, Any],
    pr: dict[str, Any],
    comments: list[dict[str, Any]],
    check_runs: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    requires_assistant: bool,
    run_lookup: Callable[[str], dict[str, Any] | None] | None,
) -> list[str]:
    if not requires_assistant:
        add_decision(
            decisions,
            "pr_assistant_review_only_evidence",
            "skip",
            "PR Assistant receipt is not required for docs-only path risk.",
            evidence={"required": False},
        )
        return []

    assistant = policy["pr_assistant"]
    head_sha = str(pr.get("headRefOid") or "")
    policy_with_head = {**policy, "_current_head_sha": head_sha}
    markers, comment_url, body, marker_name, evidence_source, source_current_head = latest_assistant_receipt(
        comments,
        check_runs,
        policy_with_head,
    )
    blockers: list[str] = []
    if not markers:
        blockers.append("missing_pr_assistant_receipt")
        blockers.append("missing_current_head_approved_v5_review")
        add_decision(
            decisions,
            "pr_assistant_review_only_evidence",
            "fail",
            "No trusted PR Assistant receipt comment or check-run summary was found.",
            evidence={
                "required": True,
                "marker": marker_name or None,
                "comment_url": comment_url or None,
                "evidence_source": evidence_source or None,
                "source_current_head": source_current_head,
            },
            blockers=blockers,
        )
        return blockers

    pr_url = str(pr.get("url") or "")
    review_head_sha = markers.get("MERGLBOT_REVIEW_HEAD_SHA", "")
    schema_version = markers.get("MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION", "")
    boundary = markers.get("MERGLBOT_REVIEW_BOUNDARY", "")
    verdict = markers.get("MERGLBOT_REVIEW_VERDICT", "")
    status = markers.get("MERGLBOT_REVIEW_STATUS", "")
    docs_state = markers.get("MERGLBOT_DOCUMENTATION_OBLIGATION_STATE", "")
    provider_degraded = normalize_machine_token(markers.get("MERGLBOT_PROVIDER_DEGRADED", ""))
    closeout_mode = markers.get("MERGLBOT_CLOSEOUT_MODE", "")
    pr_check_surface = markers.get("MERGLBOT_PR_CHECK_SURFACE", "")
    run_id = markers.get("MERGLBOT_RUN_ID", "")
    run_url = markers.get("MERGLBOT_RUN_URL", "")
    runtime_type = markers.get("MERGLBOT_RUNTIME_TYPE", "")
    visible_verdict = extract_zaver_field(body, "Verdict")
    visible_docs_state = extract_zaver_field(body, "Documentation Obligation State")
    external_runtime_types = marker_set(assistant, "external_runtime_types")
    external_run_id_prefixes = marker_set(assistant, "external_run_id_prefixes")
    boundary_optional_runtime_types = marker_set(assistant, "review_boundary_optional_runtime_types")
    is_external_runtime = runtime_type in external_runtime_types
    boundary_optional = runtime_type in boundary_optional_runtime_types

    if schema_version not in set(assistant["required_receipt_schema_versions"]):
        blockers.append("unsupported_or_missing_receipt_schema")
    if review_head_sha != head_sha:
        blockers.append("pr_assistant_head_sha_mismatch")
    if assistant.get("review_boundary_required", True) and boundary != "review_only" and not boundary_optional:
        blockers.append("pr_assistant_boundary_not_review_only")
    if closeout_mode != assistant["required_closeout_mode"]:
        blockers.append("pr_assistant_closeout_mode_not_human_merge_only")
    if status not in set(assistant["successful_statuses"]):
        blockers.append("pr_assistant_status_not_success")
    if verdict not in set(assistant["approved_verdicts"]):
        blockers.append("pr_assistant_verdict_not_approved")
    if marker_name == "MERGLBOT_PR_ASSISTANT_V5" and provider_degraded != "false":
        blockers.append("pr_assistant_provider_degraded_not_false")
    if docs_state not in set(assistant["passing_documentation_states"]):
        blockers.append("pr_assistant_documentation_state_blocks_closeout")
    if pr_check_surface != assistant["required_pr_check_surface"]:
        blockers.append("pr_assistant_check_surface_not_verified")
    if visible_verdict and visible_verdict != verdict:
        blockers.append("pr_assistant_visible_verdict_marker_mismatch")
    if visible_docs_state and visible_docs_state != docs_state:
        blockers.append("pr_assistant_visible_docs_state_marker_mismatch")
    if not run_id:
        blockers.append("missing_pr_assistant_run_id")
    elif is_external_runtime:
        if not run_id_matches_prefix(run_id, external_run_id_prefixes):
            blockers.append("pr_assistant_external_run_id_not_allowed")
    elif run_url != expected_run_url(pr_url, run_id):
        blockers.append("pr_assistant_run_url_mismatch")

    run_path = ""
    if run_id and not is_external_runtime and run_lookup is not None:
        run_lookup_failed = False
        try:
            run = run_lookup(run_id)
        except Exception as exc:  # pragma: no cover - live API failure path.
            run = None
            run_lookup_failed = True
            blockers.append(f"pr_assistant_run_lookup_failed:{exc}")
        if run:
            run_path = str(run.get("path") or "")
            if run_path not in set(assistant["allowed_workflow_paths"]):
                blockers.append("pr_assistant_run_workflow_not_allowed")
        elif not run_lookup_failed:
            blockers.append("pr_assistant_run_lookup_missing")

    if (
        marker_name == "MERGLBOT_PR_ASSISTANT_V5"
        and source_current_head
        and (
            review_head_sha != head_sha
            or status not in set(assistant["successful_statuses"])
            or verdict not in set(assistant["approved_verdicts"])
            or provider_degraded != "false"
        )
    ):
        blockers.append("missing_current_head_approved_v5_review")

    add_decision(
        decisions,
        "pr_assistant_review_only_evidence",
        "fail" if blockers else "pass",
        "Latest trusted PR Assistant receipt was parsed as review-only merge evidence.",
        evidence={
            "required": True,
            "marker": marker_name,
            "comment_url": comment_url,
            "evidence_source": evidence_source,
            "source_current_head": source_current_head,
            "schema_version": schema_version or None,
            "review_head_sha": review_head_sha or None,
            "verdict": verdict or None,
            "status": status or None,
            "documentation_obligation_state": docs_state or None,
            "provider_degraded": provider_degraded or None,
            "closeout_mode": closeout_mode or None,
            "pr_check_surface": pr_check_surface or None,
            "run_id": run_id or None,
            "run_url": run_url or None,
            "runtime_type": runtime_type or None,
            "run_path": run_path or None,
        },
        blockers=blockers,
    )
    return blockers


def evaluate_required_checks(
    policy: dict[str, Any],
    pr: dict[str, Any],
    required_contexts: list[str],
    decisions: list[dict[str, Any]],
) -> list[str]:
    check_policy = policy["required_checks"]
    success_conclusions = set(check_policy["success_conclusions"])
    blocking_conclusions = set(check_policy["blocking_conclusions"])
    self_check_names = set(check_policy.get("self_check_names", []))
    rollup = pr.get("statusCheckRollup") or []
    if not isinstance(rollup, list):
        rollup = []
    check_by_name = {status_check_name(check): check for check in rollup if status_check_name(check)}
    required = [context for context in required_contexts if context not in self_check_names]
    blockers: list[str] = []
    for context in required:
        check = check_by_name.get(context)
        if not check:
            blockers.append(f"required_check_missing:{context}")
        elif not check_passed(check, success_conclusions):
            blockers.append(f"required_check_not_success:{context}")

    if check_policy.get("block_failed_non_required_checks", True):
        for check in rollup:
            name = status_check_name(check)
            if not name or name in self_check_names or name in required:
                continue
            if check_blocked(check, blocking_conclusions):
                blockers.append(f"non_required_check_failed:{name}")

    add_decision(
        decisions,
        "required_checks_gate",
        "fail" if blockers else "pass",
        "Branch-protection required checks were evaluated on the current PR head.",
        evidence={
            "required_contexts": required,
            "observed_checks": sorted(check_by_name),
            "self_check_names": sorted(self_check_names),
        },
        blockers=blockers,
    )
    return blockers


def evaluate_draft_state(pr: dict[str, Any], decisions: list[dict[str, Any]]) -> list[str]:
    blockers = ["pr_is_draft"] if pr.get("isDraft") else []
    add_decision(
        decisions,
        "draft_state_gate",
        "fail" if blockers else "pass",
        "Draft pull requests are not final-merge-ready.",
        evidence={"is_draft": bool(pr.get("isDraft"))},
        blockers=blockers,
    )
    return blockers


def evaluate(
    *,
    policy: dict[str, Any],
    pr: dict[str, Any],
    comments: list[dict[str, Any]],
    changed_paths: list[str],
    required_contexts: list[str],
    check_runs: list[dict[str, Any]] | None = None,
    run_lookup: Callable[[str], dict[str, Any] | None] | None = None,
    content_loader: Callable[[str], str] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    blockers: list[str] = []
    path_blockers, high_risk_paths, docs_only = evaluate_path_risk(
        policy,
        changed_paths,
        decisions,
        content_loader,
    )
    blockers.extend(path_blockers)
    requires_assistant = not docs_only or bool(high_risk_paths)
    blockers.extend(
        evaluate_pr_assistant(
            policy,
            pr,
            comments,
            check_runs or [],
            decisions,
            requires_assistant,
            run_lookup,
        )
    )
    blockers.extend(evaluate_required_checks(policy, pr, required_contexts, decisions))
    blockers.extend(evaluate_draft_state(pr, decisions))

    return {
        "schema_version": 1,
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
        "evaluated_at": evaluated_at or now_iso(),
        "repo": pr.get("repository"),
        "pr_number": pr.get("number"),
        "pr_url": pr.get("url"),
        "head_sha": pr.get("headRefOid"),
        "base_ref": pr.get("baseRefName"),
        "ok": not blockers,
        "blockers": blockers,
        "decisions": decisions,
    }


def branch_required_contexts(repo: str, base_ref: str, fallback: list[str]) -> list[str]:
    try:
        required = gh_json(
            ["api", f"repos/{repo}/branches/{base_ref}/protection/required_status_checks"]
        )
    except PolicyError:
        return fallback
    contexts = set(str(context) for context in required.get("contexts") or [])
    for check in required.get("checks") or []:
        context = check.get("context")
        if context:
            contexts.add(str(context))
    return sorted(contexts) if contexts else fallback


def collect_live_context(repo: str, pr_number: int, policy: dict[str, Any]) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    list[str],
    Callable[[str], dict[str, Any] | None],
]:
    fields = ",".join(
        [
            "baseRefName",
            "headRefName",
            "headRefOid",
            "isDraft",
            "mergeStateStatus",
            "reviewDecision",
            "statusCheckRollup",
            "url",
        ]
    )
    pr = gh_json(["pr", "view", str(pr_number), "--repo", repo, "--json", fields])
    pr["repository"] = repo
    pr["number"] = pr_number

    comments = flatten_paginated_slurp(
        gh_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
            ]
        )
    )
    check_runs_payload = gh_json(
        [
            "api",
            f"repos/{repo}/commits/{pr['headRefOid']}/check-runs?per_page=100",
        ]
    )
    check_runs = check_runs_payload.get("check_runs") or []
    files = flatten_paginated_slurp(
        gh_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repo}/pulls/{pr_number}/files?per_page=100",
            ]
        )
    )
    changed_paths = [str(item.get("filename") or "") for item in files if item.get("filename")]
    fallback = list(policy["required_checks"].get("fallback_required_contexts", []))
    required_contexts = branch_required_contexts(repo, str(pr.get("baseRefName") or "main"), fallback)
    run_cache: dict[str, dict[str, Any] | None] = {}

    def run_lookup(run_id: str) -> dict[str, Any] | None:
        if not run_id or not run_id.isdigit():
            return None
        if run_id not in run_cache:
            run_cache[run_id] = gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
        return run_cache[run_id]

    return pr, comments, check_runs, changed_paths, required_contexts, run_lookup


def validate_policy(policy: dict[str, Any]) -> None:
    required_root = {
        "schema_version",
        "policy_id",
        "policy_version",
        "mode",
        "scope_baseline",
        "pr_assistant",
        "required_checks",
        "path_risk",
    }
    missing = sorted(required_root - set(policy))
    if missing:
        raise SystemExit(f"Policy missing root keys: {', '.join(missing)}")
    if policy["mode"] != "human_merge_only":
        raise SystemExit("Final Merge Readiness policy mode must stay human_merge_only")
    if policy["schema_version"] != 1:
        raise SystemExit("Unsupported Final Merge Readiness policy schema_version")
    if policy["scope_baseline"].get("github_active_repo_denominator") != 46:
        raise SystemExit("Scope baseline must keep the 46 active GitHub repo denominator")


def self_test() -> int:
    policy = load_json(pathlib.Path(DEFAULT_POLICY_PATH))
    validate_policy(policy)
    pr = {
        "repository": "merglbot-core/github",
        "number": 42,
        "url": "https://github.com/merglbot-core/github/pull/42",
        "headRefOid": "abc123",
        "baseRefName": "main",
        "isDraft": False,
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "ci",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            }
        ],
    }
    body = "\n".join(
        [
            "## Zaver",
            "Verdict: approved_for_closeout",
            "Documentation Obligation State: satisfied",
            "",
            "<!-- MERGLBOT_PR_ASSISTANT_V4 -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_REVIEW_BOUNDARY: review_only -->",
            "<!-- MERGLBOT_FOLLOW_UP_ID: pr-42-review-100 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: satisfied -->",
            "<!-- MERGLBOT_CLOSEOUT_MODE: human_merge_only -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: 100 -->",
            "<!-- MERGLBOT_RUN_URL: https://github.com/merglbot-core/github/actions/runs/100 -->",
        ]
    )
    comments = [
        {
            "body": body,
            "created_at": "2026-05-01T00:00:00Z",
            "html_url": "https://github.com/merglbot-core/github/pull/42#issuecomment-1",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
    ]
    run_lookup = lambda run_id: {"path": ".github/workflows/merglbot-pr-assistant-v4-on-demand.yml"}
    receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py", "docs/pr-assistant/final-merge-readiness.md"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "print('ok')",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert receipt["ok"], receipt

    stale = body.replace("MERGLBOT_REVIEW_HEAD_SHA: abc123", "MERGLBOT_REVIEW_HEAD_SHA: def456")
    stale_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[{**comments[0], "body": stale}],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "pr_assistant_head_sha_mismatch" in stale_receipt["blockers"]

    spoofed_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[{**comments[0], "user": {"login": "octocat", "type": "User"}}],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "missing_pr_assistant_receipt" in spoofed_receipt["blockers"]
    assert "missing_current_head_approved_v5_review" in spoofed_receipt["blockers"]

    cloud_body = "\n".join(
        [
            "Verdict: approved_for_closeout",
            "Documentation Obligation State: satisfied",
            "",
            "<!-- MERGLBOT_PR_ASSISTANT_V4 -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_FOLLOW_UP_ID: pr-assistant-v4:100 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: satisfied -->",
            "<!-- MERGLBOT_CLOSEOUT_MODE: human_merge_only -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: pr-assistant-v4:100 -->",
            "<!-- MERGLBOT_RUNTIME_TYPE: github_app_cloud_run -->",
        ]
    )
    cloud_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[
            {
                **comments[0],
                "body": cloud_body,
                "user": {"login": "merglbot-pr-assistant-v4-stg[bot]", "type": "Bot"},
            }
        ],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=lambda run_id: (_ for _ in ()).throw(AssertionError("cloud receipts must not call actions run lookup")),
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert cloud_receipt["ok"], cloud_receipt

    degraded_cloud_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[
            {
                **comments[0],
                "body": cloud_body.replace("MERGLBOT_REVIEW_STATUS: success", "MERGLBOT_REVIEW_STATUS: degraded"),
                "user": {"login": "merglbot-pr-assistant-v4-stg[bot]", "type": "Bot"},
            }
        ],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=None,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "pr_assistant_status_not_success" in degraded_cloud_receipt["blockers"]

    docs_only_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[],
        changed_paths=["docs/pr-assistant/final-merge-readiness.md"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert docs_only_receipt["ok"], docs_only_receipt

    missing_check_receipt = evaluate(
        policy=policy,
        pr={**pr, "statusCheckRollup": []},
        comments=comments,
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "required_check_missing:ci" in missing_check_receipt["blockers"]

    draft_receipt = evaluate(
        policy=policy,
        pr={**pr, "isDraft": True},
        comments=comments,
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "pr_is_draft" in draft_receipt["blockers"]

    failed_non_required_check_receipt = evaluate(
        policy=policy,
        pr={
            **pr,
            "statusCheckRollup": [
                *pr["statusCheckRollup"],
                {
                    "__typename": "CheckRun",
                    "name": "lint-extra",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
            ],
        },
        comments=comments,
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "non_required_check_failed:lint-extra" in failed_non_required_check_receipt["blockers"]

    forbidden_path_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=["service-account.json"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "forbidden_path:service-account.json" in forbidden_path_receipt["blockers"]

    terraform_apply_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=["infra/main.tf"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "terraform " + "apply -auto-approve",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert any(
        blocker.startswith("forbidden_content:terraform_apply:infra/main.tf")
        for blocker in terraform_apply_receipt["blockers"]
    )

    admin_bypass_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=[".github/workflows/release.yml"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: "echo 'bypass branch " + "protection'",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert any(
        blocker.startswith("forbidden_content:branch_protection_admin_bypass:.github/workflows/release.yml")
        for blocker in admin_bypass_receipt["blockers"]
    )

    private_key_label = "PRIVATE" + " KEY"
    encrypted_key_header = "-" * 5 + "BEGIN ENCRYPTED " + private_key_label + "-" * 5
    encrypted_key_footer = "-" * 5 + "END ENCRYPTED " + private_key_label + "-" * 5
    encrypted_private_key_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        run_lookup=run_lookup,
        content_loader=lambda path: f"{encrypted_key_header}\nredacted\n{encrypted_key_footer}",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert any(
        blocker.startswith("forbidden_content:private_key_material:scripts/pr-assistant/final-merge-readiness.py")
        for blocker in encrypted_private_key_receipt["blockers"]
    )

    v5_body = "\n".join(
        [
            "<!-- MERGLBOT_PR_ASSISTANT_V5 -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_FOLLOW_UP_ID: pr-assistant-v5:100 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_PROVIDER_DEGRADED: false -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: satisfied -->",
            "<!-- MERGLBOT_CLOSEOUT_MODE: human_merge_only -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: pr-assistant-v5:100 -->",
            "<!-- MERGLBOT_RUNTIME_TYPE: github_app_cloud_run -->",
        ]
    )
    stale_comment = {**comments[0], "body": stale, "created_at": "2026-05-01T00:05:00Z"}
    current_head_v5_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[stale_comment],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        check_runs=[
            {
                "name": "Merglbot PR Assistant v5",
                "completed_at": "2026-05-01T00:01:00Z",
                "html_url": "https://github.com/merglbot-core/github/runs/100",
                "output": {"summary": v5_body},
            }
        ],
        run_lookup=lambda run_id: (_ for _ in ()).throw(AssertionError("v5 cloud receipts must not call actions run lookup")),
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert current_head_v5_receipt["ok"], current_head_v5_receipt

    invalid_v5_receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=[],
        changed_paths=["scripts/pr-assistant/final-merge-readiness.py"],
        required_contexts=["ci"],
        check_runs=[
            {
                "name": "Merglbot PR Assistant v5",
                "completed_at": "2026-05-01T00:01:00Z",
                "html_url": "https://github.com/merglbot-core/github/runs/101",
                "output": {
                    "summary": v5_body.replace(
                        "MERGLBOT_REVIEW_HEAD_SHA: abc123",
                        "MERGLBOT_REVIEW_HEAD_SHA: invalid_head_sha",
                    )
                },
            }
        ],
        run_lookup=None,
        content_loader=lambda path: "",
        evaluated_at="2026-05-01T00:00:00Z",
    )
    assert "missing_current_head_approved_v5_review" in invalid_v5_receipt["blockers"]

    print(json.dumps({"ok": True, "self_test": "passed"}))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Final Merge Readiness.")
    parser.add_argument("--repo", help="Repository in owner/name form.")
    parser.add_argument("--pr", type=int, help="Pull request number.")
    parser.add_argument("--policy", default=DEFAULT_POLICY_PATH, help="Policy manifest JSON path.")
    parser.add_argument(
        "--content-root",
        default=".",
        help="Checkout root used for changed-file content scanning. Defaults to the current working directory.",
    )
    parser.add_argument("--output", help="Optional path to write the JSON receipt.")
    parser.add_argument("--self-test", action="store_true", help="Run deterministic unit tests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy = load_json(pathlib.Path(args.policy))
    validate_policy(policy)
    if args.self_test:
        return self_test()
    if not args.repo or not args.pr:
        raise SystemExit("--repo and --pr are required unless --self-test is used")

    pr, comments, check_runs, changed_paths, required_contexts, run_lookup = collect_live_context(
        args.repo,
        args.pr,
        policy,
    )
    receipt = evaluate(
        policy=policy,
        pr=pr,
        comments=comments,
        changed_paths=changed_paths,
        required_contexts=required_contexts,
        check_runs=check_runs,
        run_lookup=run_lookup,
        content_loader=content_loader_for_root(pathlib.Path(args.content_root)),
    )
    output = json.dumps(receipt, indent=2, sort_keys=True)
    if args.output:
        pathlib.Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
