# Autopilot adoption for EPICs #888 and #910 (owner plan of 30 Sep 2026)

The launchd autopilots `~/.merglbot/gh-cost/autopilot.py` (#888) and
`~/.merglbot/gh-cost-910/autopilot.py` (#910) were changed three times without a
reviewed PR by their owning session. This package puts that running code under
review, adds the behaviour the owner decided on 30 Sep 2026, and installs it with
the same lock/backup/readback contract as `gh-cost-910-closeout` and `gh-cost-922-window`.

## 0. Status and known defects of the adopted code

This package documents code that is **running today**; it does not install anything by itself.

| Part | State |
|---|---|
| Adopted diffs (this directory) | review object; the live files equal their post-images |
| `cost_adoption.py` helpers | merged in #969 (`bc2bd71`) |
| `patch_autopilot.py` | merged in #970 (`95ac7d2`) |
| `install.py` (`code`, `record-owner-exception`, `release-hold`, `rollback`) | pending, separate PR after the patch |

Sections 2 and 3 describe the behaviour and install contract the patch and installer deliver;
until the installer runs, the live autopilots execute the adopted post-images unchanged.

**Known defects in `910_20260928_pilot.diff` (live `08477d87`), fixed by the patch, not here:**

1. A failed jobs request becomes an empty jobs list, so the run is silently skipped and a
   pilot verdict can pass on incomplete data. The patch marks the row incomplete
   (`incomplete_at`), returns without measuring and retries on the next tick.
2. The post-merge runs query has no upper bound and the cached runs are split only by
   `since`, so runs after the 14-day window can change the result. It also lists only
   completed runs, at most 30 per query, while infra has about 1 700 runs per window.
   The patch lists every UTC day of both windows completely (all statuses, paginated,
   bounded by exact timestamps), measures a uniform deterministic sample of about 300 runs
   per window, and holds the verdict while a sampled run is still running.

The report is due after 7 Oct 2026 23:05 Prague time; the installer must run before then
(target 6 Oct 17:00), otherwise the #910 autopilot is paused before that tick.

**Other review notes on the adopted code:**
- `owner_excepted()` rows are read field by field in the #892 closeout text. The only live
  row (`892|merglbot-core/merglbot-admin`) carries all five fields (`basis`, `decided_at`,
  `decided_at_prague`, `source`, `text`); #892 closed on 28 Sep 2026. The patch replaces the
  direct indexing with `exception_notes()`, which reads every field with a default.
- The low-traffic grace in `measure_runner_label` (and `measure_job_runs`) accepts zero
  observations on run counts alone and on an incomplete run census. The patch requires a
  complete census and a passed live caller check (`live_caller_config`) no older than 24 h,
  and it measures again any row the adopted code accepted without one. The live state had no
  such row on 1 Oct 2026.
- Runs without the pilot job are re-queried on later ticks. The patch records a finished run
  without the job as absent; a run whose job is still queued or running stays retryable and
  holds the verdict. A census that needs more calls than one tick continues on the next tick.

## 1. Adopted local edits (review object: `adopted/*.diff`)

| Diff | Pre-image (installed by) | Post-image | What changed |
|---|---|---|---|
| `910_20260926_env_wait.diff` | `7ff576da` (#938) | `51260863` | `measure_env_wait` skips documented runs and PRs whose base is not main (#912) |
| `910_20260928_pilot.diff` | `8538a0ed` (#943) | `08477d87` (live) | low-traffic grace in `measure_runner_label` (#913); DoD kind `arm64_pilot` and one-time `report_pilot` (#917); `technical_hold` skips any sub |
| `888_20260928_owner_exception.diff` | `f6fb6794` (#944) | `4b78d530` (live) | owner exception for #892 (keeps `met_at`, late literal comment) |

Wave-3 installs #941/#942 were applied on top of `51260863`; both live files are the
post-images above. Each diff reproduces its post-image byte for byte from the backed-up
pre-image (`patch -o`), which the installer re-checks locally before writing.

## 2. New behaviour (owner decisions of 30 Sep 2026)

- **#895 owner exception (888):** rows whose rollout PR was dropped (`acquisition-analysis`
  Denatura #170, Proteinaco #184) count once live-verified: PR closed unmerged and the
  recorded gitleaks contexts still required on main. Decision record: github#895 comment
  5917584272; its body is `decisions/895.cs.md`.
- **Low-traffic rule (888/910 `measure_job_runs`, 910 `measure_runner_label`):** after 14
  days with no disqualifying run in a complete census (every run listed, finished and read,
  measured by its current attempt), a row counts only when the caller is verified live on main
  (workflow files of the rollout PR, one pinned hub PR Gate call, and for #913 the effective
  runner `ubuntu-slim`, explicit or the pinned hub default). A check older than 24 h, or an
  acceptance the adopted code made without one, is measured again and never closes a sub-issue.
- **#917 pilot comparison (910):** every UTC day of both 14-day windows is listed completely
  and a uniform deterministic sample (about 300 runs per window) is measured by its first
  attempt; unfinished runs hold the verdict and a census spans ticks. Runs shorter than 60 s
  are gate outcomes (V6 rejection or nothing to test since infra#3342) and stay out of
  p50/p95; timed-out jobs count as failures. The stop rule is about time; failures are
  reported separately, and only verdicts of the current census version are reported.
- **Billing verdicts (#896, #922):** FAKT (≥ 80 % of model) and PARTIAL (> 0) are posted and
  close the sub-issue; BEZ ÚSPORY (≤ 0) is posted and stays open. A verdict waits until 72 h
  after the window's end and until every in-scope repository with a billable run in the window
  has usage dated inside the window from its newest billable run on (no per-repository export
  completeness marker exists, so the settlement time bounds a partially exported day). Missing
  or incomplete usage data is retried hourly and reported once as DATA_GAP after 72 hours. A
  close is recorded only after the issue reads back closed as completed and Done.
- **888 `close_epic`:** also requires every live native sub-issue closed and Done on
  Project 64 (so #909 keeps #888 open), checked at most hourly.
- **Old one-shot installers fail closed:** `gh-cost-910-closeout` anchors on the legacy
  `f"dod:{sub}"` key. The adopted code still contains it; the patch replaces it with
  `"dod:" + str(sub)`, so that installer no longer matches after installation. The
  installer re-runs every older `tools/gh-cost-*/patch_autopilot.py` in memory and aborts if
  one would rewrite code.

`cost_adoption.py` holds the helpers and is installed next to both autopilots.
`patch_autopilot.py` holds exact-string anchors that must match once; a fully patched
source is returned unchanged.

## 3. Install

Only from the protected, substantively V6-reviewed main commit, after `--dry-run`, with
freshly read SHA-256 digests. Checks OWNER_HOLD, takes both existing `autopilot/lock`
directories, backs up to `backups/adoption-<UTC>/` with a manifest, writes atomically and
reads back. Verification is the next natural tick; never run a tick manually.

```bash
python3 tools/gh-cost-autopilot-adoption/install.py code --dry-run \
  --expected-code-888 <sha256> --expected-state-888 <sha256> \
  --expected-code-910 <sha256> --expected-state-910 <sha256>
python3 tools/gh-cost-autopilot-adoption/install.py record-owner-exception --dry-run \
  --comment-url https://github.com/merglbot-core/github/issues/895#issuecomment-5917584272 \
  --expected-state-888 <sha256>
```

`release-hold` (after #917 is closed and Done) and `rollback` (only to the adopted image)
are documented in `install.py --help`.

Refs merglbot-core/github#888, #895, #910, #913, #917
