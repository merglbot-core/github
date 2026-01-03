#!/usr/bin/env bash
# Purpose: Set org-level GitHub Actions variables for PR Assistant v3 model defaults.
# Usage:
#   ./scripts/pr-assistant/set-org-vars.sh --dry-run
#   ./scripts/pr-assistant/set-org-vars.sh
#
# Requires: gh auth with admin:org scope.

set -euo pipefail

DRY_RUN="false"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="true" ;;
  esac
done

OPENAI_MODEL_DEFAULT="${MERGLBOT_OPENAI_MODEL_DEFAULT:-gpt-5.2}"
ANTHROPIC_MODEL_DEFAULT="${MERGLBOT_ANTHROPIC_MODEL_DEFAULT:-claude-opus-4-5-20250929}"

ORGS=(
  "merglbot-core"
  "merglbot-public"
  "merglbot-denatura"
  "merglbot-proteinaco"
  "merglbot-ruzovyslon"
  "merglbot-extractors"
  "merglbot-milan-private"
  "merglbot-autodoplnky"
  "merglbot-hodinarstvibechyne"
  "merglbot-kiteboarding"
)

set_var() {
  local org="$1"
  local name="$2"
  local value="$3"

  if [ "$DRY_RUN" == "true" ]; then
    echo "DRY: $org -> $name=$value"
    return 0
  fi

  if gh api "/orgs/$org/actions/variables/$name" > /dev/null 2>&1; then
    gh api --method PATCH "/orgs/$org/actions/variables/$name" \
      -f value="$value" \
      -f visibility="all" > /dev/null
    echo "✅ $org updated: $name=$value"
  else
    gh api --method POST "/orgs/$org/actions/variables" \
      -f name="$name" \
      -f value="$value" \
      -f visibility="all" > /dev/null
    echo "✅ $org created: $name=$value"
  fi
}

echo "Mode:      $([ "$DRY_RUN" == "true" ] && echo "DRY RUN" || echo "APPLY")"
echo "OpenAI:    $OPENAI_MODEL_DEFAULT"
echo "Anthropic: $ANTHROPIC_MODEL_DEFAULT"
echo ""

for org in "${ORGS[@]}"; do
  set_var "$org" "MERGLBOT_OPENAI_MODEL" "$OPENAI_MODEL_DEFAULT"
  set_var "$org" "MERGLBOT_ANTHROPIC_MODEL" "$ANTHROPIC_MODEL_DEFAULT"
done

