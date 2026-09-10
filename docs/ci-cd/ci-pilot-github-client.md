# CI pilot GitHub adapter

No import-time actions. Run offline fixtures:

```sh
python3 -m unittest discover -s tests -p test_ci_pilot_github.py
```

Workflow support requires an exact full-file SHA256 allowlist match. Comments,
unrelated scalars and any future edits fail closed pending new review. Audited
source commits (workflow paths in WORKFLOWS; dual V6 and actionlint verified):

- exporter: 5f2c6f67a73cf7fc4d6fa7da23ab426172ba9d8f
- infra: f396ce798b96c9e353fe7db856b28a4e86825f05
- fb-viz: b1489d531c5fb5d3a9b0c956ed290c244fd35a09

Per-attempt jobs/start times include natural reruns. Mutable PR associations do
not prove historical base: DATA_GAP. Empty associations require a unique commit
association. Null runner IDs remain gaps; only ID zero plus empty steps proves
runner-free cancellation. Selector writes remain scoped through the guard.
