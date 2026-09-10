# Local periodic runtime

Prerequisites: merged/reviewed controller and runtime, a stable checkout path,
authenticated `gh`, guarded variable-write authority, durable seeded state,
and the old model heartbeat verified PAUSED with no next run. No LPW job is
changed. The label is exclusively `ai.merglbot.ci-pilot-supervisor` in the
current user's GUI domain. Never reset `counted_prs`, history or measurements.

Generate a plist without installing anything:

```sh
python3 /absolute/reviewed-checkout/scripts/ci-pilot/runtime.py plist --state-dir /absolute/pilot-state
```

After prerequisite verification, save that output as
`~/Library/LaunchAgents/ai.merglbot.ci-pilot-supervisor.plist`, validate it with
`plutil -lint`, and bootstrap that file with `launchctl bootstrap gui/$(id -u)`.
Verify the exact job using `launchctl print gui/$(id -u)/ai.merglbot.ci-pilot-supervisor`
and inspect state/next_action timestamps after its first run. This documentation
does not install or start the job. Installation is a separate delivery action.

launchd wakes one short process every five minutes; it skips idle GitHub polls
until fifteen minutes have elapsed. Active or unverified state polls every five
minutes. Deadline/count checks override idle skipping. No LLM is involved.
The runtime lock prevents overlaps, subprocess timeout is four minutes, and
only sanitized results are stored. A timed-out controller process group is
terminated before a separate bounded cleanup-only subprocess attempts selector
removal/readback. Missing cleanup proof remains unverified and is retried at
the active cadence. Missing historical evidence always remains DATA_GAP.

After deadline or case limit, stop only after selector cleanup is verified by
readback. The wrapper persists a terminal marker then unloads **only its own**
launchd job; a failed unload is retried on the next wakeup. Unfinished admitted
runs or unavailable historical evidence are explicit `DATA_GAP` in runtime.json.
They are not declared completed just because selector cleanup succeeded.
Review their retained run/job evidence separately after the scheduler stops.

Measurements retain distinct wait/job snapshots (first/last observation times),
job names, runner IDs and steps counts across completion and head cleanup.
Current aggregates remain separate from cumulative evidence. Fixture tests:

```sh
python3 -m unittest discover -s tests -p 'test_ci_pilot*.py'
```
