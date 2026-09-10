# Bounded CI delay supervisor

```sh
python3 scripts/ci-pilot/controller.py tick --state-dir /absolute/state
python3 scripts/ci-pilot/controller.py activate --state-dir /absolute/state --receipt /absolute/receipt.json --apply
python3 -m unittest discover -s tests -p test_ci_pilot.py
```

Without --apply GitHub is read-only; local state is atomic and locked. The semantic
pilot writes CI_DELAY_PILOT_PR/SHA/BASE_SHA. Infra uses
all three: install base SHA, head SHA, then PR number. Exporter and fb-viz
retain their audited two-variable contract.

The successor test program (infra#2643, owner-approved 2026-09-10) additionally
authorizes the adapter to set CI_DEBOUNCE_TEST_PR and CI_DEBOUNCE_TEST_MODE in
merglbot-core/infra only. Setting either variable in exporter or fb-viz is denied.
Deletion of all five exact variable names is authorized across the three pilot
repositories to remove partial or orphaned selectors. No other repository or
variable is writable. All writes still use the guarded command wrapper.
Cleanup removes debounce PR/mode first, then semantic PR/head/base, and verifies
global absence. The semantic controller does not activate the successor selectors;
it detects them as conflicting/orphaned admission and cleans them. Runtime
installation and successor activation require their separate reviewed delivery.
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
boundary. Retired intervals also require `stopped_at`: attempts starting at or
after cleanup are excluded, while already admitted jobs are measured through
completion. Multiple admissions of one head retain separate latest interval
measurements and aggregate without counting an attempt twice. Exact duplicate
history intervals are ignored; overlapping intervals, missing boundaries and
runner timing gaps prevent a complete-evidence verdict. Recovery must preserve
the interval boundaries and `interval_measurements`, as well as prior snapshots.
Once a delay is observed (including retained `counted_prs` evidence),
that PR is exempt from the no-delay timeout; closure, holds, the global deadline
and five-case limit still apply. Legacy active/history admission timestamps migrate
to the earliest retained start; imported `historical_only` run timestamps are not
selection proof. Invalid windows require recovery without overwriting durable state.
When recovering state, preserve `pr_windows` alongside counts and historical receipts;
never delete a window or replace its first start with a later admission.

An expired PR with no observed delay remains ineligible for the rest of this pilot.
The semantic selector must skip its retained expired `pr_windows` entry and choose
the next eligible PR, rather than repeatedly retrying the oldest PR.


## Automatic infra experiment (version 1)

Use the existing runtime/lock/heartbeat. `begin-experiment --apply` requires no old
receipt, verified global cleanup and terminal old history; historical counts stay.
`activate --receipt <file> --apply` accepts repo=merglbot-core/infra, positive `pr`,
`kind`=synthetic|natural, `mode`=baseline|delay and live `protection_sha256`.
Reviewed source hashes, protections and environments bind activation.
Errors, partial activation, expiry, close/merge and changed scope/protection clean
both old and new selectors. `stop-selection --apply` ends a measurement interval.
At most three distinct PRs per kind, one selection globally, unchanged Sep14 deadline.
Intermediate heads and all attempts are measured; completed results are reused
only against unchanged live run/attempt inventory. Gaps remain explicit. Admission
runner time counts; actual checkout/base still need separate execution evidence.
The agent owns V6/merge and the separate GO audit. No production rollout.
### Bounded experiment supervisor recovery

The test successor must verify `runtime.ready(state_dir, now)` before writing a
selector: a successful wake within 360 seconds, a non-stopped healthy runtime,
and the loaded launchd job bound to this exact runtime release and state directory.
The first active experiment index (zero) also forces the five-minute cadence.

Recovery requires the reviewed successor controller (#832) advertising
`EXPERIMENT_STATE_VERSION=1`; the legacy controller cannot be rearmed. After
retiring the legacy supervisor, create the empty bounded experiment state, then
run `runtime.py recover-experiment --state-dir <state-dir>` under the runtime lock.
Recovery requires no active case or legacy receipt, no holds, time before the
original deadline, globally verified selector cleanup and complete old run history.
It preserves all counters and history. It only rearms the plan; bootstrap the
reviewed test supervisor and verify an actual successful wake before selection.
Recovery alone is not supervisor health or experiment delivery evidence.

Recovery covers both explicit retirement of the original two-case pilot and
case-limit shutdown. It does not require five historical cases or trust an old
`runtime.json.stopped` flag: manual bootout can leave that flag false. It requires
a live, explicitly absent supervisor service, with a readable launchd-domain
positive control, before cleanup and again before rearming. A loaded supervisor
or an unavailable/ambiguous launchd read blocks recovery without resetting its plan.
Historical case counts remain unchanged and do not set the successor's case limit.

### Experiment observation helper

`experiment_measurements.py` provides read-only `observe`/`histories` for successor
phases. It discovers all run attempts (including reruns of originally older runs),
assigns them through the adapter's start/stop interval, preserves observation snapshots,
and reuses completed measurements only for the same attempt inventory and interval.
Missing evidence remains a data gap. This helper alone activates no experiment.
