# CI pilot GitHub adapter

`scripts/ci-pilot/github_client.py` provides the bounded pilot's API adapter.
Importing it performs no requests and enables no selectors or scheduled jobs.
Controller/runtime delivery is separate.

Reads fail on incomplete pagination, changed PR bindings and missing evidence.
Variable pagination uses the API's thirty-item cap. Mutations are restricted
to the two named pilot selectors in three repositories through the existing
guarded wrapper. No raw command output or credential values are persisted.
Measurements include run/attempt/job identity, PR number and immutable run head,
pending timer start, and runner-zero/empty-step cancellation evidence.
PR associations expose current head/base, so historical actual base remains
`DATA_GAP`. Explicit null runner IDs are preserved as unknown and increase
`runner_evidence_gaps`; only ID zero plus empty steps proves runner-free cancellation.
Each bounded attempt uses its own metadata/jobs and start time, including reruns
of runs created before activation.
Empty merged-run PR associations require a unique commit-to-PR association;
ambiguity fails closed. Available branch identity must also match.

Run the committed offline fixtures; no GitHub credentials are needed:

```sh
python3 -m unittest discover -s tests -p test_ci_pilot_github.py
```
