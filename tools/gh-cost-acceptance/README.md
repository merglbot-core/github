# EPIC #910 local acceptance repair

This package fixes two false negatives in the already-running local autopilot.
It changes no workflow, V6 gate, branch protection or billing acceptance.

`semantic.py` accepts the deployed parameterized `ubuntu-slim` hub and excludes
only proven pre-merge commit parents from post-merge push-run counting. It rejects
incomplete or duplicate run pages. `patch_autopilot.py` applies those predicates
to the existing script and rejects source drift. `install.py` holds the existing
autopilot lock, checks OWNER_HOLD and the expected source hash, backs up code and
state, atomically installs the module and script, and leaves state untouched.

Before installation, obtain substantive exact-head V6 approval of this source.
Read the current autopilot SHA256 and run `install.py --expected-sha SHA --dry-run`
against that exact source. Then run the same command without `--dry-run` after
confirming the existing lock is free. A failed lock acquisition is a wait on the
existing tick, not permission to remove its lock. The installer prints a backup
directory; `install.py --rollback DIR` restores only the old code after checking
that the installed code has not changed. It never restores an older state.json.

After install, wait for the next natural autopilot tick and verify current main,
the refreshed #913/#915 measurements, the full sub-issue evidence and rate-limit
headers. Do not force runs to satisfy a numeric DoD. A verified configuration
does not prove settled financial savings.

Tests: `python3 -m unittest discover -s tools/gh-cost-acceptance -p 'test_*.py'`.

Refs merglbot-core/github#913
Refs merglbot-core/github#915
