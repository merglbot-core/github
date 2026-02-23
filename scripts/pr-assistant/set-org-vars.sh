#!/usr/bin/env bash
# Purpose: Set org-level GitHub Actions variables for PR Assistant v3 model defaults.
# Usage:
#   ./scripts/pr-assistant/set-org-vars.sh --dry-run
#   ./scripts/pr-assistant/set-org-vars.sh
#
# Requires: gh auth with admin:org scope.

set -euo pipefail

if [ -z "${BASH_VERSION:-}" ]; then
  echo "ERROR: This script must be run with bash (do not use sh)." >&2
  exit 2
fi

DRY_RUN="false"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="true" ;;
  esac
done

trim_ws() {
  printf '%s' "${1:-}" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'
}

sanitize_model() {
  local raw
  raw="$(trim_ws "${1:-}")"
  case "$raw" in
    *[[:space:]]*) raw="" ;;
  esac
  case "$raw" in
    *[!A-Za-z0-9._-]* ) raw="" ;;
  esac
  printf '%s' "$raw"
}

sanitize_reasoning_effort() {
  local raw
  raw="$(trim_ws "${1:-}")"
  raw="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
  case "$raw" in
    low|medium|high|xhigh) printf '%s' "$raw" ;;
    *) printf '%s' "" ;;
  esac
}

OPENAI_MODEL_DEFAULT="$(sanitize_model "${MERGLBOT_OPENAI_MODEL_DEFAULT:-gpt-5-mini}")"
ANTHROPIC_MODEL_DEFAULT="$(sanitize_model "${MERGLBOT_ANTHROPIC_MODEL_DEFAULT:-claude-sonnet-4-6}")"
OPENAI_SYNTHESIS_MODEL_DEFAULT="$(sanitize_model "${MERGLBOT_OPENAI_MODEL_SYNTHESIS_DEFAULT:-gpt-5.2}")"
OPENAI_SYNTHESIS_REASONING_EFFORT_DEFAULT="$(sanitize_reasoning_effort "${MERGLBOT_OPENAI_REASONING_EFFORT_SYNTHESIS_DEFAULT:-medium}")"

if [ -z "$OPENAI_MODEL_DEFAULT" ]; then
  OPENAI_MODEL_DEFAULT="gpt-5-mini"
fi
if [ -z "$ANTHROPIC_MODEL_DEFAULT" ]; then
  ANTHROPIC_MODEL_DEFAULT="claude-sonnet-4-6"
fi
if [ -z "$OPENAI_SYNTHESIS_MODEL_DEFAULT" ]; then
  OPENAI_SYNTHESIS_MODEL_DEFAULT="gpt-5.2"
fi
if [ -z "$OPENAI_SYNTHESIS_REASONING_EFFORT_DEFAULT" ]; then
  echo "ERROR: Invalid MERGLBOT_OPENAI_REASONING_EFFORT_SYNTHESIS_DEFAULT (expected low|medium|high|xhigh)" >&2
  exit 1
fi

ORGS=(
  "merglbot-core"
  "merglbot-public"
  "merglbot-cerano"
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

  if [ "$DRY_RUN" = "true" ]; then
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

echo "Mode:      $([ "$DRY_RUN" = "true" ] && echo "DRY RUN" || echo "APPLY")"
echo "OpenAI:    $OPENAI_MODEL_DEFAULT"
echo "Anthropic: $ANTHROPIC_MODEL_DEFAULT"
echo "Synthesis: $OPENAI_SYNTHESIS_MODEL_DEFAULT (reasoning_effort=$OPENAI_SYNTHESIS_REASONING_EFFORT_DEFAULT)"
echo ""

for org in "${ORGS[@]}"; do
  set_var "$org" "MERGLBOT_OPENAI_MODEL" "$OPENAI_MODEL_DEFAULT"
  set_var "$org" "MERGLBOT_ANTHROPIC_MODEL" "$ANTHROPIC_MODEL_DEFAULT"
  set_var "$org" "MERGLBOT_OPENAI_MODEL_SYNTHESIS" "$OPENAI_SYNTHESIS_MODEL_DEFAULT"
  set_var "$org" "MERGLBOT_OPENAI_REASONING_EFFORT_SYNTHESIS" "$OPENAI_SYNTHESIS_REASONING_EFFORT_DEFAULT"
done
