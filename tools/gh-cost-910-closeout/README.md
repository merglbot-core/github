# #910 local closeout guard

The #910 autopilot previously trusted historical `board_done_at` marks and
unchecked issue/Project writes. This package checks the live issue state and
the Project mutation before persisting a closeout. It also records the
documented plane_so #35 economic exception when #914's actual DoD eventually
matures, without counting the excluded repository as implemented savings.

The installer atomically changes code and migrates only `subs["921"]`:
`board_done_at` becomes null and `technical_hold` becomes true. The hold is
intentional: the current-main audit found 39 still-unbounded jobs, and their
workflow authority PRs must merge before #921 can be accepted. A separate
operator must verify each current-main job or a documented safety exception,
then clear the hold under the same existing autopilot lock. Neither a green
test nor an owner merge alone clears the hold.

Install only after a substantive current-head V6 approval and protected merge.
Supply freshly read SHA-256 digests of the live code and state to `install.py`
with `--expected-code` and `--expected-state`; run `--dry-run` first. The
installer checks OWNER_HOLD, takes the existing lock, backs up code and state,
and refuses source or state drift. `--rollback` refuses a newer natural tick
and refuses to restore unsafe legacy closeout while #921's technical hold is
active. Keep the backup for a reviewed recovery patch; do not manually restore
its old `board_done_at` marker. No job, workflow, credential or fleet process
is restarted.

For #914, `plane_so` is a documented low-volume economic exception. Its
current weekly Monday 02:30 UTC CodeQL schedule and main/release push signal
must both remain; the retained push is not counted as a saving. EPIC closeout
reads every page of the native sub-issue collection (including #930) and each
child Project 66 Status. Historical local Done markers cannot replace them.

Verify an ordinary launchd tick, the live open #921 issue, Project 66 In
Progress, the scheduled #922 billing window, and unchanged unrelated DoD.
The #934 settled net acceptance remains separate from #922's gross model.

Refs merglbot-core/github#910
Refs merglbot-core/github#914
Refs merglbot-core/github#921
Refs merglbot-core/github#922
