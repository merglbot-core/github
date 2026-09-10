# Bounded CI pilot runtime

Requires the reviewed controller beside `runtime.py`. One user launchd job replaces the paused conversational monitor; do not install duplicate supervisors.

Run the fixtures: `python3 -m unittest discover -s tests -p 'test_ci_pilot*.py'`.

Use an immutable merged checkout and a durable private state directory. Seed `counted_prs` with previous actual pilot cases, including unsuccessful cases. Never reset the experiment count. Generate a plist with `python3 scripts/ci-pilot/runtime.py plist --state-dir <absolute-state-dir>`; validate it, install as `~/Library/LaunchAgents/ai.merglbot.ci-pilot-supervisor.plist`, then bootstrap the user domain. Keep selectors absent until main workflow support and the semantic receipt are verified.

Launchd wakes every five minutes; inactive reads run every fifteen. The fixed deadline is 2026-09-14T19:03:19Z. Timeout terminates this controller process group, then independently attempts guarded cleanup/readback under the controller lock. Read failure remains unverified. After deadline/case-limit cleanup is verified, runtime records its terminal state and unloads only its own label. Verify launchd absence.

`next_action.json` supplies the agent's current task; `runtime.json` records cadence/stop state. Missing evidence or unfinished admitted runs remains DATA_GAP. Sleep, offline hosts and API outages can delay cleanup; no wall-clock guarantee is claimed. 
