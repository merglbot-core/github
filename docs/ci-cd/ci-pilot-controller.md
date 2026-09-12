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
An open phase without in-window observations stays pending for its first event;
a closed empty phase has a data gap, including an interval with retained heads but
no matching attempts. Neither is complete evidence. This helper alone activates
no experiment.

## Automatic infra experiment (version 1)

Use the existing runtime/lock/heartbeat. `begin-experiment --apply` requires no old
receipt, verified global cleanup and terminal old history; historical counts stay.
`activate --receipt <file> --apply` accepts repo=merglbot-core/infra, positive `pr`,
`kind`=synthetic|natural, `mode`=baseline|delay and live `protection_sha256`.
Reviewed source hashes, protections and environments bind activation.
Errors, partial activation, expiry, close/merge and changed scope/protection clean
both old and new selectors. `stop-selection --apply` ends a measurement interval.
At most three distinct PRs per kind, one selection globally, unchanged Sep14 deadline.
The reviewed observer measures all attempts; gaps stay explicit. Include admission
runner time and checkout proof. The agent owns V6/merge and GO evidence.
Zero-write aborts stay audited but unmeasured; attempted writes remain conservative.
No production rollout.


## Natural compatibility case 2733

The automatic adapter now accepts only `merglbot-core/infra#2733`, in natural
`delay` mode. Its two assessed Python paths and base-owned classifier digest
are pinned in `automatic.py`. This does not activate selection. Install only
after the replacement classifier PR has been reviewed, merged, and its live
base digest verified. Reconcile that digest after any classifier review fix.

Use a separate state directory for this compatibility case, preserving all old
state and case counts. The existing absolute September 14, 2026, 19:03:19 UTC
cutoff is a shorter bound than seven days. Only one selection may exist across
all supported repositories; no synthetic or baseline mode for this case.
Prove a real supervisor wake and exact loaded source before activation.

The base-owned classifier rechecks each event and applies its conservative
Python capability envelope; the supervising agent also reads each new head's
full semantic diff. A syntax refusal is an immediate-test fallback, not proof
of a V6 finding. Scope expansion ends the case. Current scope admission never
substitutes for full exact-head V6 evidence and required-check verification.

Closed compatibility PRs and explicit case termination are persistent terminal
states. Selectors are removed first; already admitted jobs are observed until
complete before the supervisor unloads. A read gap cannot certify completion.
Historical experiment phase transitions retain their existing behavior.

Success requires captured final pending-environment timer, checkout parents,
substantive V6 and standard merge while selection remains active. Do not count
mere timestamp spacing or an immediate final run as delayed acceptance.
Refs: merglbot-core/infra#2688.

### Live main source and PR metadata base

Automatic admission validates the reviewed classifier and workflow at the live
`refs/heads/main` commit, with a second reference read after the snapshot. The
PR metadata base may lag that branch; it remains unchanged in the receipt and
PR identity validation. Both the metadata-base workflow and live-main workflow
must match the pinned workflow. A branch movement during validation refuses
admission. The per-event classifier still requires the event head/base to match
live refs; selecting a PR does not approve a stale event or change merge rules.

The previous #2683 case merged before activation; #2696 stays closed. The
replacement #2733 assessment covers the coverage calculator and its head
regression tests, with unchanged ten-minute waiting and September 14 cutoff.
Reuse the inactive compatibility state and preserve the two historical natural
case counts in the overall five-case accounting. The selected business PR is
completed by its development agent; the pilot does not take over that workstream.

### September 12 assessed repair refresh

The classifier digest now binds the assessed ancestor
`0146939ca86efcd0009cb290707ba5106efb79c5` (infra #2737). The natural run
34708056870 selected immediate tests against the older ancestor and is not a
delayed case. Its completed phase stays archived; never erase its terminal
state to claim uninterrupted selection. Any new phase uses a separate state
directory and the same supervisor label only after the previous service is
unloaded. Carry forward the overall case ledger and original experiment end.

This update does not activate selection.
The automatic compatibility adapter removes selection after 24 hours without
an observed first-attempt natural event within that selection window. A late
event or rerun cannot reset the window. Already admitted jobs are drained before
the supervisor stops, and empty successful inventories are an expected timeout
outcome. Failed inventory reads remain data gaps. The absolute September 14
deadline is unchanged; a new candidate still requires a fresh scope assessment.

### Recovery after an empty read-failure phase

After a GitHub command failure, selection remains off until all inventory and
per-head reads succeed. A successfully read stopped interval with no events is
recorded as `empty_phase_verified`, not as a tested or accepted case. A later
read failure clears that observation and remains a data gap. Existing terminal
case stops are unchanged; recovery does not automatically reselect a PR.

If the operator reselects the same natural PR through the existing checked
activation path, its 24-hour window starts at its earliest phase in the
experiment. Prior timely events are retained; an expired retry cannot write
a selector. Phase history and the absolute cutoff are preserved.

### Filter-pinning assessment binding

The adapter digest binds the bounded assessment of business head
`d8b7e3e171d5ac5a91b39ec2d258fdd66cf0e8ec`. Install only after the matching
infra classifier is reviewed and merged. Keep the current recovered state and
its earliest selection window; no fresh experiment, history reset, automatic
reselection, or wider path grant is introduced. Revalidate the then-current
business head before activation. This binding is not business V6 approval.
