# Bounded CI delay supervisor

```sh
python3 scripts/ci-pilot/controller.py tick --state-dir /absolute/state
python3 scripts/ci-pilot/controller.py activate --state-dir /absolute/state --receipt /absolute/receipt.json --apply
python3 -m unittest discover -s tests -p test_ci_pilot.py
```

Without --apply GitHub is read-only; local state is atomic and locked. Only
CI_DELAY_PILOT_PR/SHA are mutated: install SHA first, delete PR first.
Limits: one active PR, five observed-delay PRs, 2026-09-14T19:03:19Z and 24h
without delay. Local/global OWNER_HOLD, PR holds, changed or missing evidence
trigger cleanup/readback. Active ticks recheck holds/time after observations.

Agent-reviewed full-diff receipt: repo, pr, head/base, sorted paths, eligible,
assessment, diff_sha256, workflow_sha256, protection_sha256 (SHA256 of sorted
JSON {protection, rules}). Only app code/tests and companion docs qualify;
exclude TF/IAM/auth/secrets/workflows/deploy/dependencies/persistent schemas.

Corrupt durable state returns recovery_required/unverified and exit 1 even
when selector cleanup is verified; repeated invocations cannot reset history.
Recovery: stop the scheduler, retain the corrupt file, restore a verified
backup under controller.lock, then reconcile the union of all observed-case
identities, receipts and measurements against retained/live evidence. Preserve
all counted PRs; never replace state with {} or zero the budget. If completeness
cannot be proved, keep recovery_required. Re-run tick --apply and verify both
cleanup and the restored history before admitting another case.
