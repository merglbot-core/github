# Safe activation of the #922 window guard

Activate only from the protected, substantively V6-reviewed main commit. Compute
fresh SHA256s of `~/.merglbot/gh-cost-910/{autopilot.py,state.json}` immediately
before invoking the installer. Pass them as `--expected-code` and
`--expected-state`, and repeat `--followup org/repo#PR` for each verified follow-up.
All eight `BILLING_REQUIRED_FOLLOWUPS` are mandatory; empty and partial batches
fail before API calls or writes. Include future additional follow-ups too. The
same command accepts an already byte-identical reviewed patch for registration.
It also repairs missing merge metadata in existing verified rollout records:
fresh GitHub main-merge proof must match each registered head and any existing
timestamp/SHA. Only `merged_at` and `merge_sha` are filled, never state or DoD.
First use `--dry-run`; re-read hashes before the real install if any tick intervenes.
Every live follow-up is checked against GitHub before taking the existing lock.
The installer honors OWNER_HOLD and the core API reserve, refuses stale hashes,
backs up both files, writes atomically and restores both on replacement failure.
It refuses published/closed programs. It never resets #921's owner decision.

Do not manually run a tick or launch another observer. Inspect the next natural
launchd tick and its state/log to prove loading and the new window. The unchanged
`prs`, `dod` and `subs` sections must match the pre-install snapshot, except for
independent, evidenced natural autopilot activity.

Failure rollback is automatic while holding the lock. After a successful install,
the backup is evidence, not permission to rewind newer state: any later recovery
requires a fresh lock and comparison against newer ticks before restoring code
or state. Never restore old DoD or owner policy over new decisions.
