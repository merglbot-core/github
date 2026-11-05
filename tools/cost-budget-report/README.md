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
