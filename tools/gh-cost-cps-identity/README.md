# Workflow-run identity for the FinOps audit

Program: [github#934](https://github.com/merglbot-core/github/issues/934),
under [EPIC github#931](https://github.com/merglbot-core/github/issues/931).

`snapshot_identity_error(snapshot, expected_repo)` is a pure, read-only validator.
It checks that a collected repository snapshot belongs to the inventory entry
and that each workflow-run ID is a distinct positive integer. A rerun keeps the
same run ID; two attempts must not become two created workflow runs.

The audit's actual `cost_per_success.py` previously accepted a duplicated success:
two distinct successes with synthetic spend 2 became three successes and cost
0.666667 instead of 1. It also accepted a snapshot labelled as another repository.
The original CLI counterexample is retained in the program evidence as
`cost_per_success_identity_counterexample_20260927.json`.

Consumers must classify a returned error as DATA_GAP, leave the exact metric and
conditional bounds unavailable, and preserve the input. Do not silently deduplicate
records, select a rerun, or turn corrupt evidence into an empty census. Valid empty
input only passes identity validation; it does not prove a positive success count,
coverage, pagination, final outcomes, billing settlement, or financial acceptance.

Run the regression suite from the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_gh_cost_run_identity.py' -v
```

The regular repository unittest-discovery CI step already collects these tests.
No workflow, credentials, authority, required checks, gate or autopilot state change
is needed. Local audit-tool integration and actual-dataset validation remain
separate from publishing this source helper.
