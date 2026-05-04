# GHAU-WASTE: github ent-dependabot weekly timeout

Project: GitHub Actions 30-Day Consumption Audit v1 (`4863ff67-0df7-4faf-b9de-5911b1458ab8`)

## Why

Audit telemetry over 2026-03-31 → 2026-04-30 attributed Actions minutes to the weekly enterprise Dependabot closeout job in `merglbot-core/github` running without a job-level `timeout-minutes` cap. When a downstream operation hung, the job would consume the default 6-hour ceiling.

## Change

`.github/workflows/ent-dependabot-weekly.yml` is updated so that:

- The closeout job declares an explicit `timeout-minutes` cap matching observed p95 runtime plus a small buffer.
- Schedule and trigger wiring are unchanged.

## Merge policy

This change touches `.github/workflows/**` and is therefore `human_merge_only`.

## Reference

- Cost audit window: `2026-03-31T08:27:19Z` → `2026-04-30T08:27:19Z`
- Candidate mutex: `ghau-opt:merglbot-core/github:ent-dependabot-weekly-timeout`
