# CI pilot GitHub adapter

`scripts/ci-pilot/github_client.py` provides the bounded pilot's API adapter.
Importing it performs no requests and enables no selectors or scheduled jobs.
Controller/runtime delivery is separate.

Reads fail on incomplete pagination, changed PR bindings and missing evidence.
Variable pagination uses the API's thirty-item cap. Mutations are restricted
to the two named pilot selectors in three repositories through the existing
guarded wrapper. No raw command output or credential values are persisted.
Measurements include run/attempt/job identity, exact PR head/base binding,
pending timer start, and runner-zero/empty-step cancellation evidence.

Run the committed offline fixtures; no GitHub credentials are needed:

```sh
python3 -m unittest discover -s tests -p test_ci_pilot_github.py
```
