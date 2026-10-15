#!/usr/bin/env bash
set -euo pipefail

: "${RUNNER_TEMP:?RUNNER_TEMP is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${PROJECT39_INVENTORY_ACTION_PATH:?action source path is required}"

report_path="$(mktemp "$RUNNER_TEMP/project39-npm-inventory.XXXXXXXX")"
printf 'report_path=%s\n' "$report_path" >> "$GITHUB_OUTPUT"

case "${PROJECT39_INVENTORY_SCOPE:-}" in
  core|proteinaco) ;;
  *)
    printf '%s\n' '{"status":"DATA_GAP","reason":"invalid_scope"}' > "$report_path"
    exit 1
    ;;
esac

python3 "$PROJECT39_INVENTORY_ACTION_PATH/package_inventory.py" \
  --scope "$PROJECT39_INVENTORY_SCOPE" \
  --lockfile "$GITHUB_WORKSPACE/package-lock.json" > "$report_path"
