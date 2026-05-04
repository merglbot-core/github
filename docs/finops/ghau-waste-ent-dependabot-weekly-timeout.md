# GHAU-WASTE: github ent-dependabot weekly closeout iteration caps + workflow_dispatch dedup

Project: GitHub Actions 30-Day Consumption Audit v1 (`4863ff67-0df7-4faf-b9de-5911b1458ab8`)

## Why

Audit telemetry over 2026-03-31 → 2026-04-30 attributed Actions minutes to the weekly enterprise Dependabot closeout job in `merglbot-core/github` running through long fix-and-review tail iterations and to redundant manual `workflow_dispatch` reruns that piled up alongside in-flight runs.

## Change

`.github/workflows/ent-dependabot-weekly.yml` is updated so that:

- The reusable closeout workflow is invoked with explicit `max_fix_iterations: 2` and `max_review_iterations: 2`. Tail iterations beyond these caps were the long-tail consumer of weekly minutes.
- `concurrency.cancel-in-progress` is now `${{ github.event_name == 'workflow_dispatch' }}` (previously `false`). This deduplicates manual reruns started while an earlier manual run is still in flight, without affecting `schedule`-triggered runs.
- `concurrency.group` is `ent-dependabot-weekly-closeout-${{ github.event_name }}` so that `schedule` and `workflow_dispatch` runs occupy disjoint groups; a manual rerun can never cancel an in-flight scheduled weekly closeout.

## Operator notes

- The change does not introduce a job-level `timeout-minutes` cap. The waste reduction here is iteration-count and dedup, not wall-clock timeout. The candidate slug retains `weekly-timeout` as a historical lane key but the implementation is iteration-cap + trigger-isolated dedup.
- Manual `workflow_dispatch` reruns are now deduplicated within their own group; in-flight scheduled runs are unaffected.

## Merge policy

This change touches `.github/workflows/**` and is therefore `human_merge_only`.

## Reference

- Cost audit window: `2026-03-31T08:27:19Z` → `2026-04-30T08:27:19Z`
- Candidate mutex: `ghau-opt:merglbot-core/github:ent-dependabot-weekly-timeout`
