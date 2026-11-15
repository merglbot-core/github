# Shared budget observation renderer

Refs merglbot-core/github#982. Source preparation only; not installed.

`budget_report.py` renders the same Markdown section for the existing monthly
report and autonomy digest. It has no network, subprocess, scheduler, model or
budget mutation capability. The existing read path must supply a snapshot and
its provenance, preserving the original observation time. Rendering is not a
fresh observation. Do not rerun a collector merely to render another consumer.

Snapshot shape matches the existing cost-program read evidence:
`{"rows": [{"id", "budget_scope", "product", "budget_amount", "consumed_amount",
"prevent_further_usage", "alert_enabled"?}]}`.

Provenance version 1 requires `schema_version`, `source_sha256` (hash of exact
snapshot bytes), `source_endpoint`, `observed_at` (timezone included), and
`complete: true` only after the producer has verified its scope and pagination.
`period` is either null (unknown) or an explicit `{start, end}` interval from
the source. Do not derive a billing period from the observation calendar.
`alert_enabled` must be separately evidenced; a soft budget alone does not
prove alerts enabled. Missing alert evidence stays DATA_GAP.

Consumers call `render(raw, provenance, now=..., max_age=...)` or the CLI with
`--snapshot`, `--provenance`, `--max-age-hours`. A missing, stale, future,
incomplete or hash-mismatched input emits DATA_GAP rather than stale numbers.
Keep the last successful source observation separately; do not relabel it as
current data. No financial savings acceptance is inferred from this section.

`snapshot_adapter.normalize` consumes the existing reader's captured listing
pages and detail responses (`request_url`, `response`, timezone-aware `observed_at`, integer
`exit_code`). It performs no API calls. It checks terminal pagination, stable
counts, unique IDs, complete detail coverage and consistent configuration.
Captures from different observations are refused; the default maximum spread
is five minutes. The oldest observation time is retained, not refreshed to
the rendering time. Original captures remain the source of record; their
canonical JSON hashes bind provenance without copying alert recipient names.
This adapter must be invoked by the existing reader; it is not a new collector.
The captured request URL must bind every listing page and detail to the declared
API endpoint; foreign scopes, wrong pages and detail IDs are rejected. The reader
must record the URL actually fetched, never retrofit it from the declared scope.
Malformed captures raise ValueError. The monthly consumer uses render_paragraphs
to pass plain text to its escaping HTML/PDF backend, rather than Markdown tables.

## Remaining delivery

The installed autonomy digest has no located versioned source (D-P-6194).
Its read-only snapshot is independently pinned under D-P-6210. This helper
does not establish integration or authorize replacing that script. Both real
consumer entrypoints, their provenance producer and the authorized installation
path still need wiring and acceptance. `integration/*.patch` are hash-bound
source candidates against the observed consumer files, not an installer and
not permission to modify them. Tests execute only the newly added blocks,
never the digest's original receipt cleanup or notifications.
No launchd or running ledger script is
modified by this source preparation. Four existing owner hard stops remain
unchanged under D-P-5243; no duplicate Copilot budget is created.

Run the focused deterministic tests with:
`python3 -m unittest discover -s tools/cost-budget-report -v`.

## Installation packet adapter (D-P-6463)

`install_adapter.py --packet <new-directory>` reads the three fixed consumer/renderer
paths and creates an exclusive, private packet with exact candidates, original
backup bytes, metadata/xattr hashes and a content binding. It never executes
consumers or writes host targets. `--verify` re-derives the packet from the current
reviewed source and current preimages; source, content, metadata, backup or candidate
drift is refused. Symlink inputs are refused. The native macOS xattr reader is
used where Python lacks that API.

This is preparation for the owner installation transport, not an installer
executor or an admission decision. No `apply` operation is exposed. The protected
installation must separately preserve metadata/xattrs, take durable backups before
replacement, refuse intervening changes, and roll back only matching postimages.
Packet creation and tests do not establish production integration. No scheduler,
collector, timer, consumer cleanup or notification is run.

The packet snapshot records ctime and native macOS flags and refuses present or
unreadable native ACLs. Native ACL absence is checked on the open file descriptor
before and after reading. Non-macOS test snapshots are explicitly marked as such
and cannot substitute for native installation admission. Rollback of a newly
created renderer must preserve it in an archive after verifying its postimage.

## GET reader (source-only, D-P-8812)

`budget_reader.capture(endpoint, get=..., remaining_requests=...)` captures bounded
listing and detail GETs, then calls the existing normalizer. An existing authorized
caller must provide fresh shared-quota evidence and the transport; `gh_get` uses
the guarded `gh api --method GET` transport and existing authentication. It never
acquires credentials, retries, writes files, starts a service or changes budgets.
The whole observation reserves at most 100 requests and leaves 2000 requests.
Insufficient capacity or partial/changed pagination fails before publication.

Returned source captures may contain recipient identities and must stay private;
only normalized bytes and provenance are suitable for report consumers. API
listing product arrays and detail product strings are compared as product sets.
Missing consumption is refused, never replaced with zero. API schema reference:
https://docs.github.com/en/enterprise-cloud@latest/rest/billing/budgets

This source does not wire a producer into a timer or publish `latest.json`.
Runtime invocation/publication is a separately authorized delivery obligation.

For historical install-patch tests after host installation, set
`COST_BUDGET_TEST_BASELINES` to a directory containing `digest` and `monthly`
archived preimages. The original baseline, patch and candidate SHA checks remain
mandatory; installed targets and archived backups must not be rewritten for tests.

## Recurring refresh candidate (D-P-8988, source only)

`refresh_cli.py` is disabled unless explicitly enabled by a separately admitted
caller. It does not create a timer or service. The existing digest is the proposed
caller; `integration/refresh-v2/` contains exact hash-bound deltas from installed
consumer source snapshots. Historical installation manifests are unchanged.
The financial acceptance observer's finite stages and consumed attempts are not
repurposed for recurring collection.

The candidate uses the existing bounded `budget_reader.capture` and normalizer.
It reserves a durable attempt before its first GET, counts quota plus budget GETs
inside a 100-request ceiling, and retains the shared reserve of 2000. Transport
bounds each response to 1 MiB and each child to at most 30 seconds; the transaction
also enforces a total response bound and a 120-second soft deadline. The proposed
caller wraps the entire invocation in existing capped-run at 2 GiB / 150 seconds
with orphan cleanup. No claim of physical-memory containment is made for an
unwrapped direct Python call.

`pair_io` reads both files through one open directory descriptor. `pair_stage`
creates a private fsynced immutable generation. `pair_exchange` uses native Darwin
RENAME_SWAP with no sequential fallback. The displaced generation is retained
unchanged under `.budget-generation-<attempt>` for readers and recovery. The
append-only locked journal binds prior/new byte hashes and private capture evidence.
Any unresolved attempt, partial journal, drift or ambiguous exchange stays held;
there is no automatic retry, rollback, cleanup or attempt reset. Only verified
publication permits a subsequent attempt. Private captures contain source values
and remain mode 0600; the consumers receive only the normalized pair.

Existing valid inputs are reused without GET. The refresh threshold is explicit
and has no activation default. Runtime approval must choose a threshold that keeps
the 24-hour consumer freshness contract valid between daily and monthly reads;
source tests use synthetic thresholds, not a new production cadence. The patch is
OFF unless MERGLBOT_BUDGET_REFRESH_ENABLED=1 and an explicit threshold is configured.
Unknown periods, old observation timestamps and incomplete financial acceptance
are never rewritten as current or successful.

Before runtime delivery: verify all source/admission pins, current consumer
preimages, helper installation layout, the separate journal directory and exclusion
namespace, explicit threshold, capped-run version and exact caller command.
Prepare an owner installation manifest with backup and conditional rollback;
never execute a consumed install attempt. Native metadata/ACL admission and source
review remain installer responsibilities, not substitutes provided by this module.
A data recovery decision must inspect both retained generations and journal state
before any archive or rollback; no blanket replay permission is implied.

Validation covers fake GETs, no-GET reuse/disabled mode, quota refusal, timeout,
output exhaustion, invalid state transitions, drift, partial journal, interrupted
post-exchange execution, native exchange of temporary directories, retained-reader
behavior, and both renderer forms. Tests never execute the original digest cleanup
or notification, the live monthly report, or the real GET transport.
