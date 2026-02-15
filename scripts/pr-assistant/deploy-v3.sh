#!/usr/bin/env bash
# Purpose: Deploy PR Assistant v3 workflow copy to all Merglbot repos (issue_comment trigger cannot be workflow_call).
# Usage:
#   ./scripts/pr-assistant/deploy-v3.sh --dry-run
#   ./scripts/pr-assistant/deploy-v3.sh --only merglbot-core,merglbot-public --dry-run
#   ./scripts/pr-assistant/deploy-v3.sh

set -euo pipefail

DRY_RUN="false"
FORCE="false"
ONLY_REPOS_RAW=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --force)
      FORCE="true"
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

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
SOURCE_WORKFLOW="${WORKSPACE_ROOT}/merglbot-core/github/.github/workflows/merglbot-pr-assistant-v3-on-demand.yml"
SOURCE_STEP1="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/pr-assistant-step1-parallel-api-calls.sh"
TARGET_REPOS_FILE="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/target-repos.txt"

if [ ! -f "$SOURCE_WORKFLOW" ]; then
  echo "ERROR: Source workflow not found: $SOURCE_WORKFLOW" >&2
  exit 1
fi

if [ ! -f "$SOURCE_STEP1" ]; then
  echo "ERROR: Source Step1 script not found: $SOURCE_STEP1" >&2
  exit 1
fi

if [ ! -f "$TARGET_REPOS_FILE" ]; then
  echo "ERROR: Target repos file not found: $TARGET_REPOS_FILE" >&2
  exit 1
fi

# Platform scope (exclude Merglevsky-cz entirely).
TARGET_REPOS=()
while IFS= read -r line; do
  line="${line%%#*}"
  line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [ -z "$line" ] && continue
  TARGET_REPOS+=("$line")
done < "$TARGET_REPOS_FILE"

if [ -n "$ONLY_REPOS_RAW" ]; then
  ONLY_REPOS=()
  IFS=',' read -r -a only_parts <<< "$ONLY_REPOS_RAW"
  for raw in "${only_parts[@]}"; do
    repo="$(printf '%s' "$raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -z "$repo" ] && continue
    in_target="false"
    for t in "${TARGET_REPOS[@]}"; do
      if [ "$t" = "$repo" ]; then
        in_target="true"
        break
      fi
    done
    if [ "$in_target" != "true" ]; then
      echo "ERROR: --only repo not in target list: $repo" >&2
      exit 2
    fi
    ONLY_REPOS+=("$repo")
  done

  if [ "${#ONLY_REPOS[@]}" -eq 0 ]; then
    echo "ERROR: --only provided but no valid repos parsed" >&2
    exit 2
  fi

  TARGET_REPOS=("${ONLY_REPOS[@]}")
fi

is_git_clean() {
  local repo_dir="$1"
  git -C "$repo_dir" diff --quiet && git -C "$repo_dir" diff --cached --quiet
}

echo "Workspace: $WORKSPACE_ROOT"
echo "Source:    $SOURCE_WORKFLOW"
echo "Step1:     $SOURCE_STEP1"
echo "Mode:      $([ "$DRY_RUN" == "true" ] && echo "DRY RUN" || echo "APPLY")"
echo "Force:     $FORCE"
if [ -n "$ONLY_REPOS_RAW" ]; then
  echo "Only:      $ONLY_REPOS_RAW"
fi
echo ""

for repo in "${TARGET_REPOS[@]}"; do
  repo_dir="${WORKSPACE_ROOT}/${repo}"
  dest_workflow="${repo_dir}/.github/workflows/merglbot-pr-v3-on-demand.yml"
  dest_step1="${repo_dir}/scripts/pr-assistant/pr-assistant-step1-parallel-api-calls.sh"

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
    echo "DRY:  $repo -> $dest_workflow"
    echo "DRY:  $repo -> $dest_step1"
    continue
  fi

  mkdir -p "$(dirname "$dest_workflow")"
  mkdir -p "$(dirname "$dest_step1")"
  cp "$SOURCE_WORKFLOW" "$dest_workflow"
  cp "$SOURCE_STEP1" "$dest_step1"
  chmod +x "$dest_step1" || true
  echo "✅    $repo"
done
