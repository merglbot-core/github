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

if [ ! -f "$SOURCE_WORKFLOW" ]; then
  echo "ERROR: Source workflow not found: $SOURCE_WORKFLOW" >&2
  exit 1
fi

# Platform scope (exclude Merglevsky-cz entirely).
TARGET_REPOS=(
  "merglbot-core/ai_prompts"
  "merglbot-core/dataform"
  "merglbot-core/infra"
  "merglbot-core/merglbot-admin"
  "merglbot-core/platform"
  "merglbot-core/tf-modules-"
  "merglbot-public/docs"
  "merglbot-public/website"
  "merglbot-proteinaco/abc_product_material_analysis"
  "merglbot-proteinaco/btf-viz"
  "merglbot-proteinaco/proteinaco-web"
  "merglbot-proteinaco/viz-api"
  "merglbot-denatura/denatura-btf-data"
  "merglbot-denatura/denatura-fb-viz"
  "merglbot-denatura/marketing_actions_detector"
  "merglbot-ruzovyslon/business_forecasting"
  "merglbot-ruzovyslon/kbc_data_quality_metodology"
  "merglbot-ruzovyslon/ruzovyslon-web"
  "merglbot-ruzovyslon/viz-api"
  "merglbot-extractors/facebook-extractor"
  "merglbot-milan-private/fakturoid"
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