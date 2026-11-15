  # Source-only opt-in: runtime installation and threshold need separate approval.
  if [ "${MERGLBOT_BUDGET_REFRESH_ENABLED:-0}" = "1" ]; then
    "$HOME/.merglbot/mem-guard/capped-run" --max-gb 2 --timeout 150 -- \
      /opt/homebrew/bin/python3 -I "$HOME/.merglbot/tools/cost-budget-report/refresh_cli.py" \
      --enabled --root "$budget_root" \
      --journal "$HOME/.merglbot/gh-cost-wave3/budget-refresh-state/journal.jsonl" \
      --refresh-after-hours "${MERGLBOT_BUDGET_REFRESH_HOURS:-unconfigured}" 1>&2 \
      || echo 'DATA_GAP — obnova rozpočtového vstupu neprošla; původní evidence zachována.'
  fi
