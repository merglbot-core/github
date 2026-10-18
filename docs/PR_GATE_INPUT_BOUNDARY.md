# PR Gate branch-prefix input boundary

The reusable PR Gate accepts a free-form `allowed-branch-prefixes` string. Pass it
through the branch-check step's environment and read `process.env` in JavaScript.
Do not substitute the workflow expression into executable JavaScript source.
This keeps punctuation in a caller-supplied prefix as data rather than syntax.

The branch check preserves the existing comma-separated list, whitespace trimming,
default prefixes and warning-only result. An empty prefix still matches any branch;
changing that established behaviour would be a separate policy change. Job
permissions, action pins, triggers and required check names are unchanged.

`tests/test_pr_gate_branch_input.py` extracts the actual branch-check script and
environment binding from the workflow and executes the script with isolated data.
It uses Python's standard library, so it needs no pip install before the repository
contract test step. It checks ordinary, trimmed,
unmatched, empty and punctuation-bearing prefixes. It does not run Actions, make
GitHub calls or demonstrate an exploit against a live caller.

Tracking: [github#961](https://github.com/merglbot-core/github/issues/961).
The protected workflow change requires its own current-head review and legitimate
owner merge; local tests are not evidence that callers use the repair.

## Source delivery order

This minimal authority source and the test-only review source are separate
DRAFT PRs under the same D104 intent and shared sublimit. The test PR targets
this feature branch because its assertions require the repaired workflow.
Neither feature branch is a landed dependency or authorised runtime change.
Both PRs remain DRAFT: merge, workflow activation and downstream caller
verification require their separate legitimate stage. Preserve the original
workflow and test postimages; do not weaken the tests to pass the old workflow.
