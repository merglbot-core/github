#!/usr/bin/env python3
"""Generate, validate, and audit the PR Assistant enterprise rollout manifest."""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import pathlib
import subprocess
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ALLOWED_ROLLOUT_TIERS = {"core", "public", "client", "private", "shared"}
ALLOWED_ADMISSION_STATES = {"baseline_only", "advisory_docs_pilot"}
ALLOWED_DEPLOY_MODES = {"copy_target", "canonical_self"}
DEFAULT_MANIFEST_PATH = "scripts/pr-assistant/repo-policy-manifest.json"
DEFAULT_POLICY_PATH = "scripts/pr-assistant/repo-policy-inventory-policy.json"
DEFAULT_TARGET_LIST_PATH = "scripts/pr-assistant/target-repos.txt"
REQUIRED_REPO_FIELDS = (
    "repo",
    "enabled",
    "rollout_tier",
    "admission_state",
    "human_merge_only",
    "deploy_mode",
    "expected_workflow",
    "notes",
)
CONTRACT_MARKERS = (
    "MERGLBOT_REVIEW_BOUNDARY: review_only",
    "MERGLBOT_FOLLOW_UP_ID:",
    "MERGLBOT_REVIEW_HEAD_SHA:",
    "MERGLBOT_REVIEW_VERDICT:",
    "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE:",
    "MERGLBOT_CLOSEOUT_MODE:",
)


@dataclass
class RepoAudit:
    repo: str
    deploy_mode: str
    default_branch: str | None
    workflow_present: bool
    workflow_sha: str | None
    workflow_matches_canonical: bool
    step1_present: bool
    step1_sha: str | None
    step1_matches_canonical: bool
    review_boundary_marker_present: bool
    handoff_contract_present: bool
    documentation_gate_present: bool
    deployment_state: str
    phase03_contract_compliant: bool
    advisory_docs_pilot_status: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the PR Assistant enterprise repo-policy manifest and rollout coverage."
    )
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST_PATH,
        help="Path to the generated repo-policy manifest JSON.",
    )
    parser.add_argument(
        "--inventory-policy",
        default=DEFAULT_POLICY_PATH,
        help="Path to the human-authored inventory policy JSON.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_manifest_parser = subparsers.add_parser(
        "sync-manifest-from-github",
        help="Regenerate repo-policy-manifest.json from live GitHub inventory plus policy overrides.",
    )
    sync_manifest_parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if repo-policy-manifest.json differs from the generated inventory view.",
    )
    sync_manifest_parser.add_argument(
        "--write",
        action="store_true",
        help="Write the regenerated repo-policy-manifest.json.",
    )
    sync_manifest_parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token for inventory reads.",
    )

    sync_targets_parser = subparsers.add_parser(
        "sync-target-repos",
        help="Render target-repos.txt from the manifest and optionally write it.",
    )
    sync_targets_parser.add_argument(
        "--target-list",
        default=DEFAULT_TARGET_LIST_PATH,
        help="Compatibility target-repos.txt path.",
    )
    sync_targets_parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if target-repos.txt does not match the manifest render.",
    )
    sync_targets_parser.add_argument(
        "--write",
        action="store_true",
        help="Write the rendered compatibility file.",
    )

    verify_manifest_parser = subparsers.add_parser(
        "verify-manifest",
        help="Validate manifest schema and ensure target-repos.txt matches the manifest.",
    )
    verify_manifest_parser.add_argument(
        "--target-list",
        default=DEFAULT_TARGET_LIST_PATH,
        help="Compatibility target-repos.txt path.",
    )

    verify_inventory_parser = subparsers.add_parser(
        "verify-enterprise-inventory",
        help="Verify that the committed manifest exactly matches current live GitHub inventory.",
    )
    verify_inventory_parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token for inventory reads.",
    )

    baseline_parser = subparsers.add_parser(
        "build-coverage-baseline",
        help="Audit remote repo copies and render the coverage baseline JSON.",
    )
    baseline_parser.add_argument(
        "--output",
        help="Write the generated baseline JSON to this path.",
    )
    baseline_parser.add_argument(
        "--check",
        help="Fail if the generated baseline does not match this committed artifact.",
    )
    baseline_parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token for API reads.",
    )

    verify_parser = subparsers.add_parser(
        "verify",
        help="Run enterprise inventory, manifest, target-list, and coverage-baseline verification.",
    )
    verify_parser.add_argument(
        "--target-list",
        default=DEFAULT_TARGET_LIST_PATH,
        help="Compatibility target-repos.txt path.",
    )
    verify_parser.add_argument(
        "--token-env",
        default="GITHUB_TOKEN",
        help="Environment variable containing a GitHub token for API reads.",
    )

    return parser.parse_args()


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def validate_repo_name(value: str, *, label: str) -> None:
    if not isinstance(value, str) or "/" not in value or value.startswith("/") or value.endswith("/"):
        raise SystemExit(f"{label} must be org/repo")


def validate_inventory_policy(policy: dict[str, Any], policy_path: pathlib.Path) -> None:
    if not isinstance(policy, dict):
        raise SystemExit(f"{policy_path} must contain a JSON object at the root.")

    required_root_keys = (
        "schema_version",
        "baseline_date",
        "baseline_artifact",
        "canonical_source",
        "required_orgs",
        "excluded_orgs",
        "excluded_repos",
        "default_rollout_tier",
        "repo_defaults",
        "org_defaults",
        "repo_overrides",
    )
    for key in required_root_keys:
        if key not in policy:
            raise SystemExit(f"{policy_path} missing required root key: {key}")

    if policy["schema_version"] != 1:
        raise SystemExit(f"{policy_path} has unsupported schema_version={policy['schema_version']}")

    baseline_date = policy["baseline_date"]
    if not isinstance(baseline_date, str) or len(baseline_date) != 10:
        raise SystemExit(f"{policy_path} baseline_date must be YYYY-MM-DD")

    baseline_artifact = policy["baseline_artifact"]
    if not isinstance(baseline_artifact, str) or not baseline_artifact.endswith(".json"):
        raise SystemExit(f"{policy_path} baseline_artifact must be a .json path")

    canonical = policy["canonical_source"]
    if not isinstance(canonical, dict):
        raise SystemExit(f"{policy_path} canonical_source must be an object")
    for key in ("repo", "workflow_path", "step1_path"):
        value = canonical.get(key)
        if not isinstance(value, str) or not value:
            raise SystemExit(f"{policy_path} canonical_source.{key} must be a non-empty string")
    validate_repo_name(canonical["repo"], label=f"{policy_path} canonical_source.repo")

    required_orgs = policy["required_orgs"]
    if not isinstance(required_orgs, list) or not required_orgs:
        raise SystemExit(f"{policy_path} required_orgs must be a non-empty array")
    for index, org in enumerate(required_orgs, start=1):
        if not isinstance(org, str) or not org:
            raise SystemExit(f"{policy_path} required_orgs[{index}] must be a non-empty string")

    excluded_orgs = policy["excluded_orgs"]
    if not isinstance(excluded_orgs, list):
        raise SystemExit(f"{policy_path} excluded_orgs must be an array")
    for index, org in enumerate(excluded_orgs, start=1):
        if not isinstance(org, str) or not org:
            raise SystemExit(f"{policy_path} excluded_orgs[{index}] must be a non-empty string")

    excluded_repos = policy["excluded_repos"]
    if not isinstance(excluded_repos, list):
        raise SystemExit(f"{policy_path} excluded_repos must be an array")
    for index, repo in enumerate(excluded_repos, start=1):
        validate_repo_name(repo, label=f"{policy_path} excluded_repos[{index}]")

    default_rollout_tier = policy["default_rollout_tier"]
    if default_rollout_tier not in ALLOWED_ROLLOUT_TIERS:
        raise SystemExit(
            f"{policy_path} default_rollout_tier must be one of {sorted(ALLOWED_ROLLOUT_TIERS)}"
        )

    repo_defaults = policy["repo_defaults"]
    if not isinstance(repo_defaults, dict):
        raise SystemExit(f"{policy_path} repo_defaults must be an object")
    missing_defaults = [field for field in REQUIRED_REPO_FIELDS if field not in {"repo", *repo_defaults.keys()}]
    if missing_defaults:
        raise SystemExit(
            f"{policy_path} repo_defaults missing required fields: {', '.join(sorted(missing_defaults))}"
        )
    validate_repo_override(repo_defaults, policy_path, label="repo_defaults")

    org_defaults = policy["org_defaults"]
    if not isinstance(org_defaults, dict):
        raise SystemExit(f"{policy_path} org_defaults must be an object")
    for org, override in org_defaults.items():
        if not isinstance(org, str) or not org:
            raise SystemExit(f"{policy_path} org_defaults contains an invalid org key")
        if not isinstance(override, dict):
            raise SystemExit(f"{policy_path} org_defaults.{org} must be an object")
        validate_repo_override(override, policy_path, label=f"org_defaults.{org}")

    repo_overrides = policy["repo_overrides"]
    if not isinstance(repo_overrides, dict):
        raise SystemExit(f"{policy_path} repo_overrides must be an object")
    for repo, override in repo_overrides.items():
        validate_repo_name(repo, label=f"{policy_path} repo_overrides key")
        if not isinstance(override, dict):
            raise SystemExit(f"{policy_path} repo_overrides.{repo} must be an object")
        validate_repo_override(override, policy_path, label=f"repo_overrides.{repo}")


def validate_repo_override(override: dict[str, Any], path: pathlib.Path, *, label: str) -> None:
    allowed_keys = {
        "enabled",
        "rollout_tier",
        "admission_state",
        "human_merge_only",
        "deploy_mode",
        "expected_workflow",
        "expected_step1",
        "notes",
    }
    unknown = sorted(set(override) - allowed_keys)
    if unknown:
        raise SystemExit(f"{path} {label} contains unsupported keys: {', '.join(unknown)}")

    if "enabled" in override and not isinstance(override["enabled"], bool):
        raise SystemExit(f"{path} {label}.enabled must be boolean")
    if "rollout_tier" in override and override["rollout_tier"] not in ALLOWED_ROLLOUT_TIERS:
        raise SystemExit(f"{path} {label}.rollout_tier must be one of {sorted(ALLOWED_ROLLOUT_TIERS)}")
    if "admission_state" in override and override["admission_state"] not in ALLOWED_ADMISSION_STATES:
        raise SystemExit(
            f"{path} {label}.admission_state must be one of {sorted(ALLOWED_ADMISSION_STATES)}"
        )
    if "human_merge_only" in override and override["human_merge_only"] is not True:
        raise SystemExit(f"{path} {label}.human_merge_only must stay true")
    if "deploy_mode" in override and override["deploy_mode"] not in ALLOWED_DEPLOY_MODES:
        raise SystemExit(f"{path} {label}.deploy_mode must be one of {sorted(ALLOWED_DEPLOY_MODES)}")
    if "expected_workflow" in override:
        value = override["expected_workflow"]
        if not isinstance(value, str) or not value.startswith(".github/workflows/"):
            raise SystemExit(f"{path} {label}.expected_workflow must point into .github/workflows/")
    if "expected_step1" in override:
        value = override["expected_step1"]
        if value is not None and (
            not isinstance(value, str) or not value.startswith("scripts/pr-assistant/")
        ):
            raise SystemExit(f"{path} {label}.expected_step1 must point into scripts/pr-assistant/")
    if "notes" in override and not isinstance(override["notes"], str):
        raise SystemExit(f"{path} {label}.notes must be a string")


def validate_manifest(
    manifest: dict[str, Any],
    manifest_path: pathlib.Path,
    *,
    allow_example_repo: bool = False,
) -> None:
    if not isinstance(manifest, dict):
        raise SystemExit(f"{manifest_path} must contain a JSON object at the root.")

    required_root_keys = (
        "schema_version",
        "baseline_date",
        "baseline_artifact",
        "inventory_policy",
        "managed_orgs",
        "canonical_source",
        "repos",
    )
    for key in required_root_keys:
        if key not in manifest:
            raise SystemExit(f"{manifest_path} missing required root key: {key}")

    if manifest["schema_version"] != 2:
        raise SystemExit(f"{manifest_path} has unsupported schema_version={manifest['schema_version']}")

    baseline_date = manifest["baseline_date"]
    if not isinstance(baseline_date, str) or len(baseline_date) != 10:
        raise SystemExit(f"{manifest_path} baseline_date must be YYYY-MM-DD")

    baseline_artifact = manifest["baseline_artifact"]
    if not isinstance(baseline_artifact, str) or not baseline_artifact.endswith(".json"):
        raise SystemExit(f"{manifest_path} baseline_artifact must be a .json path")

    inventory_policy = manifest["inventory_policy"]
    if not isinstance(inventory_policy, str) or not inventory_policy.endswith(".json"):
        raise SystemExit(f"{manifest_path} inventory_policy must be a .json path")

    managed_orgs = manifest["managed_orgs"]
    if not isinstance(managed_orgs, list) or not managed_orgs:
        raise SystemExit(f"{manifest_path} managed_orgs must be a non-empty array")
    for index, org in enumerate(managed_orgs, start=1):
        if not isinstance(org, str) or not org:
            raise SystemExit(f"{manifest_path} managed_orgs[{index}] must be a non-empty string")

    canonical = manifest["canonical_source"]
    if not isinstance(canonical, dict):
        raise SystemExit(f"{manifest_path} canonical_source must be an object")
    for key in ("repo", "workflow_path", "step1_path"):
        value = canonical.get(key)
        if not isinstance(value, str) or not value:
            raise SystemExit(f"{manifest_path} canonical_source.{key} must be a non-empty string")
    validate_repo_name(canonical["repo"], label=f"{manifest_path} canonical_source.repo")

    repos = manifest["repos"]
    if not isinstance(repos, list) or not repos:
        raise SystemExit(f"{manifest_path} repos must be a non-empty array")

    seen_repos: set[str] = set()
    seen_canonical_self = 0
    for index, repo_entry in enumerate(repos, start=1):
        if not isinstance(repo_entry, dict):
            raise SystemExit(f"{manifest_path} repos[{index}] must be an object")
        missing = [field for field in REQUIRED_REPO_FIELDS if field not in repo_entry]
        if missing:
            raise SystemExit(f"{manifest_path} repos[{index}] missing required fields: {', '.join(missing)}")

        repo = repo_entry["repo"]
        if repo == "example/example" and allow_example_repo:
            pass
        else:
            validate_repo_name(repo, label=f"{manifest_path} repos[{index}].repo")
        if repo in seen_repos:
            raise SystemExit(f"{manifest_path} contains duplicate repo entry: {repo}")
        seen_repos.add(repo)

        enabled = repo_entry["enabled"]
        if not isinstance(enabled, bool):
            raise SystemExit(f"{manifest_path} repos[{index}].enabled must be boolean")

        rollout_tier = repo_entry["rollout_tier"]
        if rollout_tier not in ALLOWED_ROLLOUT_TIERS:
            raise SystemExit(
                f"{manifest_path} repos[{index}].rollout_tier must be one of {sorted(ALLOWED_ROLLOUT_TIERS)}"
            )

        admission_state = repo_entry["admission_state"]
        if admission_state not in ALLOWED_ADMISSION_STATES:
            raise SystemExit(
                f"{manifest_path} repos[{index}].admission_state must be one of {sorted(ALLOWED_ADMISSION_STATES)}"
            )

        human_merge_only = repo_entry["human_merge_only"]
        if human_merge_only is not True:
            raise SystemExit(f"{manifest_path} repos[{index}].human_merge_only must stay true")

        deploy_mode = repo_entry["deploy_mode"]
        if deploy_mode not in ALLOWED_DEPLOY_MODES:
            raise SystemExit(
                f"{manifest_path} repos[{index}].deploy_mode must be one of {sorted(ALLOWED_DEPLOY_MODES)}"
            )
        if deploy_mode == "canonical_self":
            seen_canonical_self += 1
            if repo != canonical["repo"]:
                raise SystemExit(
                    f"{manifest_path} repos[{index}] uses deploy_mode=canonical_self but repo != canonical_source.repo"
                )

        expected_workflow = repo_entry["expected_workflow"]
        if not isinstance(expected_workflow, str) or not expected_workflow.startswith(".github/workflows/"):
            raise SystemExit(
                f"{manifest_path} repos[{index}].expected_workflow must point into .github/workflows/"
            )

        expected_step1 = repo_entry.get("expected_step1")
        if expected_step1 is not None and (
            not isinstance(expected_step1, str) or not expected_step1.startswith("scripts/pr-assistant/")
        ):
            raise SystemExit(
                f"{manifest_path} repos[{index}].expected_step1 must point into scripts/pr-assistant/"
            )

        notes = repo_entry["notes"]
        if not isinstance(notes, str):
            raise SystemExit(f"{manifest_path} repos[{index}].notes must be a string")

    if seen_canonical_self != 1:
        raise SystemExit(f"{manifest_path} must contain exactly one deploy_mode=canonical_self repo entry")


def render_target_repos(manifest: dict[str, Any]) -> str:
    header = textwrap.dedent(
        """\
        # Derived compatibility artifact for PR Assistant rollout tooling.
        # Generated from scripts/pr-assistant/repo-policy-manifest.json
        # Inventory policy source: scripts/pr-assistant/repo-policy-inventory-policy.json
        # Regenerate / verify:
        #   python3 scripts/pr-assistant/repo-policy-manifest.py sync-target-repos --check
        #   python3 scripts/pr-assistant/repo-policy-manifest.py sync-target-repos --write
        # Enabled copy-deploy targets only. canonical_self entries stay in the manifest and coverage audit only.
        """
    )
    lines = [header.rstrip(), ""]
    last_org = None
    for entry in manifest["repos"]:
        if not entry["enabled"] or entry["deploy_mode"] != "copy_target":
            continue
        org = entry["repo"].split("/", 1)[0]
        if org != last_org and last_org is not None:
            lines.append("")
        last_org = org
        note = entry["notes"].strip()
        if note:
            lines.append(f"# NOTE: {note}")
        lines.append(entry["repo"])
    lines.append("")
    return "\n".join(lines)


def sync_target_repos(manifest: dict[str, Any], target_path: pathlib.Path, *, check: bool, write: bool) -> None:
    rendered = render_target_repos(manifest)
    if check or not write:
        current = target_path.read_text(encoding="utf-8")
        if current != rendered:
            raise SystemExit(
                f"{target_path} drifted from repo-policy-manifest.json. "
                "Run sync-target-repos --write to refresh the compatibility artifact."
            )
    if write:
        target_path.write_text(rendered, encoding="utf-8")


def get_token(token_env: str) -> str:
    token = os.environ.get(token_env) or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:
        output = subprocess.check_output(["gh", "auth", "token"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(
            f"Missing GitHub token. Set {token_env} (or GH_TOKEN) or authenticate gh."
        ) from exc
    token = output.strip()
    if not token:
        raise SystemExit(f"Missing GitHub token. Set {token_env} (or GH_TOKEN).")
    return token


def github_request(path: str, token: str) -> Any:
    url = f"https://api.github.com{path}"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "merglbot-repo-policy-manifest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        if "CERTIFICATE_VERIFY_FAILED" not in reason:
            raise SystemExit(f"GitHub API transport error for {path}: {exc}") from exc
        try:
            result = subprocess.run(
                ["gh", "api", path],
                text=True,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as gh_exc:
            raise SystemExit(
                f"GitHub API TLS validation failed for {path} and gh fallback is unavailable."
            ) from gh_exc
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "404" in stderr or "Not Found" in stderr:
                return None
            raise SystemExit(
                f"GitHub API TLS validation failed for {path} and gh fallback returned: {stderr}"
            )
        return json.loads(result.stdout)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise SystemExit(f"GitHub API {exc.code} for {path}: {exc.read().decode('utf-8', errors='replace')}") from exc


def github_paginated_request(path: str, token: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    separator = "&" if "?" in path else "?"
    while True:
        data = github_request(f"{path}{separator}per_page=100&page={page}", token)
        if not data:
            break
        if not isinstance(data, list):
            raise SystemExit(f"Expected list response from GitHub API for {path}, got: {type(data).__name__}")
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results


def read_live_inventory(policy: dict[str, Any], token: str) -> tuple[list[str], list[str]]:
    visible_orgs = sorted(
        org["login"]
        for org in github_paginated_request("/user/orgs", token)
        if org["login"] not in set(policy["excluded_orgs"])
    )

    missing_required_orgs = sorted(set(policy["required_orgs"]) - set(visible_orgs))
    if missing_required_orgs:
        raise SystemExit(
            "Live GitHub inventory is incomplete for this token. Missing required org visibility: "
            + ", ".join(missing_required_orgs)
        )

    excluded_repos = set(policy["excluded_repos"])
    repos: list[str] = []
    for org in visible_orgs:
        repo_payload = github_paginated_request(f"/orgs/{org}/repos?type=all&sort=full_name&direction=asc", token)
        for repo in repo_payload:
            if repo.get("archived"):
                continue
            full_name = repo["full_name"]
            if full_name in excluded_repos:
                continue
            repos.append(full_name)

    repos = sorted(set(repos), key=lambda value: tuple(value.split("/", 1)))
    return visible_orgs, repos


def build_manifest_from_inventory(policy: dict[str, Any], managed_orgs: list[str], repos: list[str]) -> dict[str, Any]:
    manifest = {
        "schema_version": 2,
        "baseline_date": policy["baseline_date"],
        "baseline_artifact": policy["baseline_artifact"],
        "inventory_policy": DEFAULT_POLICY_PATH,
        "managed_orgs": managed_orgs,
        "canonical_source": copy.deepcopy(policy["canonical_source"]),
        "repos": [],
    }

    repo_defaults = copy.deepcopy(policy["repo_defaults"])
    org_defaults = policy["org_defaults"]
    repo_overrides = policy["repo_overrides"]

    for repo in repos:
        org = repo.split("/", 1)[0]
        entry = {"repo": repo, **copy.deepcopy(repo_defaults)}
        if org in org_defaults:
            entry.update(copy.deepcopy(org_defaults[org]))
        if repo in repo_overrides:
            entry.update(copy.deepcopy(repo_overrides[repo]))
        manifest["repos"].append(entry)

    validate_manifest(manifest, pathlib.Path(DEFAULT_MANIFEST_PATH))
    return manifest


def sync_manifest_from_github(
    manifest_path: pathlib.Path,
    policy_path: pathlib.Path,
    *,
    token_env: str,
    check: bool,
    write: bool,
) -> dict[str, Any]:
    policy = load_json(policy_path)
    validate_inventory_policy(policy, policy_path)
    token = get_token(token_env)
    managed_orgs, repos = read_live_inventory(policy, token)
    rendered = build_manifest_from_inventory(policy, managed_orgs, repos)

    if check or not write:
        current = load_json(manifest_path)
        if current != rendered:
            current_repos = {entry["repo"] for entry in current.get("repos", []) if isinstance(entry, dict) and "repo" in entry}
            rendered_repos = {entry["repo"] for entry in rendered["repos"]}
            missing = sorted(rendered_repos - current_repos)
            extra = sorted(current_repos - rendered_repos)
            details = []
            if missing:
                details.append(f"missing repos in manifest: {', '.join(missing[:10])}" + (" ..." if len(missing) > 10 else ""))
            if extra:
                details.append(f"extra repos in manifest: {', '.join(extra[:10])}" + (" ..." if len(extra) > 10 else ""))
            suffix = f" ({'; '.join(details)})" if details else ""
            raise SystemExit(
                f"{manifest_path} drifted from live GitHub inventory and inventory policy."
                f"{suffix} Run sync-manifest-from-github --write to refresh the manifest."
            )

    if write:
        write_json(manifest_path, rendered)

    return rendered


def get_repo_default_branch(repo: str, token: str) -> str | None:
    data = github_request(f"/repos/{repo}", token)
    if not data:
        return None
    return data.get("default_branch")


def get_file(repo: str, path: str, token: str) -> tuple[str | None, str | None]:
    encoded_path = urllib.parse.quote(path, safe="/")
    data = github_request(f"/repos/{repo}/contents/{encoded_path}", token)
    if not data:
        return None, None
    if data.get("type") != "file":
        return None, None
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data.get("sha")


def audit_repo(
    repo_entry: dict[str, Any],
    canonical_workflow_sha: str,
    canonical_step1_sha: str,
    token: str,
) -> RepoAudit:
    repo = repo_entry["repo"]
    default_branch = get_repo_default_branch(repo, token)
    expected_step1 = repo_entry.get("expected_step1")

    workflow_content, workflow_sha = get_file(repo, repo_entry["expected_workflow"], token)
    step1_content, step1_sha = (None, None)
    if expected_step1:
        step1_content, step1_sha = get_file(repo, expected_step1, token)

    review_boundary_marker_present = bool(
        workflow_content and "MERGLBOT_REVIEW_BOUNDARY: review_only" in workflow_content
    )
    handoff_contract_present = bool(
        workflow_content and all(marker in workflow_content for marker in CONTRACT_MARKERS[1:])
    )
    documentation_gate_present = bool(
        workflow_content
        and "DOCUMENTATION_OBLIGATION_STATE" in workflow_content
        and "blocked_missing_authority" in workflow_content
    )

    workflow_present = workflow_content is not None
    step1_present = expected_step1 is None or step1_content is not None
    workflow_matches = bool(workflow_sha and workflow_sha == canonical_workflow_sha)
    step1_matches = bool(step1_sha and step1_sha == canonical_step1_sha) if expected_step1 else True

    if repo_entry["deploy_mode"] == "canonical_self":
        if workflow_present and step1_present and workflow_matches and step1_matches:
            deployment_state = "canonical_self"
        elif workflow_present or step1_present:
            deployment_state = "canonical_self_drift"
        else:
            deployment_state = "not_deployed"
    else:
        if workflow_present and step1_present and workflow_matches and step1_matches:
            deployment_state = "deployed_canonical_copy"
        elif workflow_present or step1_present:
            deployment_state = "deployed_copy_drift"
        else:
            deployment_state = "not_deployed"

    phase03_contract_compliant = bool(
        workflow_present
        and step1_present
        and workflow_matches
        and step1_matches
        and review_boundary_marker_present
        and handoff_contract_present
        and documentation_gate_present
    )

    if not repo_entry["enabled"]:
        advisory_docs_pilot_status = "disabled"
    elif repo_entry["admission_state"] != "advisory_docs_pilot":
        advisory_docs_pilot_status = "not_requested"
    elif not phase03_contract_compliant:
        advisory_docs_pilot_status = "blocked_contract_drift"
    else:
        advisory_docs_pilot_status = "ready"

    return RepoAudit(
        repo=repo,
        deploy_mode=repo_entry["deploy_mode"],
        default_branch=default_branch,
        workflow_present=workflow_present,
        workflow_sha=workflow_sha,
        workflow_matches_canonical=workflow_matches,
        step1_present=step1_present,
        step1_sha=step1_sha,
        step1_matches_canonical=step1_matches,
        review_boundary_marker_present=review_boundary_marker_present,
        handoff_contract_present=handoff_contract_present,
        documentation_gate_present=documentation_gate_present,
        deployment_state=deployment_state,
        phase03_contract_compliant=phase03_contract_compliant,
        advisory_docs_pilot_status=advisory_docs_pilot_status,
        notes=repo_entry["notes"],
    )


def build_coverage_baseline(manifest: dict[str, Any], token_env: str) -> dict[str, Any]:
    token = get_token(token_env)
    canonical = manifest["canonical_source"]
    canonical_workflow_content, canonical_workflow_sha = get_file(
        canonical["repo"], canonical["workflow_path"], token
    )
    canonical_step1_content, canonical_step1_sha = get_file(
        canonical["repo"], canonical["step1_path"], token
    )
    if not canonical_workflow_content or not canonical_workflow_sha:
        raise SystemExit("Unable to read canonical workflow from GitHub")
    if not canonical_step1_content or not canonical_step1_sha:
        raise SystemExit("Unable to read canonical step1 helper from GitHub")

    audits = [
        audit_repo(repo_entry, canonical_workflow_sha, canonical_step1_sha, token)
        for repo_entry in manifest["repos"]
    ]

    repo_entries = []
    for repo_entry, audit in zip(manifest["repos"], audits):
        repo_entries.append(
            {
                "repo": repo_entry["repo"],
                "enabled": repo_entry["enabled"],
                "rollout_tier": repo_entry["rollout_tier"],
                "admission_state": repo_entry["admission_state"],
                "human_merge_only": repo_entry["human_merge_only"],
                "deploy_mode": repo_entry["deploy_mode"],
                "expected_workflow": repo_entry["expected_workflow"],
                "expected_step1": repo_entry.get("expected_step1"),
                "notes": repo_entry["notes"],
                "default_branch": audit.default_branch,
                "workflow_present": audit.workflow_present,
                "workflow_sha": audit.workflow_sha,
                "workflow_matches_canonical": audit.workflow_matches_canonical,
                "step1_present": audit.step1_present,
                "step1_sha": audit.step1_sha,
                "step1_matches_canonical": audit.step1_matches_canonical,
                "review_boundary_marker_present": audit.review_boundary_marker_present,
                "handoff_contract_present": audit.handoff_contract_present,
                "documentation_gate_present": audit.documentation_gate_present,
                "deployment_state": audit.deployment_state,
                "phase03_contract_compliant": audit.phase03_contract_compliant,
                "advisory_docs_pilot_status": audit.advisory_docs_pilot_status,
            }
        )

    summary = {
        "managed_org_count": len(manifest["managed_orgs"]),
        "repo_count": len(repo_entries),
        "enabled_count": sum(1 for entry in repo_entries if entry["enabled"]),
        "copy_target_count": sum(1 for entry in repo_entries if entry["deploy_mode"] == "copy_target"),
        "canonical_self_count": sum(1 for entry in repo_entries if entry["deploy_mode"] == "canonical_self"),
        "canonical_copy_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "deployed_canonical_copy"
        ),
        "canonical_self_ready_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "canonical_self"
        ),
        "drift_count": sum(
            1
            for entry in repo_entries
            if entry["deployment_state"] in {"deployed_copy_drift", "canonical_self_drift"}
        ),
        "not_deployed_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "not_deployed"
        ),
        "phase03_contract_compliant_count": sum(
            1 for entry in repo_entries if entry["phase03_contract_compliant"]
        ),
        "advisory_docs_pilot_count": sum(
            1 for entry in repo_entries if entry["admission_state"] == "advisory_docs_pilot"
        ),
        "advisory_docs_pilot_ready_count": sum(
            1 for entry in repo_entries if entry["advisory_docs_pilot_status"] == "ready"
        ),
    }

    return {
        "schema_version": 2,
        "baseline_date": manifest["baseline_date"],
        "inventory_policy": manifest["inventory_policy"],
        "managed_orgs": manifest["managed_orgs"],
        "manifest_path": DEFAULT_MANIFEST_PATH,
        "target_list_path": DEFAULT_TARGET_LIST_PATH,
        "canonical_source": {
            "repo": canonical["repo"],
            "workflow_path": canonical["workflow_path"],
            "workflow_sha": canonical_workflow_sha,
            "step1_path": canonical["step1_path"],
            "step1_sha": canonical_step1_sha,
        },
        "summary": summary,
        "repos": repo_entries,
    }


def write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def compare_json(expected_path: pathlib.Path, payload: dict[str, Any]) -> None:
    expected = load_json(expected_path)
    if expected != payload:
        raise SystemExit(
            f"{expected_path} drifted from live rollout audit. "
            "Regenerate the committed baseline with build-coverage-baseline --output."
        )


def main() -> None:
    args = parse_args()
    manifest_path = pathlib.Path(args.manifest)
    policy_path = pathlib.Path(args.inventory_policy)

    if args.command == "sync-manifest-from-github":
        sync_manifest_from_github(
            manifest_path,
            policy_path,
            token_env=args.token_env,
            check=args.check or not args.write,
            write=args.write,
        )
        return

    manifest = load_json(manifest_path)
    validate_manifest(manifest, manifest_path)

    if args.command == "sync-target-repos":
        sync_target_repos(
            manifest,
            pathlib.Path(args.target_list),
            check=args.check or not args.write,
            write=args.write,
        )
        return

    if args.command == "verify-manifest":
        sync_target_repos(
            manifest,
            pathlib.Path(args.target_list),
            check=True,
            write=False,
        )
        baseline_artifact = pathlib.Path(manifest["baseline_artifact"])
        if not baseline_artifact.is_file():
            raise SystemExit(f"Committed baseline artifact missing: {baseline_artifact}")
        load_json(baseline_artifact)
        return

    if args.command == "verify-enterprise-inventory":
        sync_manifest_from_github(
            manifest_path,
            policy_path,
            token_env=args.token_env,
            check=True,
            write=False,
        )
        return

    if args.command == "build-coverage-baseline":
        payload = build_coverage_baseline(manifest, args.token_env)
        if args.output:
            write_json(pathlib.Path(args.output), payload)
        if args.check:
            compare_json(pathlib.Path(args.check), payload)
        if not args.output and not args.check:
            print(json.dumps(payload, indent=2, sort_keys=False))
        return

    if args.command == "verify":
        sync_manifest_from_github(
            manifest_path,
            policy_path,
            token_env=args.token_env,
            check=True,
            write=False,
        )
        sync_target_repos(
            manifest,
            pathlib.Path(args.target_list),
            check=True,
            write=False,
        )
        payload = build_coverage_baseline(manifest, args.token_env)
        compare_json(pathlib.Path(manifest["baseline_artifact"]), payload)
        return

    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
