#!/usr/bin/env python3
"""Validate the PR Assistant repo-policy manifest and audit rollout coverage."""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ALLOWED_ROLLOUT_TIERS = {"core", "public", "client", "private"}
ALLOWED_ADMISSION_STATES = {"baseline_only", "phase05_candidate"}
REQUIRED_REPO_FIELDS = (
    "repo",
    "enabled",
    "rollout_tier",
    "admission_state",
    "human_merge_only",
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
    phase05_canary_eligibility: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the PR Assistant repo-policy manifest and coverage baseline."
    )
    parser.add_argument(
        "--manifest",
        default="scripts/pr-assistant/repo-policy-manifest.json",
        help="Path to repo-policy manifest JSON.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync-target-repos",
        help="Render target-repos.txt from the manifest and optionally write it.",
    )
    sync_parser.add_argument(
        "--target-list",
        default="scripts/pr-assistant/target-repos.txt",
        help="Compatibility target-repos.txt path.",
    )
    sync_parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if target-repos.txt does not match the manifest render.",
    )
    sync_parser.add_argument(
        "--write",
        action="store_true",
        help="Write the rendered compatibility file.",
    )

    local_parser = subparsers.add_parser(
        "verify-manifest",
        help="Validate schema and ensure target-repos.txt matches the manifest.",
    )
    local_parser.add_argument(
        "--target-list",
        default="scripts/pr-assistant/target-repos.txt",
        help="Compatibility target-repos.txt path.",
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
        help="Run manifest validation and compare the generated coverage baseline.",
    )
    verify_parser.add_argument(
        "--target-list",
        default="scripts/pr-assistant/target-repos.txt",
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


def validate_manifest(manifest: dict[str, Any], manifest_path: pathlib.Path) -> None:
    if not isinstance(manifest, dict):
        raise SystemExit(f"{manifest_path} must contain a JSON object at the root.")

    for key in ("schema_version", "baseline_date", "baseline_artifact", "canonical_source", "repos"):
        if key not in manifest:
            raise SystemExit(f"{manifest_path} missing required root key: {key}")

    if manifest["schema_version"] != 1:
        raise SystemExit(f"{manifest_path} has unsupported schema_version={manifest['schema_version']}")

    baseline_date = manifest["baseline_date"]
    if not isinstance(baseline_date, str) or len(baseline_date) != 10:
        raise SystemExit(f"{manifest_path} baseline_date must be YYYY-MM-DD")

    baseline_artifact = manifest["baseline_artifact"]
    if not isinstance(baseline_artifact, str) or not baseline_artifact.endswith(".json"):
        raise SystemExit(f"{manifest_path} baseline_artifact must be a .json path")

    canonical = manifest["canonical_source"]
    if not isinstance(canonical, dict):
        raise SystemExit(f"{manifest_path} canonical_source must be an object")
    for key in ("repo", "workflow_path", "step1_path"):
        value = canonical.get(key)
        if not isinstance(value, str) or not value:
            raise SystemExit(f"{manifest_path} canonical_source.{key} must be a non-empty string")

    repos = manifest["repos"]
    if not isinstance(repos, list) or not repos:
        raise SystemExit(f"{manifest_path} repos must be a non-empty array")

    seen_repos: set[str] = set()
    for index, repo_entry in enumerate(repos, start=1):
        if not isinstance(repo_entry, dict):
            raise SystemExit(f"{manifest_path} repos[{index}] must be an object")
        missing = [field for field in REQUIRED_REPO_FIELDS if field not in repo_entry]
        if missing:
            raise SystemExit(f"{manifest_path} repos[{index}] missing required fields: {', '.join(missing)}")

        repo = repo_entry["repo"]
        if not isinstance(repo, str) or "/" not in repo or repo.startswith("/") or repo.endswith("/"):
            raise SystemExit(f"{manifest_path} repos[{index}].repo must be org/repo")
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


def render_target_repos(manifest: dict[str, Any]) -> str:
    header = textwrap.dedent(
        """\
        # Derived compatibility artifact for PR Assistant rollout tooling.
        # Canonical source of truth: scripts/pr-assistant/repo-policy-manifest.json
        # Regenerate / verify:
        #   python3 scripts/pr-assistant/repo-policy-manifest.py sync-target-repos --check
        #   python3 scripts/pr-assistant/repo-policy-manifest.py sync-target-repos --write
        # Enabled copy-deploy targets only. Canonical source repo remains merglbot-core/github.
        """
    )
    lines = [header.rstrip(), ""]
    last_org = None
    for entry in manifest["repos"]:
        if not entry["enabled"]:
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
        and "Documentation Obligation State: unknown" in workflow_content
        and "blocked_missing_authority" in workflow_content
    )

    workflow_present = workflow_content is not None
    step1_present = expected_step1 is None or step1_content is not None
    workflow_matches = bool(workflow_sha and workflow_sha == canonical_workflow_sha)
    step1_matches = bool(step1_sha and step1_sha == canonical_step1_sha) if expected_step1 else True

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
        phase05_canary_eligibility = "disabled"
    elif repo_entry["admission_state"] != "phase05_candidate":
        phase05_canary_eligibility = "not_requested"
    elif not phase03_contract_compliant:
        phase05_canary_eligibility = "blocked_contract_drift"
    else:
        phase05_canary_eligibility = "eligible"

    return RepoAudit(
        repo=repo,
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
        phase05_canary_eligibility=phase05_canary_eligibility,
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
                "phase05_canary_eligibility": audit.phase05_canary_eligibility,
            }
        )

    summary = {
        "repo_count": len(repo_entries),
        "enabled_count": sum(1 for entry in repo_entries if entry["enabled"]),
        "canonical_copy_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "deployed_canonical_copy"
        ),
        "drift_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "deployed_copy_drift"
        ),
        "not_deployed_count": sum(
            1 for entry in repo_entries if entry["deployment_state"] == "not_deployed"
        ),
        "phase03_contract_compliant_count": sum(
            1 for entry in repo_entries if entry["phase03_contract_compliant"]
        ),
        "phase05_eligible_count": sum(
            1 for entry in repo_entries if entry["phase05_canary_eligibility"] == "eligible"
        ),
    }

    return {
        "schema_version": 1,
        "baseline_date": manifest["baseline_date"],
        "manifest_path": "scripts/pr-assistant/repo-policy-manifest.json",
        "target_list_path": "scripts/pr-assistant/target-repos.txt",
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
