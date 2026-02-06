#!/usr/bin/env bash
# Find insecure reusable workflow references that use @main.
#
# Goal (SEC-P1-004):
# - No consumer repo should reference reusable workflows via @main
#
# This script scans the local workspace clones (fast, no API required).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/audit/find-reusable-workflow-main-refs.sh [--root <path>] [--fail]

Options:
  --root   Workspace root to scan (default: auto-detected relative to this repo)
  --fail   Exit 1 if any @main references are found

Scans:
  - **/.github/workflows/*.yml
  - **/.github/workflows/*.yaml

Looks for:
  - uses: merglbot-core/github/.github/workflows/<...>@main
EOF
}

ROOT=""
FAIL="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="${2:-}"
      shift 2
      ;;
    --fail)
      FAIL="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "::error::Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [ -z "$ROOT" ]; then
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
fi

PATTERN='merglbot-core/github/.github/workflows/.*@main'

echo "Scanning for reusable workflow @main references..."
echo "Root: $ROOT"
echo "Pattern: $PATTERN"
echo ""

matches=0

if command -v rg >/dev/null 2>&1; then
  if rg -n --glob '**/.github/workflows/*.{yml,yaml}' "$PATTERN" "$ROOT"; then
    matches=1
  fi
else
  # Fallback to grep (slower, but always available)
  if grep -RIn --include='*.yml' --include='*.yaml' "$PATTERN" "$ROOT" 2>/dev/null; then
    matches=1
  fi
fi

if [ "$matches" -eq 0 ]; then
  echo ""
  echo "✅ No @main references found."
  exit 0
fi

echo ""
echo "⚠️ Found @main references. Replace with a protected tag (e.g. vX.Y.Z) or a commit SHA."

if [ "$FAIL" = "true" ]; then
  exit 1
fi
