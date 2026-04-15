#!/usr/bin/env python3
"""Weekly autonomous Dependabot closeout for Merglbot ENT repositories.

The script is intentionally conservative about evidence and aggressive only
after the configured gates are proven on the live PR head. It emits one strict
JSON object to stdout and writes audit artifacts into the selected output dir.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import difflib
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEPENDABOT_LOGINS = {"dependabot[bot]", "app/dependabot"}
MERGLBOT_REVIEW_WORKFLOW_NAME = "Merglbot PR Assistant v3 (On-Demand Multi-Model)"
OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REPOSITORY_RE = re.compile(r"\[`([^`]+/[^`]+)`\]\(https://github.com/[^)]+\).*\|\s*Active\s*\|")
MERGLBOT_REVIEW_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_WAIT_SECONDS", "1500"))
MERGLBOT_REVIEW_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REVIEW_POLL_SECONDS", "60"))
REBASE_WAIT_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_WAIT_SECONDS", "600"))
REBASE_POLL_SECONDS = int(os.environ.get("ENT_DEPENDABOT_REBASE_POLL_SECONDS", "60"))
OPEN_ITEM_LIST_LIMIT = 1000
TRACKING_COMMENT_MAX_CHARS = 60000
TRACKING_RECEIPT_ITEM_LIMIT = 10
TRACKING_RECEIPT_TEXT_LIMIT = 240
APP_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}
REPO_LOCAL_SCOPE_FILE = Path(__file__).with_name("ent_repository_scope.txt")
CURRENT_REPO_ENV = "ENT_DEPENDABOT_CURRENT_REPO"
BAD_CREDENTIAL_MARKERS = (
    "HTTP 401",
    "Bad credentials",
    "Requires authentication",
)
MERGE_READY_STATES = {"CLEAN", "HAS_HOOKS"}
MERGE_REVIEW_GATE_STATE = "REVIEW_REQUIRED"
TERMINAL_MERGLBOT_REVIEW_BLOCKERS = {"review_not_approved_for_closeout"}
DEFAULT_VALIDATOR_PROFILE = "maximum_autonomy_v2"
VALIDATOR_PROFILES = {"strict_lockfile_v1", DEFAULT_VALIDATOR_PROFILE}
PACKAGE_JSON_DEPENDENCY_KEYS = {
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
}
PACKAGE_JSON_LOCKFILE_NAMES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lock",
    "bun.lockb",
}

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


def base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def github_app_auth_configured() -> bool:
    app_id_present = bool(os.environ.get("ENT_DEPENDABOT_APP_ID"))
    key_present = bool(os.environ.get("ENT_DEPENDABOT_APP_PRIVATE_KEY"))
    if app_id_present != key_present:
        raise GhError("ENT_DEPENDABOT_APP_ID and ENT_DEPENDABOT_APP_PRIVATE_KEY must be configured together")
    return app_id_present and key_present


def github_app_private_key() -> str:
    key = os.environ.get("ENT_DEPENDABOT_APP_PRIVATE_KEY", "")
    if "\\n" in key and "\n" not in key:
        key = key.replace("\\n", "\n")
    return key


def github_app_jwt() -> str:
    app_id = os.environ.get("ENT_DEPENDABOT_APP_ID", "").strip()
    key = github_app_private_key()
    if not app_id or not key:
        raise GhError("ENT_DEPENDABOT_APP_ID and ENT_DEPENDABOT_APP_PRIVATE_KEY are required for GitHub App auth")
    try:
        int(app_id)
    except ValueError as exc:
        raise GhError("ENT_DEPENDABOT_APP_ID must be the numeric GitHub App ID") from exc
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {"iat": now - 60, "exp": now + 540, "iss": app_id}
    signing_input = f"{base64url(json.dumps(header, separators=(',', ':')).encode())}.{base64url(json.dumps(payload, separators=(',', ':')).encode())}"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as key_file:
        key_file.write(key)
        key_file.flush()
        os.chmod(key_file.name, 0o600)
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            check=False,
            input=signing_input.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    if proc.returncode != 0:
        raise GhError(proc.stderr.decode("utf-8", errors="replace") or "openssl signing failed")
    return f"{signing_input}.{base64url(proc.stdout)}"


def github_api_direct(endpoint: str, token: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"https://api.github.com/{endpoint.lstrip('/')}",
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "merglbot-ent-dependabot-closeout",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body or "null")
    except Exception as exc:
        raise GhError(f"GitHub App API request failed for {endpoint}: {exc}") from exc


def installation_id_for_owner(owner: str, jwt: str) -> int:
    for endpoint in (f"orgs/{owner}/installation", f"users/{owner}/installation"):
        try:
            installation = github_api_direct(endpoint, jwt)
            installation_id = installation.get("id")
            if installation_id:
                return int(installation_id)
        except GhError as exc:
            if "HTTP Error 404" in str(exc):
                continue
            raise
    raise GhError(f"GitHub App is not installed for owner {owner}")


def installation_token_for_owner(owner: str) -> str:
    cached = APP_TOKEN_CACHE.get(owner)
    if cached and cached[1] > datetime.now(timezone.utc) + timedelta(minutes=5):
        return cached[0]
    jwt = github_app_jwt()
    installation_id = installation_id_for_owner(owner, jwt)
    token_payload = github_api_direct(f"app/installations/{installation_id}/access_tokens", jwt, method="POST", payload={})
    token = token_payload.get("token")
    expires_at = token_payload.get("expires_at")
    if not token or not expires_at:
        raise GhError(f"GitHub App installation token response missing token for {owner}")
    expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    APP_TOKEN_CACHE[owner] = (token, expires)
    return token


def bad_credentials_seen(text: str) -> bool:
    return any(marker in text for marker in BAD_CREDENTIAL_MARKERS)


def invalidate_app_token_for_owner(owner: str) -> None:
    APP_TOKEN_CACHE.pop(owner, None)


def refresh_current_repo_token() -> bool:
    repo = os.environ.get(CURRENT_REPO_ENV, "")
    if not repo or "/" not in repo or not github_app_auth_configured():
        return False
    owner = repo.split("/", 1)[0]
    invalidate_app_token_for_owner(owner)
    os.environ["GH_TOKEN"] = installation_token_for_owner(owner)
    return True


@contextmanager
def gh_token_for_repo(repo: str):
    previous = os.environ.get("GH_TOKEN")
    previous_repo = os.environ.get(CURRENT_REPO_ENV)
    if github_app_auth_configured():
        os.environ["GH_TOKEN"] = installation_token_for_owner(repo.split("/", 1)[0])
    os.environ[CURRENT_REPO_ENV] = repo
    try:
        yield
    finally:
        if previous_repo is None:
            os.environ.pop(CURRENT_REPO_ENV, None)
        else:
            os.environ[CURRENT_REPO_ENV] = previous_repo
        if previous is None:
            os.environ.pop("GH_TOKEN", None)
        else:
            os.environ["GH_TOKEN"] = previous


def run_cmd(args: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        check=False,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0 and bad_credentials_seen(f"{proc.stderr}\n{proc.stdout}") and refresh_current_repo_token():
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


def gh_api_json_with_input(endpoint: str, payload: dict[str, Any], *extra: str, method: str = "POST", check: bool = True) -> tuple[int, Any, str]:
    proc = run_cmd(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28", endpoint, "-X", method, "--input", "-", *extra],
        check=False,
        input_text=json.dumps(payload),
    )
    if check and proc.returncode != 0:
        raise GhError(proc.stderr.strip() or proc.stdout.strip() or f"gh api {endpoint} failed")
    parsed: Any = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
        except json.JSONDecodeError:
            parsed = proc.stdout.strip()
    return proc.returncode, parsed, proc.stderr.strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_repository_map(text: str, *, allow_plain_lines: bool = False) -> list[str]:
    repos: list[str] = []
    for line in text.splitlines():
        match = REPOSITORY_RE.search(line)
        if match and OWNER_REPO_RE.fullmatch(match.group(1)):
            repos.append(match.group(1))
            continue
        stripped = line.strip()
        if allow_plain_lines and OWNER_REPO_RE.fullmatch(stripped):
            repos.append(stripped)
    return sorted(dict.fromkeys(repos))


def fetch_repository_map() -> str:
    with gh_token_for_repo("merglbot-public/docs"):
        content = gh_api_json("repos/merglbot-public/docs/contents/REPOSITORY_MAP.md?ref=main")
    encoded = content.get("content")
    if not encoded:
        raise GhError("REPOSITORY_MAP.md content missing from GitHub API response")
    return base64.b64decode(encoded).decode("utf-8")


def load_repo_scope(scope_file: Path | None, *, use_repo_local_default: bool = False) -> list[str]:
    if scope_file and scope_file.exists():
        text = scope_file.read_text(encoding="utf-8")
    elif use_repo_local_default:
        text = REPO_LOCAL_SCOPE_FILE.read_text(encoding="utf-8")
    else:
        text = fetch_repository_map()
    repos = parse_repository_map(text, allow_plain_lines=bool(scope_file or use_repo_local_default))
    if len(repos) != 42:
        raise GhError(f"expected 42 in-scope active repositories, got {len(repos)}")
    if any(repo.startswith("Merglevsky-cz/") or repo.startswith("merglevsky-cz/") for repo in repos):
        raise GhError("out-of-scope Merglevsky-cz repository appeared in scope")
    return repos


def load_single_repo_scope(scope_file: Path | None) -> list[str]:
    # In GitHub Actions, never trust a branch-local mirror as the authoritative
    # boundary. Local diagnostics can use the repo-local mirror to avoid
    # unnecessary cross-repo token requirements.
    if os.environ.get("GITHUB_ACTIONS") == "true" or scope_file:
        return load_repo_scope(scope_file)
    return load_repo_scope(scope_file, use_repo_local_default=True)


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
    base_sha: str
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
    merglbot_dispatch: dict[str, Any] | None = None
    update_branch: dict[str, Any] | None = None
    post_merge: dict[str, Any] | None = None
    validated_scope_class: str | None = None
    scope_validator_evidence: list[str] = field(default_factory=list)
    would_dispatch_merglbot_review: bool = False
    would_update_branch: bool = False
    superseded_by: str | None = None
    close_reopen_condition: str | None = None
    required_check_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    would_start_fix_loop: bool = False
    would_heal_required_checks: bool = False
    fix_iterations: int = 0
    review_iterations: int = 0
    fix_commits: list[str] = field(default_factory=list)
    merglbot_findings_ledger: list[dict[str, Any]] = field(default_factory=list)
    cursor_findings_ledger: list[dict[str, Any]] = field(default_factory=list)
    ci_healing_actions: list[dict[str, Any]] = field(default_factory=list)
    terminal_close_loop_verdict: str | None = None


def reject_if_head_changed(receipt: ItemReceipt, refreshed: PullRequest, blocker: str) -> bool:
    """Fail closed when a refreshed PR snapshot no longer matches reviewed head."""
    if refreshed.head_sha == receipt.head_sha:
        return False
    receipt.blockers.append(blocker)
    receipt.head_sha = refreshed.head_sha
    return True


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
            "number,title,url,author,headRefOid,baseRefOid,baseRefName,headRefName,isDraft,mergeStateStatus,updatedAt",
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
                base_sha=str(item.get("baseRefOid") or ""),
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
    with gh_token_for_repo(f"{owner}/{repo}"):
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


@dataclass(frozen=True)
class DependabotUpdate:
    dependency: str
    from_version: str
    to_version: str
    path_hint: str = ""


def normalize_dependency_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def normalize_path_hint(value: str) -> str:
    cleaned = value.strip().strip(".")
    cleaned = re.sub(r"^(?:the\s+)?", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip("/")
    return cleaned.lower()


def parse_dependabot_update_title(title: str) -> DependabotUpdate | None:
    match = re.search(r"^Bump\s+(.+?)\s+from\s+([^\s]+)\s+to\s+([^\s]+)(?:\s+in\s+(.+))?$", title.strip(), re.IGNORECASE)
    if not match:
        return None
    return DependabotUpdate(
        dependency=normalize_dependency_name(match.group(1)),
        from_version=match.group(2).strip(),
        to_version=match.group(3).strip().rstrip(","),
        path_hint=normalize_path_hint(match.group(4) or ""),
    )


def comparable_version_parts(version: str) -> tuple[int, ...] | None:
    cleaned = version.strip().lstrip("vV")
    numbers = re.findall(r"\d+", cleaned)
    if not numbers:
        return None
    return tuple(int(part) for part in numbers[:8])


def compare_versions(left: str, right: str) -> int | None:
    left_parts = comparable_version_parts(left)
    right_parts = comparable_version_parts(right)
    if left_parts is None or right_parts is None:
        return None
    max_len = max(len(left_parts), len(right_parts))
    padded_left = left_parts + (0,) * (max_len - len(left_parts))
    padded_right = right_parts + (0,) * (max_len - len(right_parts))
    return (padded_left > padded_right) - (padded_left < padded_right)


def is_exact_stable_version_for_autoclose(version: str) -> bool:
    return re.fullmatch(r"v?\d+(?:\.\d+){0,4}", version.strip()) is not None


def package_json_dependency_version(payload: dict[str, Any], dependency: str) -> str | None:
    dependency_key = normalize_dependency_name(dependency)
    for section_name in PACKAGE_JSON_DEPENDENCY_KEYS:
        section = payload.get(section_name) or {}
        if not isinstance(section, dict):
            continue
        for name, version in section.items():
            if normalize_dependency_name(str(name)) == dependency_key:
                return str(version)
    return None


def same_dependabot_update_family(left: DependabotUpdate, right: DependabotUpdate) -> bool:
    if left.dependency != right.dependency:
        return False
    return bool(left.path_hint and right.path_hint and left.path_hint == right.path_hint)


def classify_close_candidate(
    pr: PullRequest,
    files: list[str],
    sibling_prs: list[PullRequest],
) -> tuple[str, list[str], str | None, str | None] | None:
    update = parse_dependabot_update_title(pr.title)
    if not files:
        return (
            "AUTO_CLOSE_EMPTY_DIFF",
            ["current PR diff has no changed files"],
            None,
            f"Reopen or create a new Dependabot PR if the dependency update is still needed on the current `{pr.base_ref}` branch.",
        )
    if not update:
        return None

    for sibling in sibling_prs:
        if sibling.number == pr.number:
            continue
        sibling_update = parse_dependabot_update_title(sibling.title)
        if not sibling_update or not same_dependabot_update_family(update, sibling_update):
            continue
        version_cmp = compare_versions(sibling_update.to_version, update.to_version)
        if version_cmp is not None and version_cmp > 0:
            return (
                "AUTO_CLOSE_OLDER_SIBLING",
                [
                    f"newer sibling PR exists: {sibling.url}",
                    f"{update.dependency} target {update.to_version} is older than sibling target {sibling_update.to_version}",
                ],
                sibling.url,
                f"Reopen only if newer sibling {sibling.url} is invalid and `{update.dependency}` still needs `{update.to_version}` specifically.",
            )

    package_manifests = [path for path in files if is_npm_manifest(path)]
    for path in package_manifests:
        manifest = parse_json_file(pr.repo, path, pr.base_sha)
        if manifest is None:
            return (
                "AUTO_CLOSE_DEPENDENCY_ABSENT",
                [f"{path} does not exist on base `{pr.base_ref}`"],
                None,
                f"Reopen only if `{path}` is restored and the Dependabot update still applies.",
            )
        current_version = package_json_dependency_version(manifest, update.dependency)
        if current_version is None:
            return (
                "AUTO_CLOSE_DEPENDENCY_ABSENT",
                [f"{update.dependency} is not present in {path} on base `{pr.base_ref}`"],
                None,
                f"Reopen only if `{update.dependency}` is reintroduced in `{path}` and still needs this update.",
            )
        if not is_exact_stable_version_for_autoclose(current_version) or not is_exact_stable_version_for_autoclose(update.to_version):
            continue
        version_cmp = compare_versions(current_version, update.to_version)
        if version_cmp is not None and version_cmp >= 0:
            return (
                "AUTO_CLOSE_SUPERSEDED",
                [
                    f"{path} on base `{pr.base_ref}` already has {update.dependency} at {current_version}",
                    f"PR target version is {update.to_version}",
                ],
                None,
                f"Reopen only if `{path}` no longer contains `{update.dependency}` at `{current_version}` or newer.",
            )

    for path in [item for item in files if is_workflow_file(item)]:
        if repo_file_text(pr.repo, path, pr.base_sha) is None:
            return (
                "AUTO_CLOSE_REPO_OR_PATH_DEPRECATED",
                [f"{path} no longer exists on base `{pr.base_ref}`"],
                None,
                f"Reopen only if `{path}` is restored and still needs the Dependabot workflow ref update.",
            )

    return None


def repo_file_text(repo: str, path: str, ref: str) -> str | None:
    endpoint = repo_endpoint(repo, f"contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}")
    proc = run_cmd(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            endpoint,
        ],
        check=False,
    )
    if proc.returncode != 0:
        combined = f"{proc.stderr}\n{proc.stdout}"
        if "404" in combined or "Not Found" in combined:
            return None
        raise GhError(proc.stderr.strip() or proc.stdout.strip() or f"gh api {endpoint} failed")
    payload = json.loads(proc.stdout or "{}")
    encoded = payload.get("content") if isinstance(payload, dict) else None
    if not encoded:
        return None
    return base64.b64decode(str(encoded).encode("utf-8")).decode("utf-8")


def is_dependency_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in DEPENDENCY_FILE_PATTERNS)


def is_mixed_purpose_manifest(path: str) -> bool:
    return any(pattern.search(path) for pattern in MIXED_PURPOSE_MANIFEST_PATTERNS)


def is_sensitive_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in SENSITIVE_FILE_PATTERNS)


def is_npm_manifest(path: str) -> bool:
    return path.endswith("package.json")


def is_workflow_file(path: str) -> bool:
    return path.startswith(".github/workflows/") and path.endswith((".yml", ".yaml"))


def sibling_lockfiles(path: str, files: list[str]) -> list[str]:
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    candidates = {
        f"{parent}/{name}" if parent else name
        for name in PACKAGE_JSON_LOCKFILE_NAMES
    }
    return sorted(candidates.intersection(files))


def parse_json_file(repo: str, path: str, ref: str) -> dict[str, Any] | None:
    text = repo_file_text(repo, path, ref)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GhError(f"{repo}:{path}@{ref} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GhError(f"{repo}:{path}@{ref} is not a JSON object")
    return payload


def validate_npm_manifest_payloads(
    before: dict[str, Any],
    after: dict[str, Any],
    path: str,
    files: list[str],
) -> tuple[bool, list[str], list[str]]:
    blockers: list[str] = []
    evidence: list[str] = []
    changed_keys = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    dependency_keys = [key for key in changed_keys if key in PACKAGE_JSON_DEPENDENCY_KEYS]
    non_dependency_keys = [key for key in changed_keys if key not in PACKAGE_JSON_DEPENDENCY_KEYS]
    if non_dependency_keys:
        blockers.append(f"manifest_non_dependency_keys:{path}:{','.join(non_dependency_keys)}")
    if not dependency_keys:
        blockers.append(f"manifest_no_dependency_section_change:{path}")
    for key in dependency_keys:
        old = before.get(key) or {}
        new = after.get(key) or {}
        if not isinstance(old, dict) or not isinstance(new, dict):
            blockers.append(f"manifest_dependency_section_not_object:{path}:{key}")
            continue
        if set(old) != set(new):
            blockers.append(f"manifest_dependency_name_set_changed:{path}:{key}")
            continue
        invalid_values = [
            dep
            for dep in sorted(old)
            if not isinstance(dep, str) or not isinstance(old.get(dep), str) or not isinstance(new.get(dep), str)
        ]
        if invalid_values:
            blockers.append(f"manifest_dependency_values_not_strings:{path}:{key}:{','.join(sorted(invalid_values))}")
    locks = sibling_lockfiles(path, files)
    if not locks:
        blockers.append(f"manifest_without_matching_lockfile:{path}")
    evidence.append(f"manifest_dependency_keys={','.join(dependency_keys) or 'none'}")
    evidence.append(f"manifest_lockfiles={','.join(locks) or 'none'}")
    return not blockers, blockers, evidence


def validate_npm_manifest_dependency_only(
    repo: str,
    path: str,
    *,
    base_sha: str,
    head_sha: str,
    files: list[str],
) -> tuple[bool, list[str], list[str]]:
    before = parse_json_file(repo, path, base_sha)
    after = parse_json_file(repo, path, head_sha)
    if before is None or after is None:
        return False, [f"manifest_missing_at_ref:{path}"], []
    return validate_npm_manifest_payloads(before, after, path, files)


def parse_uses_ref(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    match = re.match(r"^-?\s*uses:\s*['\"]?(.+?)@([^'\"\s#]+)", stripped)
    if not match:
        return None
    target, ref = match.group(1), match.group(2)
    if target.startswith("./"):
        return None
    if not target or not ref:
        return None
    return target, ref


def validate_workflow_ref_only_text(before: str, after: str, path: str) -> tuple[bool, list[str], list[str]]:
    blockers: list[str] = []
    evidence: list[str] = []
    changed_refs = 0
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            blockers.append(f"workflow_structural_change:{path}")
            continue
        for old, new in zip(before_lines[i1:i2], after_lines[j1:j2], strict=True):
            old_ref = parse_uses_ref(old)
            new_ref = parse_uses_ref(new)
            if not old_ref or not new_ref:
                blockers.append(f"workflow_non_uses_change:{path}")
                continue
            if old_ref[0] != new_ref[0]:
                blockers.append(f"workflow_uses_target_changed:{path}:{old_ref[0]}->{new_ref[0]}")
                continue
            if old_ref[1] == new_ref[1]:
                blockers.append(f"workflow_uses_ref_unchanged:{path}:{old_ref[0]}")
                continue
            changed_refs += 1
            evidence.append(f"workflow_uses_ref_bump:{path}:{old_ref[0]}:{old_ref[1]}->{new_ref[1]}")
    if changed_refs == 0:
        blockers.append(f"workflow_no_uses_ref_bump:{path}")
    return not blockers, blockers, evidence


def validate_workflow_ref_only(repo: str, path: str, *, base_sha: str, head_sha: str) -> tuple[bool, list[str], list[str]]:
    before = repo_file_text(repo, path, base_sha)
    after = repo_file_text(repo, path, head_sha)
    if before is None or after is None:
        return False, [f"workflow_missing_at_ref:{path}"], []
    return validate_workflow_ref_only_text(before, after, path)


def validate_change_scope(
    repo: str,
    number: int,
    files: list[str],
    *,
    base_sha: str,
    head_sha: str,
    validator_profile: str,
) -> tuple[bool, list[str], list[str], str, list[str]]:
    if validator_profile not in VALIDATOR_PROFILES:
        raise GhError(f"unknown validator_profile: {validator_profile}")
    if validator_profile == "strict_lockfile_v1":
        ok, blockers, dependency_files = classify_change_scope(files)
        return ok, blockers, dependency_files, "LOCKFILE_ONLY" if ok else "BLOCKED_CHANGE_SCOPE", []

    blockers: list[str] = []
    evidence: list[str] = [f"validator_profile={validator_profile}"]
    dependency_files: list[str] = []
    classes: set[str] = set()
    for path in files:
        if is_dependency_file(path):
            dependency_files.append(path)
            classes.add("LOCKFILE_ONLY")
            continue
        if is_npm_manifest(path):
            ok, item_blockers, item_evidence = validate_npm_manifest_dependency_only(
                repo,
                path,
                base_sha=base_sha,
                head_sha=head_sha,
                files=files,
            )
            evidence.extend(item_evidence)
            if ok:
                classes.add("VALIDATED_MANIFEST_DEP_ONLY")
            else:
                blockers.extend(f"manifest_content_validation:{blocker}" for blocker in item_blockers)
            continue
        if is_workflow_file(path):
            ok, item_blockers, item_evidence = validate_workflow_ref_only(repo, path, base_sha=base_sha, head_sha=head_sha)
            evidence.extend(item_evidence)
            if ok:
                classes.add("VALIDATED_WORKFLOW_REF_ONLY")
            else:
                blockers.extend(f"workflow_content_validation:{blocker}" for blocker in item_blockers)
            continue
        if is_sensitive_file(path):
            blockers.append(f"sensitive_file_scope:{path}")
            continue
        if is_mixed_purpose_manifest(path):
            blockers.append(f"mixed_manifest_validator_unavailable:{path}")
            continue
        blockers.append(f"non_manifest_lockfile_scope:{path}")
    if blockers:
        return False, blockers, dependency_files, "BLOCKED_SCOPE_CONTENT_VALIDATION", evidence
    return True, [], dependency_files, "+".join(sorted(classes)) or "NO_CHANGE", evidence


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
            "number,title,url,author,headRefOid,baseRefOid,baseRefName,headRefName,isDraft,mergeStateStatus,updatedAt,state",
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
        base_sha=str(item.get("baseRefOid") or ""),
        base_ref=str(item.get("baseRefName") or "main"),
        head_ref=str(item.get("headRefName") or ""),
        is_draft=bool(item.get("isDraft")),
        merge_state=str(item.get("mergeStateStatus") or "UNKNOWN"),
        updated_at=str(item.get("updatedAt") or ""),
    )


def classify_required_check_blocker(check: dict[str, Any]) -> dict[str, Any]:
    name = str(check.get("name") or "unknown")
    bucket = str(check.get("bucket") or check.get("state") or "unknown").lower()
    state = str(check.get("state") or "").lower()
    normalized = name.lower()
    if bucket == "pass":
        reason = "pass"
        category = "ok"
    elif bucket in {"fail", "failure", "cancelled", "timed_out", "action_required"} or state in {"failure", "error", "cancelled", "timed_out"}:
        reason = f"check_failed_real:{name}:{bucket or state}"
        category = "check_failed_real"
    elif bucket in {"pending", "expected", "waiting"}:
        if "codeql" in normalized or normalized.startswith("analyze "):
            reason = f"required_check_drift:pending_codeql_or_analysis:{name}"
            category = "stale_or_pending_analysis_context"
        elif "gitleaks" in normalized or "secret" in normalized:
            reason = f"required_check_drift:pending_security_context:{name}"
            category = "stale_or_pending_security_context"
        else:
            reason = f"required_check_drift:pending_never_emits_or_slow:{name}"
            category = "pending_or_never_emits"
    elif bucket in {"skipping", "skipped", "neutral"}:
        if "codeql" in normalized or normalized.startswith("analyze "):
            reason = f"required_check_drift:skipped_codeql_or_analysis:{name}"
            category = "skipped_analysis_context"
        else:
            reason = f"required_check_drift:skipped_or_neutral:{name}"
            category = "skipped_or_neutral"
    else:
        reason = f"required_check_unknown_state:{name}:{bucket or state}"
        category = "unknown_required_check_state"
    return {
        "name": name,
        "bucket": bucket,
        "state": state,
        "category": category,
        "reason": reason,
        "completed_at": check.get("completedAt"),
        "link": check.get("link"),
    }


def required_checks(repo: str, number: int) -> tuple[bool, list[dict[str, Any]], list[str], list[dict[str, Any]]]:
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
        reason = proc.stderr.strip() or "required_checks_lookup_failed"
        return False, [], [reason], [{"category": "lookup_failed", "reason": reason}]
    checks = json.loads(proc.stdout or "[]")
    diagnostics = [classify_required_check_blocker(check) for check in checks if check.get("bucket") != "pass"]
    blockers = [str(item["reason"]) for item in diagnostics]
    return len(blockers) == 0, checks, blockers, diagnostics


def required_check_healing_actions(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map required-check diagnostics to the next safe autonomous action.

    This function only plans actions. The actual write-capable close-loop lane
    must re-read PR head/check truth immediately before rerunning checks or
    pushing fixes.
    """
    actions: list[dict[str, Any]] = []
    for item in diagnostics:
        category = str(item.get("category") or "")
        name = str(item.get("name") or "unknown")
        reason = str(item.get("reason") or "")
        if category in {
            "stale_or_pending_analysis_context",
            "stale_or_pending_security_context",
            "pending_or_never_emits",
            "skipped_analysis_context",
            "skipped_or_neutral",
        }:
            actions.append(
                {
                    "check": name,
                    "action": "diagnose_or_rerun_required_check",
                    "category": category,
                    "reason": reason,
                    "requires_snapshot": True,
                }
            )
        elif category == "check_failed_real":
            actions.append(
                {
                    "check": name,
                    "action": "start_minimal_pr_branch_fix_loop",
                    "category": category,
                    "reason": reason,
                    "requires_same_pr_branch": True,
                }
            )
        else:
            actions.append(
                {
                    "check": name,
                    "action": "classify_before_mutation",
                    "category": category,
                    "reason": reason,
                    "fail_closed": True,
                }
            )
    return actions


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


def merglbot_dispatch_inputs(number: int, head_sha: str) -> dict[str, str]:
    return {
        "pr_number": str(number),
        "review_mode": "light",
        "include_retro": "false",
        "diff_scope": "auto",
        "expected_head_sha": head_sha,
    }


def is_current_head_merglbot_terminal_blocker(payload: dict[str, Any], head_sha: str) -> bool:
    if payload.get("ok"):
        return False
    review_head = payload.get("review_head_sha") or payload.get("head_sha")
    if review_head != head_sha:
        return False
    if payload.get("current_head_match") is False:
        return False
    verdict = str(payload.get("verdict") or "").strip().lower()
    status = str(payload.get("status") or "").strip().lower()
    blockers = payload.get("blockers")
    terminal_blockers = [
        str(blocker)
        for blocker in (blockers if isinstance(blockers, list) else [])
        if str(blocker) in TERMINAL_MERGLBOT_REVIEW_BLOCKERS
    ]
    if verdict in {"approved_for_closeout", "approved"} and status == "success":
        return False
    return bool(terminal_blockers) or verdict in {"changes_required", "blocked", "needs_work"} or status in {"blocked", "failed"}


def merglbot_findings_ledger(payload: dict[str, Any], head_sha: str) -> list[dict[str, Any]]:
    blockers = payload.get("blockers")
    blocker_list = blockers if isinstance(blockers, list) else []
    return [
        {
            "source": "merglbot",
            "head_sha": head_sha,
            "review_head_sha": payload.get("review_head_sha") or payload.get("head_sha"),
            "verdict": payload.get("verdict"),
            "status": payload.get("status"),
            "comment_url": payload.get("comment_url"),
            "blockers": [str(item) for item in blocker_list],
            "next_action": "minimal_same_branch_fix_then_rerun_merglbot",
        }
    ]


def cursor_findings_ledger(status: str | None, head_sha: str | None) -> list[dict[str, Any]]:
    if not status:
        return []
    if status in {"cursor_pass", "cursor_absent_not_required", "cursor_no_current_bug_signal"}:
        return []
    return [
        {
            "source": "cursor",
            "head_sha": head_sha,
            "status": status,
            "next_action": "minimal_same_branch_fix_then_rerun_cursor_or_recheck_current_head_summary",
        }
    ]


def mark_fix_loop_candidate(
    receipt: ItemReceipt,
    *,
    classification: str,
    evidence: str,
    max_fix_iterations: int,
    max_review_iterations: int,
) -> None:
    receipt.action = "would_start_fix_loop"
    receipt.classification = classification
    receipt.would_start_fix_loop = True
    receipt.terminal_close_loop_verdict = "PENDING_AUTONOMOUS_FIX_LOOP"
    receipt.evidence.append(evidence)
    receipt.evidence.append(f"max_fix_iterations={max_fix_iterations}")
    receipt.evidence.append(f"max_review_iterations={max_review_iterations}")


def find_merglbot_review_workflow(repo: str) -> dict[str, Any]:
    workflows = gh_api_json(repo_endpoint(repo, "actions/workflows?per_page=100"))
    for workflow in workflows.get("workflows", []):
        if workflow.get("name") == MERGLBOT_REVIEW_WORKFLOW_NAME and workflow.get("state") == "active":
            return workflow
    for workflow in workflows.get("workflows", []):
        if workflow.get("name") == MERGLBOT_REVIEW_WORKFLOW_NAME:
            return workflow
    raise GhError(f"merglbot_review_workflow_missing:{MERGLBOT_REVIEW_WORKFLOW_NAME}")


def latest_merglbot_dispatch_run(repo: str, workflow_id: int | str, head_ref: str, head_sha: str) -> dict[str, Any] | None:
    encoded_ref = quote(head_ref, safe="")
    runs = gh_api_json(repo_endpoint(repo, f"actions/workflows/{workflow_id}/runs?event=workflow_dispatch&branch={encoded_ref}&per_page=10"))
    for run in runs.get("workflow_runs", []):
        if run.get("head_sha") == head_sha:
            return {
                "id": run.get("id"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
                "head_sha": run.get("head_sha"),
                "head_branch": run.get("head_branch"),
            }
    return None


def trigger_merglbot_review(repo: str, number: int, head_ref: str, head_sha: str) -> dict[str, Any]:
    workflow = find_merglbot_review_workflow(repo)
    workflow_id = workflow.get("id") or workflow.get("path")
    if not workflow_id:
        raise GhError("merglbot_review_workflow_id_missing")
    payload = {
        "ref": head_ref,
        "inputs": merglbot_dispatch_inputs(number, head_sha),
    }
    endpoint = repo_endpoint(repo, f"actions/workflows/{workflow_id}/dispatches")
    gh_api_json_with_input(endpoint, payload, method="POST")
    run: dict[str, Any] | None = None
    deadline = time.time() + min(MERGLBOT_REVIEW_POLL_SECONDS * 2, 120)
    while time.time() <= deadline:
        time.sleep(5)
        run = latest_merglbot_dispatch_run(repo, workflow_id, head_ref, head_sha)
        if run:
            break
    return {
        "method": "workflow_dispatch",
        "workflow_name": workflow.get("name"),
        "workflow_id": workflow_id,
        "workflow_path": workflow.get("path"),
        "ref": head_ref,
        "head_sha": head_sha,
        "run_url": None if not run else run.get("html_url"),
        "run_id": None if not run else run.get("id"),
    }


def wait_for_merglbot(repo: str, number: int, head_ref: str, head_sha: str, *, apply: bool) -> dict[str, Any]:
    first = verify_merglbot(repo, number)
    if first.get("ok"):
        return first
    if is_current_head_merglbot_terminal_blocker(first, head_sha):
        return first
    if not apply:
        return first
    try:
        dispatch = trigger_merglbot_review(repo, number, head_ref, head_sha)
    except GhError as exc:
        message = str(exc)
        if "merglbot_review_workflow_missing" in message:
            blocker = "repo_enrollment:merglbot_workflow_dispatch_missing"
        elif "workflow_dispatch" in message and "trigger" in message:
            blocker = "repo_enrollment:merglbot_workflow_dispatch_missing"
        elif "403" in message or "Resource not accessible" in message:
            blocker = "app_capability:actions_write_missing_or_denied"
        else:
            blocker = f"merglbot_workflow_dispatch_failed:{message}"
        return {
            "ok": False,
            "blockers": [blocker],
            "dispatch": {
                "method": "workflow_dispatch",
                "ref": head_ref,
                "head_sha": head_sha,
            },
        }
    deadline = time.time() + MERGLBOT_REVIEW_WAIT_SECONDS
    latest = first
    while time.time() <= deadline:
        time.sleep(min(MERGLBOT_REVIEW_POLL_SECONDS, max(0, deadline - time.time())))
        latest = verify_merglbot(repo, number)
        latest["dispatch"] = dispatch
        if latest.get("ok"):
            return latest
        if is_current_head_merglbot_terminal_blocker(latest, head_sha):
            return latest
    latest.setdefault("blockers", []).append("merglbot_review_poll_timeout")
    latest["dispatch"] = dispatch
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


def request_update_branch(repo: str, number: int, expected_head_sha: str, *, apply: bool) -> dict[str, Any]:
    if not apply:
        return {
            "ok": False,
            "method": "update_branch_api",
            "blockers": ["update_branch_required"],
            "initial_head_sha": expected_head_sha,
            "dry_run": True,
        }
    endpoint = repo_endpoint(repo, f"pulls/{number}/update-branch")
    code, payload, stderr = gh_api_json_with_input(endpoint, {"expected_head_sha": expected_head_sha}, method="PUT", check=False)
    if code != 0:
        return {
            "ok": False,
            "method": "update_branch_api",
            "blockers": [stderr or "update_branch_api_failed"],
            "initial_head_sha": expected_head_sha,
        }
    deadline = time.time() + REBASE_WAIT_SECONDS
    latest = refresh_pr(repo, number)
    while time.time() <= deadline:
        time.sleep(min(REBASE_POLL_SECONDS, max(0, deadline - time.time())))
        latest = refresh_pr(repo, number)
        current = latest.head_sha
        if current != expected_head_sha or latest.merge_state != "BEHIND":
            return {
                "ok": True,
                "method": "update_branch_api",
                "initial_head_sha": expected_head_sha,
                "final_head_sha": latest.head_sha,
                "final_merge_state": latest.merge_state,
                "response": payload,
            }
    return {
        "ok": False,
        "method": "update_branch_api",
        "blockers": ["update_branch_poll_timeout"],
        "initial_head_sha": expected_head_sha,
        "final_head_sha": latest.head_sha,
        "final_merge_state": latest.merge_state,
        "response": payload,
    }


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


def close_comment(pr: PullRequest, classification: str, evidence: list[str], workflow_url: str, reopen_condition: str | None = None) -> str:
    return "\n".join(
        [
            "Dependabot PR closed by ENT weekly autonomous closeout.",
            "",
            f"- Classification: `{classification}`",
            f"- Evidence: {'; '.join(evidence)}",
            f"- Reopen condition: {reopen_condition or f'reopen or create a new Dependabot PR if the dependency update is still needed on the current `{pr.base_ref}` branch.'}",
            f"- Workflow run: {workflow_url or 'not available'}",
        ]
    )


def validate_file_scope_for_current_head(
    pr: PullRequest,
    receipt: ItemReceipt,
    *,
    validator_profile: str,
    sibling_prs: list[PullRequest],
) -> tuple[bool, PullRequest]:
    expected = refresh_pr(pr.repo, pr.number)
    receipt.head_sha = expected.head_sha
    files = pr_files(pr.repo, pr.number)
    after_scope = refresh_pr(pr.repo, pr.number)
    if after_scope.head_sha != expected.head_sha:
        receipt.blockers.append("head_changed_during_scope_check")
        receipt.head_sha = after_scope.head_sha
        return False, after_scope

    close_candidate = classify_close_candidate(pr, files, sibling_prs)
    if close_candidate:
        classification, evidence, successor_url, reopen_condition = close_candidate
        receipt.action = "would_close"
        receipt.classification = classification
        receipt.evidence.extend(evidence)
        receipt.superseded_by = successor_url
        receipt.close_reopen_condition = reopen_condition
        return False, after_scope

    scope_ok, scope_blockers, dependency_files, scope_class, scope_evidence = validate_change_scope(
        pr.repo,
        pr.number,
        files,
        base_sha=after_scope.base_sha,
        head_sha=after_scope.head_sha,
        validator_profile=validator_profile,
    )
    receipt.validated_scope_class = scope_class
    receipt.scope_validator_evidence.extend(scope_evidence)
    receipt.evidence.append(f"changed_files={len(files)}")
    receipt.evidence.append("dependency_files=" + ",".join(dependency_files))
    if not scope_ok:
        receipt.classification = scope_class if scope_class.startswith("BLOCKED_") else "BLOCKED_CHANGE_SCOPE"
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
    validator_profile: str,
    sibling_prs: list[PullRequest],
    autonomous_fix_loop: bool,
    max_fix_iterations: int,
    max_review_iterations: int,
) -> ItemReceipt:
    apply = mode == "apply"
    receipt = ItemReceipt(repo=pr.repo, pr_number=pr.number, url=pr.url, action="blocked", classification="BLOCKED", head_sha=pr.head_sha)
    if pr.author not in DEPENDABOT_LOGINS:
        receipt.blockers.append("not_dependabot_author")
        return receipt
    if pr.is_draft:
        receipt.blockers.append("draft_pr")
        return receipt
    scope_ok, refreshed = validate_file_scope_for_current_head(pr, receipt, validator_profile=validator_profile, sibling_prs=sibling_prs)
    if receipt.action == "would_close":
        if apply:
            receipt.action = "closed"
            receipt.comment_url = close_pr(
                pr.repo,
                pr.number,
                close_comment(pr, receipt.classification, receipt.evidence, workflow_url, receipt.close_reopen_condition),
            )
        return receipt
    if not scope_ok:
        return receipt

    if refreshed.merge_state == "BEHIND":
        if not apply:
            receipt.action = "would_update_branch"
            receipt.classification = "WOULD_UPDATE_BRANCH_THEN_REVALIDATE"
            receipt.would_update_branch = True
            receipt.update_branch = request_update_branch(pr.repo, pr.number, refreshed.head_sha, apply=False)
            receipt.evidence.append("PR is behind base; apply would call update-branch API with expected_head_sha and revalidate")
            return receipt
        receipt.evidence.append("PR was behind base after scope validation; requested update-branch API")
        update_branch = request_update_branch(pr.repo, pr.number, refreshed.head_sha, apply=apply)
        receipt.update_branch = update_branch
        if not update_branch.get("ok"):
            receipt.classification = "BLOCKED_UPDATE_BRANCH"
            receipt.blockers.extend([f"update_branch:{blocker}" for blocker in update_branch.get("blockers", [])])
            return receipt
        refreshed = refresh_pr(pr.repo, pr.number)
        scope_ok, refreshed = validate_file_scope_for_current_head(
            refreshed,
            receipt,
            validator_profile=validator_profile,
            sibling_prs=sibling_prs,
        )
        if receipt.action == "would_close":
            if apply:
                receipt.action = "closed"
                receipt.comment_url = close_pr(
                    pr.repo,
                    pr.number,
                    close_comment(refreshed, receipt.classification, receipt.evidence, workflow_url, receipt.close_reopen_condition),
                )
            return receipt
        if not scope_ok:
            return receipt
        if refreshed.merge_state == "BEHIND":
            receipt.classification = "BLOCKED_UPDATE_BRANCH"
            receipt.blockers.append("update_branch:still_behind_after_update")
            return receipt

    checks_ok, checks, check_blockers, check_diagnostics = required_checks(pr.repo, pr.number)
    receipt.required_check_diagnostics.extend(check_diagnostics)
    if not checks_ok:
        receipt.ci_healing_actions.extend(required_check_healing_actions(check_diagnostics))
        if not apply and autonomous_fix_loop:
            receipt.would_heal_required_checks = True
            if any(item.get("action") == "start_minimal_pr_branch_fix_loop" for item in receipt.ci_healing_actions):
                mark_fix_loop_candidate(
                    receipt,
                    classification="WOULD_START_AUTONOMOUS_FIX_LOOP",
                    evidence="Required checks include real failures; close-loop lane should apply a minimal same-branch fix and rerun gates.",
                    max_fix_iterations=max_fix_iterations,
                    max_review_iterations=max_review_iterations,
                )
            else:
                receipt.action = "would_heal_required_checks"
                receipt.classification = "WOULD_HEAL_REQUIRED_CHECKS"
                receipt.terminal_close_loop_verdict = "PENDING_REQUIRED_CHECK_HEALING"
                receipt.evidence.append("Required checks appear stale/pending/skipped; close-loop lane should rerun/diagnose before treating as a hard blocker.")
            receipt.blockers.extend([f"required_check:{blocker}" for blocker in check_blockers])
            receipt.evidence.append(f"required_checks={len(checks)}")
            return receipt
        receipt.blockers.extend([f"required_check:{blocker}" for blocker in check_blockers])
        receipt.evidence.append(f"required_checks={len(checks)}")
        return receipt

    merglbot = wait_for_merglbot(pr.repo, pr.number, refreshed.head_ref, refreshed.head_sha, apply=apply)
    receipt.merglbot_receipt = merglbot
    receipt.merglbot_dispatch = merglbot.get("dispatch")
    if not merglbot.get("ok"):
        if not apply:
            if is_current_head_merglbot_terminal_blocker(merglbot, refreshed.head_sha):
                receipt.merglbot_findings_ledger.extend(merglbot_findings_ledger(merglbot, refreshed.head_sha))
                if autonomous_fix_loop:
                    mark_fix_loop_candidate(
                        receipt,
                        classification="WOULD_START_AUTONOMOUS_FIX_LOOP",
                        evidence="Merglbot current-head review is terminal and not approved; close-loop lane should apply a minimal same-branch fix and rerun Merglbot.",
                        max_fix_iterations=max_fix_iterations,
                        max_review_iterations=max_review_iterations,
                    )
                    receipt.blockers.extend([f"merglbot:{blocker}" for blocker in merglbot.get("blockers", [])])
                    return receipt
                receipt.classification = "BLOCKED_MERGLBOT_CHANGES_REQUIRED"
                receipt.evidence.append("Merglbot current-head review is terminal and not approved for closeout")
                receipt.blockers.extend([f"merglbot:{blocker}" for blocker in merglbot.get("blockers", [])])
                return receipt
            receipt.action = "would_dispatch_review"
            receipt.classification = "WOULD_DISPATCH_MERGLBOT_REVIEW"
            receipt.would_dispatch_merglbot_review = True
            receipt.evidence.append("Merglbot receipt is missing or stale; apply would dispatch a head-bound workflow review and revalidate")
            return receipt
        verdict = str(merglbot.get("verdict") or "").lower()
        if verdict in {"changes_required", "blocked", "needs_work"} or "review_not_approved_for_closeout" in [str(item) for item in merglbot.get("blockers", [])]:
            receipt.classification = "BLOCKED_MERGLBOT_CHANGES_REQUIRED"
            receipt.evidence.append("Merglbot current-head review returned changes_required or non-approved closeout verdict")
            receipt.merglbot_findings_ledger.extend(merglbot_findings_ledger(merglbot, refreshed.head_sha))
        receipt.blockers.extend([f"merglbot:{blocker}" for blocker in merglbot.get("blockers", [])])
        return receipt

    cursor_ok, cursor = cursor_status(pr.repo, pr.number)
    receipt.cursor_status = cursor
    if not cursor_ok:
        receipt.cursor_findings_ledger.extend(cursor_findings_ledger(cursor, refreshed.head_sha))
        if not apply and autonomous_fix_loop:
            mark_fix_loop_candidate(
                receipt,
                classification="WOULD_START_AUTONOMOUS_FIX_LOOP",
                evidence="Cursor current-head signal is blocking; close-loop lane should apply a minimal same-branch fix and rerun/recheck Cursor.",
                max_fix_iterations=max_fix_iterations,
                max_review_iterations=max_review_iterations,
            )
            receipt.blockers.append(cursor)
            return receipt
        receipt.blockers.append(cursor)
        return receipt

    refreshed = refresh_pr(pr.repo, pr.number)
    if reject_if_head_changed(receipt, refreshed, "head_changed_after_review"):
        return receipt

    if refreshed.merge_state == MERGE_REVIEW_GATE_STATE and allow_policy_alignment:
        alignment = align_review_gate(pr.repo, output_dir, apply=apply)
        receipt.evidence.append(f"policy_alignment={alignment.get('ok')}")
        if not alignment.get("ok"):
            receipt.blockers.extend([f"policy_alignment:{blocker}" for blocker in alignment.get("blockers", [])])
            write_json(output_dir / "policy" / f"{pr.repo.replace('/', '__')}-failed.json", alignment)
            return receipt
        refreshed = refresh_pr(pr.repo, pr.number)
        if reject_if_head_changed(receipt, refreshed, "head_changed_after_policy_alignment"):
            return receipt
    elif refreshed.merge_state == MERGE_REVIEW_GATE_STATE:
        receipt.classification = "BLOCKED_MERGE_STATE"
        receipt.blockers.append("review_required_policy_alignment_disabled")
        return receipt

    if refreshed.merge_state not in MERGE_READY_STATES:
        receipt.classification = "BLOCKED_MERGE_STATE"
        receipt.blockers.append(f"merge_state:{refreshed.merge_state}")
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
    validator_profile: str,
    autonomous_fix_loop: bool,
    max_fix_iterations: int,
    max_review_iterations: int,
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
            "would_update_branch": [],
            "would_dispatch_review": [],
            "would_start_fix_loop": [],
            "would_heal_required_checks": [],
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
        "would_update_branch": [],
        "would_dispatch_review": [],
        "would_start_fix_loop": [],
        "would_heal_required_checks": [],
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
                receipt = process_pr(
                    pr,
                    mode=mode,
                    output_dir=output_dir,
                    allow_policy_alignment=allow_policy_alignment,
                    workflow_url=workflow_url,
                    validator_profile=validator_profile,
                    sibling_prs=prs,
                    autonomous_fix_loop=autonomous_fix_loop,
                    max_fix_iterations=max_fix_iterations,
                    max_review_iterations=max_review_iterations,
                )
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
            elif key == "would_update_branch":
                repo_result["would_update_branch"].append(receipt.__dict__)
            elif key == "would_dispatch_review":
                repo_result["would_dispatch_review"].append(receipt.__dict__)
            elif key == "would_start_fix_loop":
                repo_result["would_start_fix_loop"].append(receipt.__dict__)
            elif key == "would_heal_required_checks":
                repo_result["would_heal_required_checks"].append(receipt.__dict__)
            else:
                repo_result["blocked"].append(receipt.__dict__)
            seen_without_action.add(pr.number)
            if max_prs_per_repo and processed >= max_prs_per_repo:
                repo_result["warnings"].append("max_prs_per_repo_reached")
                return repo_result
        if not took_action:
            return repo_result


def truncate_tracking_text(value: Any, limit: int = TRACKING_RECEIPT_TEXT_LIMIT) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def tracking_comment_receipt(report: dict[str, Any], *, minimal: bool = False) -> dict[str, Any]:
    """Return a compact receipt that fits GitHub issue comment limits.

    The full machine receipt is preserved as the workflow artifact. Tracking
    issue comments are an index/summary surface and must not be able to fail the
    closeout after merge/close actions have already completed.
    """
    receipt = {
        "ok": report.get("ok"),
        "final_verdict": report.get("final_verdict"),
        "generated_at": report.get("generated_at"),
        "mode": report.get("mode"),
        "validator_profile": report.get("validator_profile", DEFAULT_VALIDATOR_PROFILE),
        "workflow_url": report.get("workflow_url"),
        "repos_scanned": report.get("repos_scanned"),
        "open_prs_total": report.get("open_prs_total"),
        "open_dependabot_prs_total": report.get("open_dependabot_prs_total"),
        "open_non_dependabot_prs_total": report.get("open_non_dependabot_prs_total"),
        "open_issues_total": report.get("open_issues_total"),
        "dependabot_prs_before": report.get("dependabot_prs_before"),
        "merged_count": len(report.get("merged_prs", [])),
        "closed_count": len(report.get("closed_prs", [])),
        "blocked_count": len(report.get("blocked_prs", [])),
        "would_merge_count": len(report.get("would_merge_prs", [])),
        "would_close_count": len(report.get("would_close_prs", [])),
        "would_update_branch_count": len(report.get("would_update_branch_prs", [])),
        "would_dispatch_review_count": len(report.get("would_dispatch_review_prs", [])),
        "would_start_fix_loop_count": len(report.get("would_start_fix_loop_prs", [])),
        "would_heal_required_checks_count": len(report.get("would_heal_required_checks_prs", [])),
        "remaining_dependabot_prs": report.get("remaining_dependabot_prs"),
        "top_blocker_reasons": report.get("top_blocker_reasons", [])[:TRACKING_RECEIPT_ITEM_LIMIT],
        "telemetry_warnings_count": len(report.get("telemetry_warnings", [])),
        "remaining_blockers_count": len(report.get("remaining_blockers", [])),
        "artifact_note": "Full receipt is available in the workflow artifact ent-dependabot-weekly-<run_id>.",
    }
    if not minimal:
        receipt["remaining_blockers_sample"] = [
            truncate_tracking_text(blocker)
            for blocker in report.get("remaining_blockers", [])[:TRACKING_RECEIPT_ITEM_LIMIT]
        ]
    return receipt


def build_tracking_comment_body(report: dict[str, Any], summary_markdown: str) -> str:
    def render(receipt: dict[str, Any], summary: str, note: str) -> str:
        return "\n".join(
            [
                "## ENT Dependabot Weekly Autonomous Closeout",
                "",
                summary,
                "",
                note,
                "",
                "<details><summary>Compact machine receipt</summary>",
                "",
                "```json",
                json.dumps(receipt, indent=2, sort_keys=True),
                "```",
                "",
                "</details>",
                "",
                "Full per-PR and per-repo receipts are stored in the workflow artifact.",
            ]
        )

    body = render(
        tracking_comment_receipt(report),
        summary_markdown,
        "",
    )
    if len(body) <= TRACKING_COMMENT_MAX_CHARS:
        return body

    compact_summary = "\n".join(summary_markdown.splitlines()[:18])
    body = render(
        tracking_comment_receipt(report),
        compact_summary,
        "_Summary table omitted because the full comment exceeded the GitHub issue comment limit._",
    )
    if len(body) <= TRACKING_COMMENT_MAX_CHARS:
        return body

    minimal_summary = "\n".join(summary_markdown.splitlines()[:8])
    body = render(
        tracking_comment_receipt(report, minimal=True),
        minimal_summary,
        "_Summary and receipt details were compacted to keep this comment under the GitHub issue comment limit._",
    )
    if len(body) <= TRACKING_COMMENT_MAX_CHARS:
        return body
    raise GhError("tracking_comment_body_exceeds_limit_after_compaction")


def post_tracking_report(tracking_issue: str, report: dict[str, Any], summary_markdown: str) -> str | None:
    if not tracking_issue:
        return None
    match = re.match(r"https://github.com/([^/]+/[^/]+)/issues/(\d+)$", tracking_issue)
    if not match:
        raise GhError(f"invalid tracking issue URL: {tracking_issue}")
    repo, number = match.group(1), int(match.group(2))
    body = build_tracking_comment_body(report, summary_markdown)
    with gh_token_for_repo(repo):
        return post_comment_with_stdin(repo, number, body)


def markdown_summary(report: dict[str, Any]) -> str:
    lines = [
        f"- Mode: `{report['mode']}`",
        f"- Validator profile: `{report.get('validator_profile', DEFAULT_VALIDATOR_PROFILE)}`",
        f"- Status: `{report['final_verdict']}`",
        f"- Repos scanned: `{report['repos_scanned']}`",
        f"- Open PRs: `{report['open_prs_total']}` (`{report['open_dependabot_prs_total']}` Dependabot / `{report['open_non_dependabot_prs_total']}` non-Dependabot)",
        f"- Open issues: `{report['open_issues_total']}`",
        f"- Dependabot PRs before: `{report['dependabot_prs_before']}`",
        f"- Merged: `{len(report['merged_prs'])}`",
        f"- Closed: `{len(report['closed_prs'])}`",
        f"- Blocked: `{len(report['blocked_prs'])}`",
        f"- Would update branch: `{len(report.get('would_update_branch_prs', []))}`",
        f"- Would dispatch Merglbot review: `{len(report.get('would_dispatch_review_prs', []))}`",
        f"- Would start autonomous fix loop: `{len(report.get('would_start_fix_loop_prs', []))}`",
        f"- Would heal required checks: `{len(report.get('would_heal_required_checks_prs', []))}`",
        f"- Telemetry warnings: `{len(report.get('telemetry_warnings', []))}`",
        f"- Slack delivery: `{report.get('slack_delivery', {}).get('status', 'not_requested')}`",
        "",
        "| Repo | PRs | Dependabot | Non-Dependabot | Issues | Merged | Closed | Blocked | Would merge | Would close | Would update | Would review | Would fix | Would heal checks | Skipped |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["repo_table"]:
        lines.append(
            "| {repo} | {open_prs} | {before} | {non_dependabot} | {issues} | {merged} | {closed} | {blocked} | {would_merge} | {would_close} | {would_update} | {would_review} | {would_fix} | {would_heal_checks} | {skipped} |".format(
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
                would_update=row.get("would_update_branch", 0),
                would_review=row.get("would_dispatch_review", 0),
                would_fix=row.get("would_start_fix_loop", 0),
                would_heal_checks=row.get("would_heal_required_checks", 0),
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
    validator_profile: str,
    tracking_comment_url: str | None = None,
) -> dict[str, Any]:
    merged = [item for result in repo_results for item in result.get("merged", [])]
    closed = [item for result in repo_results for item in result.get("closed", [])]
    blocked = [item for result in repo_results for item in result.get("blocked", [])]
    would_merge = [item for result in repo_results for item in result.get("would_merge", [])]
    would_close = [item for result in repo_results for item in result.get("would_close", [])]
    would_update_branch = [item for result in repo_results for item in result.get("would_update_branch", [])]
    would_dispatch_review = [item for result in repo_results for item in result.get("would_dispatch_review", [])]
    would_start_fix_loop = [item for result in repo_results for item in result.get("would_start_fix_loop", [])]
    would_heal_required_checks = [item for result in repo_results for item in result.get("would_heal_required_checks", [])]
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
            "would_update_branch": len(result.get("would_update_branch", [])),
            "would_dispatch_review": len(result.get("would_dispatch_review", [])),
            "would_start_fix_loop": len(result.get("would_start_fix_loop", [])),
            "would_heal_required_checks": len(result.get("would_heal_required_checks", [])),
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
        "validator_profile": validator_profile,
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
        "would_update_branch_prs": would_update_branch,
        "would_dispatch_review_prs": would_dispatch_review,
        "would_start_fix_loop_prs": would_start_fix_loop,
        "would_heal_required_checks_prs": would_heal_required_checks,
        "skipped_by_allowlist_prs": skipped_by_allowlist,
        "remaining_dependabot_prs": len(blocked)
        + len(skipped_by_allowlist)
        + (len(would_merge) if mode == "dry-run" else 0)
        + (len(would_close) if mode == "dry-run" else 0)
        + (len(would_update_branch) if mode == "dry-run" else 0)
        + (len(would_dispatch_review) if mode == "dry-run" else 0)
        + (len(would_start_fix_loop) if mode == "dry-run" else 0)
        + (len(would_heal_required_checks) if mode == "dry-run" else 0),
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
        f"Mode: `{report.get('mode', 'unknown')}` | Validator: `{report.get('validator_profile', DEFAULT_VALIDATOR_PROFILE)}` | Repos: `{report.get('repos_scanned', 0)}`\n"
        f"Dependabot PRs: `{report.get('dependabot_prs_before', 0)}` before, "
        f"`{len(report.get('merged_prs', []))}` merged, "
        f"`{len(report.get('closed_prs', []))}` closed, "
        f"`{len(report.get('blocked_prs', []))}` blocked, "
        f"`{len(report.get('would_update_branch_prs', []))}` would-update, "
        f"`{len(report.get('would_dispatch_review_prs', []))}` would-review, "
        f"`{len(report.get('would_start_fix_loop_prs', []))}` would-fix, "
        f"`{len(report.get('would_heal_required_checks_prs', []))}` would-heal-checks, "
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
https://github.com/Merglevsky-cz/example
some/local/path
merglbot-core/agents-orchestrator
"""
    assert parse_repository_map(sample) == ["merglbot-core/github", "merglbot-public/docs"]
    assert parse_repository_map(sample, allow_plain_lines=True) == [
        "merglbot-core/agents-orchestrator",
        "merglbot-core/github",
        "merglbot-public/docs",
    ]
    assert classify_change_scope(["package-lock.json", "apps/web/yarn.lock"])[0] is True
    assert classify_change_scope(["apps/web/package.json"])[0] is False
    assert classify_change_scope(["docs/requirements/design.txt"])[0] is False
    assert classify_change_scope(["requirements-dev.txt"])[0] is True
    assert classify_change_scope([".github/workflows/ci.yml"])[0] is False
    assert classify_change_scope(["terraform/main.tf"])[0] is False
    assert "DIRTY" not in MERGE_READY_STATES
    assert MERGE_REVIEW_GATE_STATE not in MERGE_READY_STATES
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
                "would_update_branch": [],
                "would_dispatch_review": [],
                "warnings": [],
            }
        ],
        pr_allowlist=set(),
        approval_note="",
        approval_issue_url="",
        workflow_url="https://github.com/o/r/actions/runs/1",
        validator_profile=DEFAULT_VALIDATOR_PROFILE,
    )
    assert report["repos_scanned"] == 1
    assert report["dependabot_prs_before"] == 1
    assert report["open_non_dependabot_prs_total"] == 1
    assert build_slack_payload(report, "ok")["text"].count("Dependabot") >= 2
    large_report = dict(report)
    large_report["blocked_prs"] = [
        {"repo": "merglbot-core/github", "pr_number": number, "blockers": ["sample"], "evidence": ["x" * 200]}
        for number in range(400)
    ]
    large_report["remaining_blockers"] = ["b" * 1000 for _ in range(400)]
    compact_body = build_tracking_comment_body(large_report, markdown_summary(large_report))
    assert len(compact_body) <= TRACKING_COMMENT_MAX_CHARS
    assert "Full per-PR and per-repo receipts are stored in the workflow artifact." in compact_body
    compact_receipt_text = json.dumps(tracking_comment_receipt(large_report))
    assert "blocked_count" in compact_receipt_text
    assert "remaining_blockers_count" in compact_receipt_text
    assert len(tracking_comment_receipt(large_report)["remaining_blockers_sample"]) == TRACKING_RECEIPT_ITEM_LIMIT
    assert parse_pr_allowlist("merglbot-core/github#1 https://github.com/merglbot-public/docs/pull/2") == {
        ("merglbot-core/github", 1),
        ("merglbot-public/docs", 2),
    }
    assert merglbot_dispatch_inputs(7, "a" * 40) == {
        "pr_number": "7",
        "review_mode": "light",
        "include_retro": "false",
        "diff_scope": "auto",
        "expected_head_sha": "a" * 40,
    }
    assert is_current_head_merglbot_terminal_blocker(
        {
            "ok": False,
            "review_head_sha": "a" * 40,
            "current_head_match": True,
            "verdict": "changes_required",
            "status": "blocked",
            "blockers": ["review_not_approved_for_closeout"],
        },
        "a" * 40,
    ) is True
    assert is_current_head_merglbot_terminal_blocker(
        {
            "ok": False,
            "review_head_sha": "b" * 40,
            "current_head_match": False,
            "verdict": "changes_required",
            "status": "blocked",
            "blockers": ["review_not_approved_for_closeout"],
        },
        "a" * 40,
    ) is False
    assert is_current_head_merglbot_terminal_blocker(
        {
            "ok": False,
            "review_head_sha": "a" * 40,
            "current_head_match": True,
            "verdict": "pending",
            "status": "pending",
            "blockers": ["review_pending"],
        },
        "a" * 40,
    ) is False
    assert is_current_head_merglbot_terminal_blocker(
        {
            "ok": False,
            "review_head_sha": "a" * 40,
            "current_head_match": True,
            "verdict": "",
            "status": "failed",
            "blockers": [],
        },
        "a" * 40,
    ) is True
    assert is_current_head_merglbot_terminal_blocker(
        {
            "ok": True,
            "review_head_sha": "a" * 40,
            "current_head_match": True,
            "verdict": "approved_for_closeout",
            "status": "success",
            "blockers": [],
        },
        "a" * 40,
    ) is False
    assert request_update_branch("merglbot-core/github", 1, "b" * 40, apply=False)["blockers"] == ["update_branch_required"]
    assert bad_credentials_seen("GraphQL: Bad credentials") is True
    APP_TOKEN_CACHE["example"] = ("token", datetime.now(timezone.utc) + timedelta(hours=1))
    invalidate_app_token_for_owner("example")
    assert "example" not in APP_TOKEN_CACHE
    assert parse_dependabot_update_title("Bump lodash from 4.17.20 to 4.17.21 in /apps/web") == DependabotUpdate(
        dependency="lodash",
        from_version="4.17.20",
        to_version="4.17.21",
        path_hint="apps/web",
    )
    assert compare_versions("^4.17.21", "4.17.20") == 1
    assert compare_versions("v1.2.0", "1.2") == 0
    assert is_exact_stable_version_for_autoclose("4.17.21") is True
    assert is_exact_stable_version_for_autoclose("^4.17.21") is False
    assert is_exact_stable_version_for_autoclose("4.17.21-beta.1") is False
    assert package_json_dependency_version({"dependencies": {"Lodash": "^4.17.21"}}, "lodash") == "^4.17.21"
    older = PullRequest(
        "o/r",
        1,
        "Bump lodash from 4.17.19 to 4.17.20 in /apps/web",
        "https://github.com/o/r/pull/1",
        "dependabot[bot]",
        "a" * 40,
        "b" * 40,
        "main",
        "dependabot/npm_and_yarn/apps/web/lodash-4.17.20",
        False,
        "CLEAN",
        utc_now(),
    )
    newer = PullRequest(
        "o/r",
        2,
        "Bump lodash from 4.17.20 to 4.17.21 in /apps/web",
        "https://github.com/o/r/pull/2",
        "dependabot[bot]",
        "c" * 40,
        "b" * 40,
        "main",
        "dependabot/npm_and_yarn/apps/web/lodash-4.17.21",
        False,
        "CLEAN",
        utc_now(),
    )
    close_candidate = classify_close_candidate(older, ["apps/web/package-lock.json"], [older, newer])
    assert close_candidate is not None
    assert close_candidate[0] == "AUTO_CLOSE_OLDER_SIBLING"
    no_path_older = PullRequest(
        "o/r",
        3,
        "Bump lodash from 4.17.19 to 4.17.20",
        "https://github.com/o/r/pull/3",
        "dependabot[bot]",
        "d" * 40,
        "b" * 40,
        "main",
        "dependabot/npm_and_yarn/lodash-4.17.20",
        False,
        "CLEAN",
        utc_now(),
    )
    assert classify_close_candidate(no_path_older, ["package-lock.json"], [no_path_older, newer]) is None
    assert classify_required_check_blocker({"name": "Analyze (actions)", "bucket": "pending"})["category"] == "stale_or_pending_analysis_context"
    assert classify_required_check_blocker({"name": "ci", "bucket": "fail"})["category"] == "check_failed_real"
    assert classify_required_check_blocker({"name": "codeql / Analyze (javascript)", "bucket": "skipping"})["category"] == "skipped_analysis_context"
    healing = required_check_healing_actions([
        classify_required_check_blocker({"name": "ci", "bucket": "fail"}),
        classify_required_check_blocker({"name": "Analyze (actions)", "bucket": "pending"}),
    ])
    assert healing[0]["action"] == "start_minimal_pr_branch_fix_loop"
    assert healing[1]["action"] == "diagnose_or_rerun_required_check"
    ledger = merglbot_findings_ledger(
        {
            "ok": False,
            "review_head_sha": "a" * 40,
            "current_head_match": True,
            "verdict": "changes_required",
            "status": "blocked",
            "comment_url": "https://github.com/o/r/pull/1#issuecomment-1",
            "blockers": ["review_not_approved_for_closeout"],
        },
        "a" * 40,
    )
    assert ledger[0]["next_action"] == "minimal_same_branch_fix_then_rerun_merglbot"
    fix_receipt = ItemReceipt("o/r", 1, "u", "blocked", "BLOCKED", head_sha="a" * 40)
    mark_fix_loop_candidate(
        fix_receipt,
        classification="WOULD_START_AUTONOMOUS_FIX_LOOP",
        evidence="test",
        max_fix_iterations=5,
        max_review_iterations=5,
    )
    assert fix_receipt.action == "would_start_fix_loop"
    assert fix_receipt.would_start_fix_loop is True
    assert fix_receipt.terminal_close_loop_verdict == "PENDING_AUTONOMOUS_FIX_LOOP"
    assert validate_change_scope(
        "merglbot-core/github",
        1,
        ["package-lock.json"],
        base_sha="a" * 40,
        head_sha="b" * 40,
        validator_profile=DEFAULT_VALIDATOR_PROFILE,
    )[0] is True
    assert validate_npm_manifest_payloads(
        {"dependencies": {"a": "^1.0.0"}, "scripts": {"test": "x"}},
        {"dependencies": {"a": "^1.1.0"}, "scripts": {"test": "x"}},
        "package.json",
        ["package.json", "package-lock.json"],
    )[0] is True
    assert validate_npm_manifest_payloads(
        {"dependencies": {"a": "^1.0.0"}, "scripts": {"test": "x"}},
        {"dependencies": {"a": "^1.1.0"}, "scripts": {"test": "y"}},
        "package.json",
        ["package.json", "package-lock.json"],
    )[0] is False
    assert validate_npm_manifest_payloads(
        {"dependencies": {"a": "^1.0.0"}},
        {"dependencies": {"a": "^1.1.0", "b": "^1.0.0"}},
        "package.json",
        ["package.json", "package-lock.json"],
    )[0] is False
    assert validate_npm_manifest_payloads(
        {"dependencies": {"a": "^1.0.0"}},
        {"dependencies": {"a": "^1.1.0"}},
        "package.json",
        ["package.json"],
    )[0] is False
    assert parse_uses_ref("      uses: actions/checkout@v6") == ("actions/checkout", "v6")
    assert parse_uses_ref("      - uses: actions/setup-node@v6") == ("actions/setup-node", "v6")
    assert parse_uses_ref("      run: echo nope") is None
    assert validate_workflow_ref_only_text(
        "jobs:\n  x:\n    uses: owner/repo/.github/workflows/ci.yml@old\n",
        "jobs:\n  x:\n    uses: owner/repo/.github/workflows/ci.yml@new\n",
        ".github/workflows/ci.yml",
    )[0] is True
    assert validate_workflow_ref_only_text(
        "jobs:\n  x:\n    uses: owner/repo/.github/workflows/ci.yml@old\n",
        "jobs:\n  x:\n    run: echo nope\n",
        ".github/workflows/ci.yml",
    )[0] is False
    previous_app_env = {
        key: os.environ.get(key)
        for key in ["ENT_DEPENDABOT_APP_ID", "ENT_DEPENDABOT_APP_PRIVATE_KEY"]
    }
    try:
        os.environ.pop("ENT_DEPENDABOT_APP_ID", None)
        os.environ.pop("ENT_DEPENDABOT_APP_PRIVATE_KEY", None)
        assert github_app_auth_configured() is False
        os.environ["ENT_DEPENDABOT_APP_ID"] = "123"
        try:
            github_app_auth_configured()
            raise AssertionError("partial GitHub App auth env should fail closed")
        except GhError:
            pass
        os.environ["ENT_DEPENDABOT_APP_PRIVATE_KEY"] = "-----BEGIN KEY-----\\nabc\\n-----END KEY-----"
        assert github_app_auth_configured() is True
        assert "\nabc\n" in github_app_private_key()
    finally:
        for key, value in previous_app_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    previous_env = {
        key: os.environ.get(key)
        for key in ["GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"]
    }
    os.environ.update({
        "GITHUB_SHA": "abc",
        "GITHUB_RUN_ID": "12345",
        "GITHUB_ACTOR": "milhul6",
        "GITHUB_TRIGGERING_ACTOR": "milhul6",
    })
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
        PullRequest("o/r", 1, "x", "u", "dependabot[bot]", "a" * 40, "b" * 40, "main", "dependabot/x", False, "CLEAN", utc_now()),
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
    parser.add_argument("--validator-profile", choices=sorted(VALIDATOR_PROFILES), default=DEFAULT_VALIDATOR_PROFILE)
    parser.add_argument("--autonomous-fix-loop", action="store_true")
    parser.add_argument("--orchestrator-fix-handoff", action="store_true")
    parser.add_argument("--max-fix-iterations", type=int, default=5)
    parser.add_argument("--max-review-iterations", type=int, default=5)
    parser.add_argument("--fix-profile", choices=["dependabot_safe_v1"], default="dependabot_safe_v1")
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
        if args.repo_scope == "single_repo":
            if not args.single_repo:
                raise GhError("--single-repo is required with --repo-scope single_repo")
            all_repos = load_single_repo_scope(args.scope_file)
            repos = [args.single_repo]
            missing = set(repos) - set(all_repos)
            if missing:
                raise GhError(f"single repo is outside 42-repo scope: {', '.join(sorted(missing))}")
        elif args.repo_scope == "cohort":
            if not args.cohort_file:
                raise GhError("--cohort-file is required with --repo-scope cohort")
            all_repos = load_repo_scope(args.scope_file)
            repos = parse_repository_map(args.cohort_file.read_text(encoding="utf-8"), allow_plain_lines=True)
            missing = set(repos) - set(all_repos)
            if missing:
                raise GhError(f"cohort contains repos outside 42-repo scope: {', '.join(sorted(missing))}")
            owners = {repo.split("/", 1)[0] for repo in repos}
            if len(owners) > 1 and not github_app_auth_configured():
                raise GhError("GitHub App auth is required for multi-owner cohort runs")
        else:
            if not github_app_auth_configured():
                raise GhError("GitHub App auth is required for ENT all-repo runs")
            all_repos = load_repo_scope(args.scope_file)
            repos = all_repos

        repo_results = []
        for repo in repos:
            with gh_token_for_repo(repo):
                repo_results.append(
                    process_repo(
                        repo,
                        mode=args.mode,
                        output_dir=args.output_dir,
                        max_prs_per_repo=args.max_prs_per_repo,
                        allow_policy_alignment=args.allow_policy_alignment,
                        workflow_url=args.workflow_url,
                        pr_allowlist=pr_allowlist,
                        validator_profile=args.validator_profile,
                        autonomous_fix_loop=args.autonomous_fix_loop,
                        max_fix_iterations=args.max_fix_iterations,
                        max_review_iterations=args.max_review_iterations,
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
            validator_profile=args.validator_profile,
        )
        report["autonomous_fix_loop"] = {
            "enabled": bool(args.autonomous_fix_loop),
            "orchestrator_fix_handoff": bool(args.orchestrator_fix_handoff),
            "fix_profile": args.fix_profile,
            "max_fix_iterations": args.max_fix_iterations,
            "max_review_iterations": args.max_review_iterations,
            "contract": "Autonomous PR Close-Loop v1 + MERGLBOT_PR_REVIEW_AUTONOMOUS_AUTOMERGE_V1",
        }
        if args.slack_notify:
            report["slack_delivery"] = post_slack_report(os.environ.get("SLACK_DEPENDABOT_WEBHOOK_URL", ""), report)
            if not report["slack_delivery"].get("ok"):
                report["telemetry_degraded"] = True
        write_json(args.output_dir / "ent_dependabot_weekly_receipt.json", report)
        write_json(args.output_dir / "ent_dependabot_repo_results.json", repo_results)
        summary = markdown_summary(report)
        (args.output_dir / "summary.md").write_text(summary + "\n", encoding="utf-8")
        if args.comment_report and args.tracking_issue:
            try:
                report["tracking_comment_url"] = post_tracking_report(args.tracking_issue, report, summary)
            except GhError as exc:
                report["telemetry_degraded"] = True
                report.setdefault("telemetry_warnings", []).append(f"tracking_comment_failed:{exc}")
            write_json(args.output_dir / "ent_dependabot_weekly_receipt.json", report)
            (args.output_dir / "summary.md").write_text(markdown_summary(report) + "\n", encoding="utf-8")
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1
    except Exception as exc:
        failure = {
            "ok": False,
            "final_verdict": "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_BLOCKED",
            "mode": args.mode,
            "validator_profile": args.validator_profile,
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
