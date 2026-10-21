# Financial acceptance observer source

Refs merglbot-core/github#934 and merglbot-core/platform#1628.

This is source preparation for the existing financial acceptance observer. It
does not create an observer, install files, register launchd, run a model, collect
billing or establish financial acceptance. The existing phase dates, two attempts
per phase, 24-hour retry interval, objective prompt and financial proof predicate
are preserved. The historical first phase remains in the source; adopting this
code must preserve the existing state rather than repeat completed work.

The proposed caller must wrap the entire observer process tree in the existing
`capped-run` with a 2 GB limit and a 600-second timeout. The source independently
subtracts elapsed preparation time and finalization headroom from its child
budget, including a ten-second internal finalization reserve. If the budget
expires before launch, it records a terminal halt without
starting the child. Memory/time guard exits and child-start errors prohibit an
automatic retry. A persisted `running` phase found by the next exclusive lock
holder also halts; an existing halt reason and consumed attempts are preserved.
The journal flushes its temporary file, replaces the state and flushes the parent
directory before launching a child. A synchronization failure refuses the child;
an already replaced `running` state halts on the next lock holder. Tests cover
the synchronization order and injected errors, not a physical power-loss drill.

The caller must supply an absolute `--objective` path to the same admitted private
objective file. No private objective locator or contents are published in this
directory. A missing argument or missing file fails before an attempt is
consumed. The observer passes the locator to its existing child prompt; the
observer itself does not read objective contents. Permission to publish source
does not admit a profile, its automatically loaded context or a new model run.

Run the offline tests from the repository root:

```bash
"$HOME/.merglbot/mem-guard/capped-run" --max-gb 2 --timeout 600 -- python3 -m unittest discover -s tools/gh-cost-934-observer -p 'test_*.py'
```

Tests use temporary synthetic files and mocked subprocesses. They do not execute
the installed observer, provider, model, launchd or GitHub API. Existing date-gate
and financial acceptance tests are retained alongside the new budget and journal
regressions.

`tests/test_financial934_observer.py` includes the same suites in the existing
repository `unittest discover -s tests` CI command. It loads these exact test
modules; there is no copied implementation or new workflow.

Protected adoption remains separate: reviewed source delivery, exact installed
preimages, current ownership, an inactive-stage preflight, preserved counters and
STOP history, an admitted install/rollback and the eligible shared owner wave.
No installer, launchd file, workflow or activation is part of this source change.
An exit-zero phase is not financial proof; only the existing `PROVEN` audit with
a nonempty evidence list can set completion.
