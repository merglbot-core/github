#!/usr/bin/env bash

# NOTE (reference-only): workflows inline these functions to avoid `source`-ing scripts
# from the repo checkout (RCE/exfil risk on PR branches). Keep this file aligned with:
# - .github/workflows/merglbot-pr-v3-on-demand.yml
# - merglbot-core/github/.github/workflows/merglbot-pr-assistant-v3-on-demand.yml (canonical implementation)

sanitize_model() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$raw" in
    *[[:space:]]*) raw="" ;;
  esac
  if [ -n "$raw" ] && ! [[ "$raw" =~ ^[A-Za-z0-9._-]+$ ]]; then
    raw=""
  fi
  printf '%s' "$raw"
}

sanitize_reasoning_effort() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr -d '\r' | tr '[:upper:]' '[:lower:]' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$raw" in
    low|medium|high|none) printf '%s' "$raw" ;;
    xhigh) printf '%s' "high" ;;
    *) printf '%s' "" ;;
  esac
}
