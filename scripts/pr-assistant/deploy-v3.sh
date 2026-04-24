#!/usr/bin/env bash
# Purpose: Deploy PR Assistant v3 workflow copy to all Merglbot repos (issue_comment trigger cannot be workflow_call).
# Usage:
#   ./scripts/pr-assistant/deploy-v3.sh --dry-run
#   ./scripts/pr-assistant/deploy-v3.sh --only merglbot-core/platform,merglbot-public/docs --dry-run
#   ./scripts/pr-assistant/deploy-v3.sh --workspace-root /tmp/pr-assistant-rollout-workspace --only merglbot-core/platform
#   ./scripts/pr-assistant/deploy-v3.sh
#
# Notes:
# - --only values must exactly match entries in target-repos.txt (org/repo format).

set -euo pipefail

DRY_RUN="false"
FORCE="false"
ONLY_REPOS_RAW=""
WORKSPACE_ROOT_OVERRIDE=""
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
    --workspace-root)
      if [ "$#" -lt 2 ]; then
        echo "ERROR: --workspace-root requires an absolute directory path" >&2
        exit 2
      fi
      WORKSPACE_ROOT_OVERRIDE="$2"
      shift 2
      ;;
    --workspace-root=*)
      WORKSPACE_ROOT_OVERRIDE="${1#--workspace-root=}"
      shift
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -n "$WORKSPACE_ROOT_OVERRIDE" ]; then
  WORKSPACE_ROOT="$WORKSPACE_ROOT_OVERRIDE"
else
  WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
fi
SOURCE_WORKFLOW="${WORKSPACE_ROOT}/merglbot-core/github/.github/workflows/merglbot-pr-assistant-v3-on-demand.yml"
SOURCE_STEP1="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/pr-assistant-step1-parallel-api-calls.sh"
SOURCE_VERIFIER="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/verify-review-receipt.py"
SOURCE_ZAVER_EXTRACTOR="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/extract-zaver-field.sh"
MANIFEST_TOOL="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/repo-policy-manifest.py"
MANIFEST_FILE="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/repo-policy-manifest.json"
TARGET_REPOS_FILE="${WORKSPACE_ROOT}/merglbot-core/github/scripts/pr-assistant/target-repos.txt"

if [ ! -f "$SOURCE_WORKFLOW" ]; then
  echo "ERROR: Source workflow not found: $SOURCE_WORKFLOW" >&2
  exit 1
fi

if [ ! -f "$SOURCE_STEP1" ]; then
  echo "ERROR: Source Step1 script not found: $SOURCE_STEP1" >&2
  exit 1
fi

if [ ! -f "$SOURCE_VERIFIER" ]; then
  echo "ERROR: Source review receipt verifier not found: $SOURCE_VERIFIER" >&2
  exit 1
fi

if [ ! -f "$SOURCE_ZAVER_EXTRACTOR" ]; then
  echo "ERROR: Source Zaver field extractor not found: $SOURCE_ZAVER_EXTRACTOR" >&2
  exit 1
fi

if [ ! -f "$MANIFEST_TOOL" ]; then
  echo "ERROR: Repo-policy manifest tool not found: $MANIFEST_TOOL" >&2
  exit 1
fi

if [ ! -f "$MANIFEST_FILE" ]; then
  echo "ERROR: Repo-policy manifest not found: $MANIFEST_FILE" >&2
  exit 1
fi

if [ ! -f "$TARGET_REPOS_FILE" ]; then
  echo "ERROR: Target repos file not found: $TARGET_REPOS_FILE" >&2
  exit 1
fi

python3 "$MANIFEST_TOOL" \
  --manifest "$MANIFEST_FILE" \
  verify-manifest \
  --target-list "$TARGET_REPOS_FILE"

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
      echo "ERROR: --only repo not in target list: $repo (expected org/repo, e.g. merglbot-core/platform)" >&2
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
echo "Verifier:  $SOURCE_VERIFIER"
echo "Extractor: $SOURCE_ZAVER_EXTRACTOR"
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
  dest_verifier="${repo_dir}/scripts/pr-assistant/verify-review-receipt.py"
  dest_zaver_extractor="${repo_dir}/scripts/pr-assistant/extract-zaver-field.sh"

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
    echo "DRY:  $repo -> $dest_verifier"
    echo "DRY:  $repo -> $dest_zaver_extractor"
    continue
  fi

  mkdir -p "$(dirname "$dest_workflow")"
  mkdir -p "$(dirname "$dest_step1")"
  mkdir -p "$(dirname "$dest_verifier")"
  mkdir -p "$(dirname "$dest_zaver_extractor")"
  cp "$SOURCE_WORKFLOW" "$dest_workflow"
  cp "$SOURCE_STEP1" "$dest_step1"
  cp "$SOURCE_VERIFIER" "$dest_verifier"
  cp "$SOURCE_ZAVER_EXTRACTOR" "$dest_zaver_extractor"
  if ! chmod +x "$dest_step1"; then
    echo "ERROR: Failed to chmod +x: $dest_step1" >&2
    exit 1
  fi
  if ! chmod +x "$dest_verifier"; then
    echo "ERROR: Failed to chmod +x: $dest_verifier" >&2
    exit 1
  fi
  if ! chmod +x "$dest_zaver_extractor"; then
    echo "ERROR: Failed to chmod +x: $dest_zaver_extractor" >&2
    exit 1
  fi
  echo "✅    $repo"
done
