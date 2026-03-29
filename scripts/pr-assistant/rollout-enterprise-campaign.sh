#!/usr/bin/env bash
# Purpose: Materialize enterprise rollout branches / PRs for PR Assistant v3 across managed repos.
# Usage:
#   ./scripts/pr-assistant/rollout-enterprise-campaign.sh --wave 1 --dry-run
#   ./scripts/pr-assistant/rollout-enterprise-campaign.sh --wave 2 --push --open-prs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEPLOY_SCRIPT="${SCRIPT_DIR}/deploy-v3.sh"
TARGET_REPOS_FILE="${SCRIPT_DIR}/target-repos.txt"
DEFAULT_CACHE_ROOT="${REPO_ROOT}/tmp/pr-assistant-rollout-campaign"

WAVE=""
CACHE_ROOT="${DEFAULT_CACHE_ROOT}"
DATE_TAG="$(date -u '+%Y%m%d')"
DRY_RUN="false"
PUSH="false"
OPEN_PRS="false"
ONLY_REPOS_RAW=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --wave)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --wave requires 1, 2, 3, or 4" >&2
        exit 2
      fi
      WAVE="$2"
      shift 2
      ;;
    --wave=*)
      WAVE="${1#--wave=}"
      shift
      ;;
    --cache-root)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --cache-root requires a directory path" >&2
        exit 2
      fi
      CACHE_ROOT="$2"
      shift 2
      ;;
    --cache-root=*)
      CACHE_ROOT="${1#--cache-root=}"
      shift
      ;;
    --date-tag)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --date-tag requires YYYYMMDD-like text" >&2
        exit 2
      fi
      DATE_TAG="$2"
      shift 2
      ;;
    --date-tag=*)
      DATE_TAG="${1#--date-tag=}"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --push)
      PUSH="true"
      shift
      ;;
    --open-prs)
      OPEN_PRS="true"
      PUSH="true"
      shift
      ;;
    --only)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --only requires a comma-separated repo list" >&2
        exit 2
      fi
      ONLY_REPOS_RAW="$2"
      shift 2
      ;;
    --only=*)
      ONLY_REPOS_RAW="${1#--only=}"
      shift
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! "$WAVE" =~ ^[1-4]$ ]]; then
  echo "ERROR: --wave must be one of 1, 2, 3, 4" >&2
  exit 2
fi

wave_matches_repo() {
  local repo="$1"
  case "$WAVE" in
    1)
      [[ "$repo" == merglbot-core/* || "$repo" == merglbot-public/* ]]
      ;;
    2)
      [[ "$repo" == merglbot-cerano/* || "$repo" == merglbot-denatura/* || "$repo" == merglbot-proteinaco/* || "$repo" == merglbot-ruzovyslon/* ]]
      ;;
    3)
      [[ "$repo" == merglbot-extractors/* || "$repo" == merglbot-milan-private/* || "$repo" == merglbot-autodoplnky/* || "$repo" == merglbot-hodinarstvibechyne/* || "$repo" == merglbot-kiteboarding/* ]]
      ;;
    4)
      [[ "$repo" == lrtch/* || "$repo" == merglbot-shared/* ]]
      ;;
  esac
}

TARGET_REPOS=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$line" ] && continue
  if wave_matches_repo "$line"; then
    TARGET_REPOS+=("$line")
  fi
done < "$TARGET_REPOS_FILE"

if [ -n "$ONLY_REPOS_RAW" ]; then
  FILTERED=()
  IFS=',' read -r -a only_parts <<< "$ONLY_REPOS_RAW"
  for raw in "${only_parts[@]}"; do
    repo="$(printf '%s' "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$repo" ] && continue
    FILTERED+=("$repo")
  done
  TARGET_REPOS=("${FILTERED[@]}")
fi

if [ "${#TARGET_REPOS[@]}" -eq 0 ]; then
  echo "ERROR: no target repos selected for wave ${WAVE}" >&2
  exit 1
fi

WORKSPACE_ROOT="${CACHE_ROOT}/workspace"
mkdir -p "${WORKSPACE_ROOT}/merglbot-core"
ln -sfn "${REPO_ROOT}" "${WORKSPACE_ROOT}/merglbot-core/github"

BRANCH="codex/pr-assistant-v3-rollout-wave-${WAVE}-${DATE_TAG}"
PR_TITLE="chore(ci): roll out PR Assistant v3 reusable snapshot"
PR_BODY="Automated enterprise rollout wave ${WAVE} for the current PR Assistant v3 reusable workflow snapshot."

repo_manifest_paths() {
  local repo="$1"
  python3 - "$repo" "${REPO_ROOT}/scripts/pr-assistant/repo-policy-manifest.json" <<'PY'
import json
import pathlib
import sys

repo = sys.argv[1]
manifest_path = pathlib.Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
for entry in manifest["repos"]:
    if entry["repo"] != repo:
        continue
    print(entry["expected_workflow"])
    expected_step1 = entry.get("expected_step1")
    if expected_step1:
        print(expected_step1)
    sys.exit(0)
raise SystemExit(f"missing repo-policy manifest entry for {repo}")
PY
}

ensure_repo_checkout() {
  local repo="$1"
  local repo_dir="${WORKSPACE_ROOT}/${repo}"
  local repo_parent
  repo_parent="$(dirname "$repo_dir")"
  mkdir -p "$repo_parent"

  if [ ! -d "$repo_dir/.git" ]; then
    echo "Cloning ${repo}"
    gh repo clone "$repo" "$repo_dir" -- --no-tags --quiet
  fi

  local default_branch
  default_branch="$(gh repo view "$repo" --json defaultBranchRef --jq '.defaultBranchRef.name')"

  git -C "$repo_dir" fetch origin --prune --quiet
  if git -C "$repo_dir" ls-remote --exit-code origin "refs/heads/${BRANCH}" > /dev/null 2>&1; then
    git -C "$repo_dir" checkout -B "$BRANCH" "origin/${BRANCH}" >/dev/null 2>&1
  else
    git -C "$repo_dir" checkout -B "$BRANCH" "origin/${default_branch}" >/dev/null 2>&1
  fi
}

commit_push_and_pr() {
  local repo="$1"
  local repo_dir="${WORKSPACE_ROOT}/${repo}"
  local repo_paths=()
  local relative_path

  while IFS= read -r relative_path; do
    [ -z "$relative_path" ] && continue
    repo_paths+=("$relative_path")
  done < <(repo_manifest_paths "$repo")

  if [ "${#repo_paths[@]}" -eq 0 ]; then
    echo "ERROR: no repo-policy manifest paths resolved for ${repo}" >&2
    exit 1
  fi

  git -C "$repo_dir" add -- "${repo_paths[@]}"
  if git -C "$repo_dir" diff --cached --quiet; then
    echo "No content changes for ${repo}"
    return 0
  fi

  git -C "$repo_dir" config user.name "merglbot-bot"
  git -C "$repo_dir" config user.email "bot@merglbot.ai"
  git -C "$repo_dir" commit -m "chore(ci): roll out PR Assistant v3 reusable snapshot" >/dev/null

  if [ "$PUSH" != "true" ]; then
    echo "Committed locally only for ${repo}"
    return 0
  fi

  git -C "$repo_dir" push --set-upstream origin "$BRANCH" >/dev/null

  if [ "$OPEN_PRS" != "true" ]; then
    echo "Pushed branch without PR for ${repo}"
    return 0
  fi

  local pr_number
  pr_number="$(gh pr list --repo "$repo" --head "$BRANCH" --json number --jq '.[0].number // empty' 2>/dev/null || true)"
  if [ -n "$pr_number" ]; then
    echo "PR already exists for ${repo}: #${pr_number}"
    return 0
  fi

  gh pr create \
    --repo "$repo" \
    --title "$PR_TITLE" \
    --body "$PR_BODY" \
    --base "$(gh repo view "$repo" --json defaultBranchRef --jq '.defaultBranchRef.name')" \
    --head "$BRANCH" >/dev/null
  echo "Created PR for ${repo}"
}

echo "Wave:           ${WAVE}"
echo "Date tag:       ${DATE_TAG}"
echo "Workspace root: ${WORKSPACE_ROOT}"
echo "Branch:         ${BRANCH}"
echo "Dry run:        ${DRY_RUN}"
echo "Push:           ${PUSH}"
echo "Open PRs:       ${OPEN_PRS}"
echo "Targets:        ${#TARGET_REPOS[@]}"
echo ""

for repo in "${TARGET_REPOS[@]}"; do
  echo "=== ${repo} ==="
  ensure_repo_checkout "$repo"

  if [ "$DRY_RUN" = "true" ]; then
    "${DEPLOY_SCRIPT}" --workspace-root "$WORKSPACE_ROOT" --only "$repo" --dry-run
    continue
  fi

  "${DEPLOY_SCRIPT}" --workspace-root "$WORKSPACE_ROOT" --only "$repo"
  bash -n "${WORKSPACE_ROOT}/${repo}/scripts/pr-assistant/pr-assistant-step1-parallel-api-calls.sh"
  commit_push_and_pr "$repo"
done

if [ "$WAVE" = "1" ]; then
  echo ""
  echo "Wave 1 note: merglbot-core/github stays in the manifest as deploy_mode=canonical_self."
fi
