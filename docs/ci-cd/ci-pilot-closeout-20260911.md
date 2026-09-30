# CI admission experiment: NO-GO for rollout

Evidence snapshot: 2026-09-11, 12:24 UTC. Tracking issue:
[infra#2643](https://github.com/merglbot-core/infra/issues/2643).

## Decision and scope

The bounded successor experiment ended with **insufficient evidence**. This does
not demonstrate that a ten-minute delay is defective. There is no authorization
for permanent rollout, fifteen-minute delay, replacement synthetic identities or
an extended experiment. Existing tests and branch protections remain required.

The approved acceptance contract required three paired synthetic baseline/delay
sequences within at most three synthetic PR identities, plus two natural delayed
CI / substantive V6 / authorized autonomous merge completions without override.
The first identity merged before its delay phase. Three complete pairs were no
longer achievable within the cap; the experiment was stopped at a definitive
NO-GO instead of spending on incomplete replacement evidence.

## Observed execution and costs

[Synthetic infra#2680](https://github.com/merglbot-core/infra/pull/2680) merged at
`d917f62f893de7686b52fcff2e40a7bb8d224772` on 2026-09-11 at 10:43:57 UTC.
Only setup and baseline A/B were recorded; no paired delay phase or new natural
completion exists in the successor ledger. The GitHub merge actor does not
establish whether a human or automation executed the merge.

| Scope | Known runner duration | Limitation |
| --- | --- | --- |
| Baseline A/B target workflow | 490 seconds (8.17 minutes) | Includes admission overhead; no delay comparator |
| All workflows on the three synthetic heads | 978 seconds (16.30 minutes) | 33 terminal jobs; seven jobs have unknown duration |

Baseline runs were 34589117154 and 34589305627. Baseline A's test job was
cancelled **after** its runner started; it is not a zero-runner cancellation.
The all-workflow total includes setup and overlaps the target-workflow row;
do not add the rows. It is a metadata-derived lower bound, not rounded billed
minutes, USD, avoided work or whole-program net savings. Source implementation
PR costs and AI billing are outside this case inventory and remain DATA_GAP.
Missing checkout/base evidence in observer records remains DATA_GAP even when
an aggregate history counter reports zero gaps.

## Installation, cleanup and provenance

[Integration infra#2644](https://github.com/merglbot-core/infra/pull/2644) merged
at `2fca2f13879aa837aa22e8ae109403fa50373f7f` using owner execution. This is an
override prerequisite, not an autonomous natural case. The reviewed controller
release `f465be493ba67ec7d331560d73d3b4124328a1fa` was installed and an actual
successful scheduled wake was observed. Installation alone did not satisfy
experiment acceptance.

At 12:07 UTC, cleanup readback confirmed all five selector names absent across
infra, denatura-forecast-exporter and denatura-fb-viz:
`CI_DELAY_PILOT_PR`, `CI_DELAY_PILOT_SHA`, `CI_DELAY_PILOT_BASE_SHA`,
`CI_DEBOUNCE_TEST_PR`, and `CI_DEBOUNCE_TEST_MODE`. Admitted history had no
unfinished runs. The dedicated local supervisor was unloaded; its absence was
checked with a readable GUI-domain positive control. These are timestamped
observations, not a perpetual guarantee. The separate heartbeat remained only
for documentation and issue closeout and must stop when that work ends.

Preserve the original pilot's two counted cases separately. Preserve the
[github#823](https://github.com/merglbot-core/github/pull/823) premature-merge
incident: later authorization is not retrospective approval. Neither original
pilot evidence nor implementation merges satisfy successor natural-case quotas.
No Terraform apply or production rollout is part of this closeout.

## Evidence and remaining acceptance gaps

The redacted session evidence directory is
`/tmp/merglbot-ci-admission-evidence-20260910/`: `HANDOFF.md`,
`TEST_EXECUTION.md`, `SYNTHETIC_1_AUDIT.md`,
`SYNTHETIC_ALL_WORKFLOW_ACCOUNTING.json`, `SUPERVISOR_STOP_READBACK.json`,
and `CLOSEOUT_REPORT.md`. These local files are supporting evidence, not durable
public links. The tracking issue retains the shareable outcome and disposition.

Three paired comparisons, the complete live safety matrix and two natural
closeouts were not achieved. Timing and checkout/base gaps remain unresolved.
Do not close those acceptance criteria as successful. Close the experiment with
an explicit NO-GO disposition; keep parent FinOps epic github#782 open. Any later
experiment needs a separately agreed scope and a complete cost/evidence design.
