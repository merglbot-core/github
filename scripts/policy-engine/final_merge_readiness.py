#!/usr/bin/env python3
"""Merglbot autonomy policy evaluator.

The script is intentionally stdlib-only so it can run from GitHub Actions
without bootstrapping a repo-specific runtime. It emits bounded JSON receipts
for final merge readiness and Terraform deployment protection approval.

Runtime contract: Python 3.11 in CI. The script only uses the standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any


DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"
DECISION_HUMAN_REQUIRED = "human_required"
INVALID_REPO_PATH_MARKER = "__invalid_repo_path__"
MAX_CHANGED_FILES = 1_000


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object in {path}")
    return data


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_repo_path(path_value: str) -> str:
    raw = str(path_value or "").replace("\\", "/").strip()
    if not raw:
        return ""
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha()):
        return INVALID_REPO_PATH_MARKER
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return INVALID_REPO_PATH_MARKER
        parts.append(part)
    return "/".join(parts)


def has_invalid_repo_path(paths: list[str]) -> bool:
    return any(normalize_repo_path(path_value) == INVALID_REPO_PATH_MARKER for path_value in paths)


def glob_match(path_value: str, patterns: list[str]) -> bool:
    """Match normalized repository-relative paths.

    `PurePosixPath.match` provides deterministic recursive `**` behavior for
    manifest paths after separators and leading slashes are normalized.
    Patterns that start with `**/` also match the same suffix at repository
    root to keep historical manifest compatibility.
    """
    normalized = normalize_repo_path(path_value)
    posix_path = PurePosixPath(normalized)
    for pattern in patterns:
        normalized_pattern = normalize_repo_path(str(pattern))
        if posix_path.match(normalized_pattern):
            return True
        if normalized_pattern.startswith("**/") and posix_path.match(normalized_pattern[3:]):
            return True
        if fnmatch.fnmatchcase(normalized, normalized_pattern):
            return True
    return False


def all_match(paths: list[str], patterns: list[str]) -> bool:
    return bool(paths) and all(glob_match(path_value, patterns) for path_value in paths)


def any_match(paths: list[str], patterns: list[str]) -> bool:
    return any(glob_match(path_value, patterns) for path_value in paths)


def stable_tree_marker(changed_files: list[str], head_sha: str, base_sha: str) -> str:
    digest = hashlib.sha256()
    digest.update(head_sha.encode("utf-8"))
    digest.update(b"\0")
    digest.update(base_sha.encode("utf-8"))
    for file_path in sorted({normalize_repo_path(file_path) for file_path in changed_files}):
        digest.update(b"\0")
        digest.update(file_path.encode("utf-8"))
    return digest.hexdigest()


def evidence_summary(values: list[str], *, head_sha: str = "", base_sha: str = "") -> dict[str, Any]:
    return {
        "count": len(values),
        "marker": stable_tree_marker(values, head_sha, base_sha) if values else None,
    }


def extract_repository_full_name(event: dict[str, Any]) -> str:
    raw_repository = event.get("repository") or event.get("repository_full_name") or event.get("repo")
    if isinstance(raw_repository, dict):
        full_name = raw_repository.get("full_name")
        if isinstance(full_name, str) and "/" in full_name:
            return full_name
        owner = raw_repository.get("owner")
        owner_name = owner.get("login") if isinstance(owner, dict) else owner
        name = raw_repository.get("name")
        if isinstance(owner_name, str) and isinstance(name, str):
            return f"{owner_name}/{name}"
        return ""
    if isinstance(raw_repository, str):
        return raw_repository.strip()
    owner = event.get("owner") or event.get("organization")
    repo_name = event.get("repo_name") or event.get("repository_name")
    if isinstance(owner, str) and isinstance(repo_name, str):
        return f"{owner}/{repo_name}"
    return ""


def evaluate_repo_scope(event: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    scope = manifest.get("repo_scope", {})
    if not isinstance(scope, dict) or not scope:
        return {"configured": False, "accepted": False}, ["Repository scope is missing from policy manifest."]

    repository_full_name = extract_repository_full_name(event)
    owner = repository_full_name.split("/", 1)[0] if "/" in repository_full_name else ""
    included_orgs = {str(value) for value in as_list(scope.get("included_orgs"))}
    excluded_orgs = {str(value) for value in as_list(scope.get("excluded_orgs"))}
    problems: list[str] = []

    if not owner:
        problems.append("Repository scope evidence is missing or malformed.")
    elif owner in excluded_orgs:
        problems.append("Repository owner is explicitly excluded by policy manifest.")
    elif included_orgs and owner not in included_orgs:
        problems.append("Repository owner is outside the policy manifest scope.")

    return {
        "configured": True,
        "accepted": not problems,
        "repository_marker": stable_digest(repository_full_name)[:16] if repository_full_name else None,
    }, problems


def classify_path_risk(changed_files: list[str], manifest: dict[str, Any]) -> tuple[str, list[str]]:
    path_policy = manifest.get("path_policy", {})
    docs_globs = as_list(path_policy.get("docs_only_globs"))
    prompt_globs = as_list(path_policy.get("prompt_library_globs"))
    workflow_globs = as_list(path_policy.get("workflow_globs"))
    terraform_globs = as_list(path_policy.get("terraform_globs"))
    denied_globs = as_list(path_policy.get("denied_globs"))

    notes: list[str] = []
    if not changed_files:
        return "unknown", ["No changed files were supplied."]
    if len(changed_files) > MAX_CHANGED_FILES:
        return "security_sensitive", ["Changed-file evidence exceeds policy size limit."]
    if has_invalid_repo_path(changed_files):
        return "security_sensitive", ["Invalid repository path changed."]
    if any_match(changed_files, denied_globs):
        return "security_sensitive", ["Denied or credential-shaped path changed."]
    if any_match(changed_files, workflow_globs):
        notes.append("Workflow, reusable action, or policy path changed.")
        return "workflow", notes
    if any_match(changed_files, terraform_globs):
        notes.append("Terraform or Terraform deployment path changed.")
        return "terraform", notes
    if all_match(changed_files, docs_globs):
        return "docs_only", notes
    if any_match(changed_files, prompt_globs):
        notes.append("Prompt library governance path changed.")
        return "prompt_library", notes
    return "code", ["General code path changed."]


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def evaluate_review_receipt(event: dict[str, Any], manifest: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    receipt = event.get("review_receipt")
    if not isinstance(receipt, dict):
        return {"status": "missing"}, ["Missing Merglbot review receipt."]

    head_sha = str(event.get("head_sha") or "")
    receipt_head_sha = str(receipt.get("head_sha") or receipt.get("review_head_sha") or "")
    required = manifest.get("required_evidence", {}).get("review_receipt", {})
    accepted = {normalize_status(value) for value in as_list(required.get("accepted_statuses"))}
    status_candidates = [
        receipt.get("status"),
        receipt.get("decision"),
        receipt.get("verdict"),
        receipt.get("review_status"),
    ]
    statuses = {normalize_status(value) for value in status_candidates if value is not None}

    problems: list[str] = []
    if required.get("must_match_head_sha", True) and (not head_sha or receipt_head_sha != head_sha):
        problems.append("Merglbot review receipt does not match current head SHA.")
    if not statuses.intersection(accepted):
        problems.append("Merglbot review receipt is not in an accepted status.")

    normalized = {
        "provider": receipt.get("provider") or required.get("provider") or "Merglbot PR Assistant",
        "head_sha": receipt_head_sha,
        "statuses": sorted(statuses),
        "accepted": not problems,
    }
    return normalized, problems


def evaluate_required_checks(event: dict[str, Any], manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    required = manifest.get("required_evidence", {}).get("required_checks", {})
    accepted = {normalize_status(value) for value in as_list(required.get("accepted_conclusions"))}
    checks = as_list(event.get("required_checks"))
    normalized: list[dict[str, Any]] = []
    problems: list[str] = []

    if not checks:
        return [], ["No required-check evidence was supplied."]

    for raw in checks:
        if not isinstance(raw, dict):
            problems.append("Malformed required-check entry.")
            continue
        name = str(raw.get("name") or raw.get("context") or "unknown")
        conclusion = normalize_status(raw.get("conclusion") or raw.get("status"))
        ok = conclusion in accepted
        normalized.append({"name": name, "conclusion": conclusion, "accepted": ok})
        if not ok:
            problems.append(f"Required check `{name}` is `{conclusion or 'unknown'}`.")
    return normalized, problems


def evaluate_docs_obligation(event: dict[str, Any], manifest: dict[str, Any], risk_class: str) -> tuple[str, list[str]]:
    accepted = {
        normalize_status(value)
        for value in as_list(manifest.get("required_evidence", {}).get("docs_obligation", {}).get("accepted_states"))
    }
    raw_state = event.get("docs_obligation")
    if raw_state is None and isinstance(event.get("review_receipt"), dict):
        raw_state = (
            event["review_receipt"].get("docs_obligation")
            or event["review_receipt"].get("documentation_obligation_state")
        )
    state = normalize_status(raw_state)
    if not state and risk_class == "docs_only":
        return "not_required", []
    if state in accepted:
        return state, []
    return state or "unknown", ["Documentation obligation is not satisfied."]


def evaluate_ai_data_policy_check(event: dict[str, Any], manifest: dict[str, Any], changed_files: list[str]) -> tuple[dict[str, Any], list[str]]:
    path_policy = manifest.get("path_policy", {})
    policy_globs = as_list(path_policy.get("ai_data_policy_globs"))
    required = bool(policy_globs) and any_match(changed_files, policy_globs)
    if not required:
        return {"required": False, "state": "not_applicable"}, []

    accepted = {
        normalize_status(value)
        for value in as_list(manifest.get("required_evidence", {}).get("ai_data_policy_check", {}).get("accepted_states"))
    }
    raw_state = event.get("ai_data_policy_check")
    if raw_state is None and isinstance(event.get("review_receipt"), dict):
        raw_state = (
            event["review_receipt"].get("ai_data_policy_check")
            or event["review_receipt"].get("client_data_exposure_check")
        )
    state = normalize_status(raw_state)
    if state in accepted:
        return {"required": True, "state": state}, []
    return {"required": True, "state": state or "missing"}, ["AI data policy check is required for this PR scope."]


def evaluate_final_merge_readiness(event: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    changed_files = [str(value) for value in as_list(event.get("changed_files"))]
    head_sha = str(event.get("head_sha") or "")
    base_sha = str(event.get("base_sha") or "")
    now = utc_now()
    ttl_minutes = int(manifest.get("receipt_ttl_minutes") or 30)
    risk_class, risk_notes = classify_path_risk(changed_files, manifest)
    repo_scope, repo_scope_problems = evaluate_repo_scope(event, manifest)
    review_receipt, review_problems = evaluate_review_receipt(event, manifest)
    required_checks, check_problems = evaluate_required_checks(event, manifest)
    docs_obligation, docs_problems = evaluate_docs_obligation(event, manifest, risk_class)
    ai_data_policy_check, ai_data_policy_problems = evaluate_ai_data_policy_check(event, manifest, changed_files)

    problems = [*repo_scope_problems, *risk_notes, *review_problems, *check_problems, *docs_problems, *ai_data_policy_problems]
    allowed_actions = manifest.get("autonomous_actions", {}).get(risk_class, [])
    if risk_class in {"workflow", "terraform", "security_sensitive", "code", "unknown"}:
        decision = DECISION_HUMAN_REQUIRED
        problems.append(f"Risk class `{risk_class}` is not eligible for autonomous final merge readiness.")
    elif "final_merge_readiness" not in allowed_actions:
        decision = DECISION_HUMAN_REQUIRED
        problems.append(f"Risk class `{risk_class}` has no autonomous final merge policy.")
    elif repo_scope_problems or review_problems or check_problems or docs_problems or ai_data_policy_problems:
        decision = DECISION_BLOCK
    else:
        decision = DECISION_ALLOW

    candidate_tree_sha = str(event.get("candidate_tree_sha") or "") or stable_tree_marker(changed_files, head_sha, base_sha)

    return {
        "schema_version": manifest.get("schema_version"),
        "policy_name": manifest.get("policy_name"),
        "head_sha": head_sha,
        "base_sha": base_sha,
        "merge_strategy": str(event.get("merge_strategy") or manifest.get("default_merge_strategy") or "squash"),
        "candidate_tree_sha": candidate_tree_sha,
        "changed_files_summary": evidence_summary(changed_files, head_sha=head_sha, base_sha=base_sha),
        "repo_scope": repo_scope,
        "risk_class": risk_class,
        "docs_obligation": docs_obligation,
        "ai_data_policy_check": ai_data_policy_check,
        "review_receipt": review_receipt,
        "required_checks": required_checks,
        "decision": decision,
        "reasons": problems,
        "issued_at": isoformat_z(now),
        "expires_at": isoformat_z(now + timedelta(minutes=ttl_minutes)),
    }


def evaluate_terraform_approval(event: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    tf_policy = manifest.get("terraform_approval", {})
    required_fields = [str(value) for value in as_list(tf_policy.get("required_fields"))]
    problems: list[str] = []
    for field in required_fields:
        if event.get(field) in (None, "", []):
            problems.append(f"Missing Terraform approval field `{field}`.")

    workspace_phase = str(event.get("workspace_phase") or "")
    expected_action = str(event.get("expected_action") or "")
    policy_decision = normalize_status(event.get("policy_decision"))
    allowed_targets = [str(value) for value in as_list(event.get("allowed_targets"))]
    plan_hash = str(event.get("plan_hash") or "")
    head_sha = str(event.get("head_sha") or "")

    if workspace_phase not in as_list(tf_policy.get("allowed_workspace_phases")):
        problems.append(f"Workspace phase `{workspace_phase}` is not allowed.")
    if expected_action not in as_list(tf_policy.get("allowed_expected_actions")):
        problems.append(f"Expected action `{expected_action}` is not allowed.")
    if policy_decision not in {normalize_status(value) for value in as_list(tf_policy.get("approved_policy_decisions"))}:
        problems.append("Terraform approval policy decision is not approved.")
    if not plan_hash.startswith("sha256:") or len(plan_hash) <= len("sha256:"):
        problems.append("Terraform plan hash must use `sha256:<digest>` format.")
    denied_target_globs = [str(value) for value in as_list(tf_policy.get("denied_target_globs"))]
    if any_match(allowed_targets, denied_target_globs):
        problems.append("Terraform approval includes a denied target.")
    if not head_sha:
        problems.append("Terraform approval is missing exact commit SHA.")

    now = utc_now()
    decision = DECISION_ALLOW if not problems else DECISION_BLOCK
    return {
        "schema_version": manifest.get("schema_version"),
        "policy_name": manifest.get("policy_name"),
        "workflow_run_id": event.get("workflow_run_id"),
        "head_sha": head_sha,
        "workspace_phase": workspace_phase,
        "plan_hash": plan_hash,
        "allowed_targets_summary": evidence_summary(allowed_targets, head_sha=head_sha),
        "expected_action": expected_action,
        "policy_decision": event.get("policy_decision"),
        "rollback_note": event.get("rollback_note"),
        "decision": decision,
        "reasons": problems,
        "issued_at": isoformat_z(now),
        "custom_deployment_protection_rule": tf_policy.get("custom_deployment_protection_rule", {}),
    }


def write_receipt(receipt: dict[str, Any], output_path: str | None) -> None:
    text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    if output_path:
        Path(output_path).write_text(text, encoding="utf-8")
    print(text, end="")


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        raise SystemExit("final_merge_readiness.py requires Python 3.11 or newer")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="scripts/policy-engine/policy-manifest.json")
    parser.add_argument("--event", required=True, help="JSON event payload to evaluate")
    parser.add_argument("--output", help="Optional path for the emitted receipt")
    parser.add_argument(
        "--mode",
        choices=["final-merge-readiness", "terraform-approval"],
        default="final-merge-readiness",
    )
    args = parser.parse_args(argv)

    manifest = load_json(args.manifest)
    event = load_json(args.event)
    if args.mode == "terraform-approval":
        receipt = evaluate_terraform_approval(event, manifest)
    else:
        receipt = evaluate_final_merge_readiness(event, manifest)
    write_receipt(receipt, args.output)
    return 0 if receipt.get("decision") == DECISION_ALLOW else 1


if __name__ == "__main__":
    raise SystemExit(main())
