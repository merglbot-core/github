#!/usr/bin/env bash

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

