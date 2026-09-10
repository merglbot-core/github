# Bounded CI delay supervisor

```sh
python3 scripts/ci-pilot/controller.py tick --state-dir /absolute/state
python3 scripts/ci-pilot/controller.py activate --state-dir /absolute/state --receipt /absolute/receipt.json --apply
python3 -m unittest discover -s tests -p test_ci_pilot.py
```

Without --apply GitHub is read-only; local state is atomic and locked. Only
CI_DELAY_PILOT_PR/SHA/BASE_SHA are the only writable variables. Infra uses
all three: install base SHA, head SHA, then PR number. Exporter and fb-viz
retain their audited two-variable contract. Cleanup deletes PR, head SHA,
then base SHA in every repository, including partial or orphaned selectors.
Limits: one active PR, five observed-delay PRs, 2026-09-14T19:03:19Z and 24h
without delay. Local/global OWNER_HOLD, PR holds, changed or missing evidence
trigger cleanup/readback. Active ticks recheck holds/time after observations.

Agent-reviewed full-diff receipt: repo, pr, head/base, sorted paths, eligible,
assessment, diff_sha256, workflow_sha256, protection_sha256 (SHA256 of sorted
JSON {protection, rules}). Only app code/tests and companion docs qualify;
exclude TF/IAM/auth/secrets/workflows/deploy/dependencies/persistent schemas.
Hash protection with Python json.dumps({"protection": protection, "rules": rules},
sort_keys=True), preserving its default separators. Hash the full GitHub diff
and base workflow text verbatim; head/base are full SHAs, eligible must be true.
Runner seconds and observed waits are evidence, not billing savings. Persist
no raw diff, logs, credentials or transcripts.

Corrupt durable state returns recovery_required/unverified and exit 1 even
when selector cleanup is verified; repeated invocations cannot reset history.
Recovery: stop the scheduler, retain the corrupt file, restore a verified
backup under controller.lock, then reconcile the union of all observed-case
identities, receipts and measurements against retained/live evidence. Preserve
all counted PRs; never replace state with {} or zero the budget. If completeness
cannot be proved, keep recovery_required. Re-run tick --apply and verify both
cleanup and the restored history before admitting another case.

The no-delay timeout is per repository/PR: `pr_windows` retains the first selector
admission intent across head changes, cleanup and restarts. Re-admission never
restarts its 24 hours. Each head still uses its own `started_at` measurement
boundary. Once a delay is observed (including retained `counted_prs` evidence),
that PR is exempt from the no-delay timeout; closure, holds, the global deadline
and five-case limit still apply. Legacy active/history admission timestamps migrate
to the earliest retained start; imported `historical_only` run timestamps are not
selection proof. Invalid windows require recovery without overwriting durable state.
When recovering state, preserve `pr_windows` alongside counts and historical receipts;
never delete a window or replace its first start with a later admission.

An expired PR with no observed delay remains ineligible for the rest of this pilot.
The semantic selector must skip its retained expired `pr_windows` entry and choose
the next eligible PR, rather than repeatedly retrying the oldest PR.
