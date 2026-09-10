# Optional existing semantic heartbeat

The runtime can maintain cadence and stop **one existing** Codex heartbeat.
This code creates/enables no automation and installs no launchd job. Without
`heartbeat.json` in the pilot state directory, standalone runtime behavior is
unchanged. Configure only the intended existing automation and thread:

```json
{"id":"existing-automation-id","target_thread_id":"existing-thread-id"}
```

The sole writable automation is
`~/.codex/automations/<id>/automation.toml`; traversal and symlinks are rejected.
The adapter accepts only the normalized flat scalar schema: version, id, kind,
name, prompt, status, rrule, target_thread_id, created_at, updated_at. Unknown
fields, comments, multiline TOML and malformed input fail closed. It preserves
all unrelated lines, including the prompt; neither prompts nor raw payloads
are emitted. File changes are atomic and strictly advance updated_at.

Cadence is five minutes while active or after an error, and fifteen when idle.
PAUSED in either file or app database stays PAUSED; the adapter never enables
a heartbeat. Deadline/case-limit completion requests PAUSED. Existing saved
state and pilot selector cleanup remain governed by the controller.

Verification opens `~/.codex/sqlite/codex-dev.db` using SQLite `mode=ro` and
query-only mode. The exact id, thread, status and rrule must agree with the
file; terminal verification also requires next_run_at=NULL. The database is
never modified. Successful identity-validated reads awaiting only app sync
are `pending`, not verified: keep the desired file cadence (including idle15),
retry the runtime every five minutes, and do not unload. Repeated pending
reads do not rewrite the file or advance updated_at. This allows idle15 to
settle asynchronously. Actual read/parse/identity errors are `unverified`;
retry the same validated adapter with the five-minute target, never bypassing
its identity checks or enabling a paused heartbeat. Both statuses report
heartbeat_sync_required and keep runtime retries at five minutes. Only proven selector cleanup **and**
proven heartbeat stop allow terminal unload of the runtime's own launchd label.

After configuring a target, inspect `runtime.json` heartbeat proof and the
sanitized `next_action.json`. If parsing or identity verification fails,
correct only the intended target/configuration; do not reset pilot history or
edit SQLite. A separate semantic-agent invocation mechanism remains outside
this adapter: changing cadence does not claim that the app executed a task.

Python 3.9-compatible offline tests use temporary TOML/SQLite fixtures:

```sh
/usr/bin/python3 -m unittest discover -s tests -p 'test_ci_pilot*.py'
```
