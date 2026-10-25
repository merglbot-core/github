#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
case "${RUNNER_TEMP:?}" in
  *$'\n'*|*$'\r'*) exit 1 ;;
esac
# No token is accepted as an action input or command-line argument.
report_dir=$(mktemp -d "${RUNNER_TEMP:?}/github-auth-observer.XXXXXXXX")
status=0
python3 -I "${PROJECT39_AUTH_OBSERVER_PATH:?}/observer.py" > "$report_dir/report.json" || status=$?
printf 'report_path=%s\n' "$report_dir/report.json" >> "${GITHUB_OUTPUT:?}"
exit "$status"
