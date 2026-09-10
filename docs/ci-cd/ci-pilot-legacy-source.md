# Legacy paused heartbeat proof

For the audited Codex 26.903.61454 build 8378, legacy scheduler admission is
file-based. `AF` reads automation files; `uI`/`lI` filter ACTIVE **before** `eI`
updates SQLite. Consequently a PAUSED file need not refresh its cached DB row.
Stale DB status, rrule or timestamps do not prove that it is running.
Explicit list/get handlers can synchronize the cache, but are unnecessary for
this stop proof and are not invoked by the adapter.

The optional fallback is fail-closed and restricted to a parsed PAUSED
version-1 heartbeat matching the configured ID/thread. It requires:

- The exact app version/build and both full module hashes pinned in
  `legacy_source.py`; future app changes are unsupported until audited.
- One exact main executable `Contents/MacOS/ChatGPT`, started after the
  audited app artifacts. Process and source identity are rechecked at the end.
- That process holds the same SQLite file (`lsof` plus inode identity), and
  both supported homes `~/.codex` and `~/.codex-o2` share the exact DB and
  target TOML inodes. Proof explicitly reports `known_home_aliases`.
  This contract is limited to this known deployment, not a general inference
  of arbitrary CODEX_HOME from a shared DB. Missing/different alias mappings
  are gaps; custom-home support is outside this bounded pilot.
- A legacy DB identity with no scoped or migrated successor for its ID/thread.
  Scoped account values are never emitted. SQLite is opened read-only.

The file is reread after provenance checks and its hash anchors the returned
proof. A successful fallback reports `database_cache: stale` and
`paused_file_admission: disabled`. This proves **no new scheduler admission**
for this automation. `already_admitted: not_cancelled` is an explicit limit:
the scheduler can already hold a selected heartbeat while awaiting thread
state, so PAUSED does not cancel that execution. Existing admitted work needs
separate evidence; it is never declared stopped by this proof.

ACTIVE still requires normal DB synchronization. Unknown source/runtime,
scope conflicts, malformed or missing data remain pending/unverified.
No app, SQLite, guardrail, LPW or automation is modified by the source verifier.

```sh
/usr/bin/python3 -m unittest discover -s tests -p 'test_ci_pilot*.py'
```
