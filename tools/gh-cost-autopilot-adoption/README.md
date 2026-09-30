# Autopilot adoption for EPICs #888 and #910 (owner plan of 30 Sep 2026)

The launchd autopilots `~/.merglbot/gh-cost/autopilot.py` (#888) and
`~/.merglbot/gh-cost-910/autopilot.py` (#910) were changed three times without a
reviewed PR by their owning session. This package puts that running code under
review, adds the behaviour the owner decided on 30 Sep 2026, and installs it with
the same lock/backup/readback contract as `gh-cost-910-closeout` and `gh-cost-922-window`.

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
- **Low-traffic rule (888 `measure_job_runs`, 910 `measure_runner_label`):** after 14 days
  with no disqualifying run, a row counts only when the caller is verified live on main
  (workflow files of the rollout PR, one pinned hub PR Gate call, and for #913 the effective
  runner `ubuntu-slim`, explicit or the pinned hub default).
- **#917 pilot comparison (910):** runs shorter than 60 s are gate outcomes (V6 rejection or
  nothing to test since infra#3342) and stay out of p50/p95 and the failure rate. The stop
  rule is about time; failures are reported separately.
- **Billing verdicts (#896, #922):** FAKT (≥ 80 % of model) and PARTIAL (> 0) are posted and
  close the sub-issue; BEZ ÚSPORY (≤ 0) is posted and stays open. Missing usage data is
  retried hourly and reported once as DATA_GAP after 72 hours.
- **888 `close_epic`:** also requires every live native sub-issue closed and Done on
  Project 64 (so #909 keeps #888 open), checked at most hourly.
- **Old one-shot installers fail closed:** `gh-cost-910-closeout` anchors on the legacy
  `f"dod:{sub}"` key, which the adopted code no longer contains. The installer re-runs every
  older `tools/gh-cost-*/patch_autopilot.py` in memory and aborts if one would rewrite code.

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
