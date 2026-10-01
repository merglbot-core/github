# Autopilot adoption for EPICs #888 and #910 (owner plan of 30 Sep 2026)

The launchd autopilots `~/.merglbot/gh-cost/autopilot.py` (#888) and
`~/.merglbot/gh-cost-910/autopilot.py` (#910) were changed three times without a
reviewed PR by their owning session. This package replaced that code with reviewed code,
added the behaviour the owner decided on 30 Sep 2026, and installed it with the same
lock/backup/readback contract as `gh-cost-910-closeout` and `gh-cost-922-window`.

## 0. Status

| Part | State |
|---|---|
| Record of the unreviewed edits | provenance comments github#888 (5922472926) and github#910 (5922473236), see section 1 |
| `cost_adoption.py` helpers | merged in #969 (`bc2bd71`) |
| `patch_autopilot.py` | merged in #970 (`95ac7d2`) |
| `install.py` (`code`, `record-owner-exception`, `release-hold`, `rollback`) | merged in #974 (`8cfec93`) |
| Installation | 1 Oct 2026 02:43 Prague from main `8cfec93`: 888 `4b78d530` → `26e774f6`, 910 `08477d87` → `8997ac43`, helper `8db5ede8`; #895 owner exception recorded 02:44 |

The unreviewed code ran until that installation. Its known defects, all corrected by #970:
in #910 a failed jobs request was read as an empty list and the #917 pilot census was capped
at 30 completed runs and unbounded after the merge; in both autopilots a low-traffic row was
accepted on run counts alone. The `owner_excepted()` rows of #892 were read field by field;
the only live row carried all fields, and the patch reads them with defaults.

## 1. Unreviewed edits replaced by this package (provenance only)

The diffs are kept as issue comments, not in this repository, so no defective code is part
of the reviewed tree. Each reproduces its post-image byte for byte from the backed-up
pre-image (`patch -o`).

| Diff (sha256) | Pre-image (installed by) | Post-image | What changed | Record |
|---|---|---|---|---|
| `910_20260926_env_wait.diff` (`602a8554…`) | `7ff576da` (#938) | `51260863` | `measure_env_wait` skips documented runs and PRs whose base is not main (#912) | github#910 comment 5922473236 |
| `910_20260928_pilot.diff` (`5057f961…`) | `8538a0ed` (#943) | `08477d87` | low-traffic grace in `measure_runner_label` (#913); DoD kind `arm64_pilot` and one-time `report_pilot` (#917); `technical_hold` skips any sub | github#910 comment 5922473236 |
| `888_20260928_owner_exception.diff` (`42f71961…`) | `f6fb6794` (#944) | `4b78d530` | owner exception for #892 (keeps `met_at`, late literal comment) | github#888 comment 5922472926 |

Wave-3 installs #941/#942 were applied on top of `51260863`. `08477d87` and `4b78d530` are
the adopted images `install.py` accepts as pre-images.

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
  `f"dod:{sub}"` key, which the patch replaces with `"dod:" + str(sub)`. The installer
  verifies every patcher listed in `OLDER_TOOLS` against protected main, re-runs each in
  memory and aborts if one would rewrite code; any unlisted `tools/gh-cost-*` patcher stops
  it.

`cost_adoption.py` holds the helpers and is installed next to both autopilots.
`patch_autopilot.py` holds exact-string anchors that must match once; a fully patched
source is returned unchanged.

## 3. Install, record, release, roll back

Writes run only when the installer, patch, helpers, decision record and the listed older
patchers equal protected main byte for byte; dry runs skip only this package's own files.
Each write takes the autopilot's `autopilot/lock`, checks OWNER_HOLD, uses freshly read
SHA-256 digests, backs up with a manifest and reads back; `code` leaves
`adoption-receipt.json` next to the autopilot. Verification is the next natural tick; never
run a tick manually.

```bash
python3 tools/gh-cost-autopilot-adoption/install.py code --dry-run \
  --expected-code-888 <sha256> --expected-state-888 <sha256> \
  --expected-code-910 <sha256> --expected-state-910 <sha256>
python3 tools/gh-cost-autopilot-adoption/install.py record-owner-exception --dry-run \
  --expected-state-888 <sha256>
python3 tools/gh-cost-autopilot-adoption/install.py release-hold --dry-run \
  --expected-state-910 <sha256>
python3 tools/gh-cost-autopilot-adoption/install.py rollback --target 888 \
  --backup ~/.merglbot/gh-cost/backups/adoption-<UTC>
```

`record-owner-exception` compares the live decision comment github#895 (5917584272) with
`decisions/895.cs.md`. `release-hold` needs #917 closed as completed and Done on Project 66.
`rollback` restores the adopted image only while both live files are the backup's
after-images.

Refs merglbot-core/github#888, #895, #910, #913, #917
