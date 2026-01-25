#!/usr/bin/env bash
# Purpose: Deploy PR Assistant v3 workflow copy to all Merglbot repos (issue_comment trigger cannot be workflow_call).
# Usage:
#   ./scripts/pr-assistant/deploy-v3.sh --dry-run
#   ./scripts/pr-assistant/deploy-v3.sh

set -euo pipefail

DRY_RUN="false"
FORCE="false"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="true" ;;
    --force) FORCE="true" ;;
  esac
done

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SOURCE_WORKFLOW="${WORKSPACE_ROOT}/merglbot-core/github/.github/workflows/merglbot-pr-assistant-v3-on-demand.yml"
TARGET_REPOS_FILE="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/target-repos.txt"

if [ ! -f "$SOURCE_WORKFLOW" ]; then
  echo "ERROR: Source workflow not found: $SOURCE_WORKFLOW" >&2
  exit 1
fi

if [ ! -f "$TARGET_REPOS_FILE" ]; then
  echo "ERROR: Target repos file not found: $TARGET_REPOS_FILE" >&2
  exit 1
fi

# Platform scope (exclude Merglevsky-cz entirely).
mapfile -t TARGET_REPOS < <(
  sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$TARGET_REPOS_FILE" | sed '/^$/d'
)

is_git_clean() {
  local repo_dir="$1"
  git -C "$repo_dir" diff --quiet && git -C "$repo_dir" diff --cached --quiet
}

echo "Workspace: $WORKSPACE_ROOT"
echo "Source:    $SOURCE_WORKFLOW"
echo "Mode:      $([ "$DRY_RUN" == "true" ] && echo "DRY RUN" || echo "APPLY")"
echo "Force:     $FORCE"
echo ""

for repo in "${TARGET_REPOS[@]}"; do
  repo_dir="${WORKSPACE_ROOT}/${repo}"
  dest="${repo_dir}/.github/workflows/merglbot-pr-v3-on-demand.yml"

  if [ ! -d "$repo_dir" ]; then
    echo "⏭️  SKIP (missing dir): $repo"
    continue
  fi

  if [ -d "${repo_dir}/.git" ]; then
    if ! is_git_clean "$repo_dir"; then
      if [ "$FORCE" == "true" ]; then
        echo "⚠️  FORCE (dirty git tree): $repo"
      else
        echo "⏭️  SKIP (dirty git tree): $repo"
        continue
      fi
    fi
  fi

  if [ "$DRY_RUN" == "true" ]; then
    echo "DRY:  $repo -> $dest"
    continue
  fi

  mkdir -p "$(dirname "$dest")"
  cp "$SOURCE_WORKFLOW" "$dest"
  echo "✅    $repo"
done
