#!/usr/bin/env bash
set -euo pipefail

# shellcheck shell=bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=secret-scan-patterns.sh
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/secret-scan-patterns.sh"

# Google OAuth refresh token heuristic
echo 'ya29.ABCDEFGHIJKLMNOPQRSTUV' | grep -Eiq "$_GOOGLE_OAUTH_REFRESH"
if echo 'ya29XABCDEFGHIJ' | grep -Eiq "$_GOOGLE_OAUTH_REFRESH"; then
  echo "unexpected match for _GOOGLE_OAUTH_REFRESH (negative case)" >&2
  exit 1
fi

# JWT heuristic (strict-ish)
echo 'eyJAAAAAAAAAAAAAAAAAAAA.BBBBBBBBBBBBBBBBBBBB.CCCCCCCCCCCCCCCCCCCC' | grep -Eiq "$JWT_PATTERN_STRICT"
if echo 'eyJAAA.BBB.CCC' | grep -Eiq "$JWT_PATTERN_STRICT"; then
  echo "unexpected match for JWT_PATTERN_STRICT (negative case)" >&2
  exit 1
fi

echo "secret-scan-patterns smoke test: ok"
