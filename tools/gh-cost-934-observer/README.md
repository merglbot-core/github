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

Dry-run checks the presence of the objective, handoff, executable and guard before
reading state. A missing input returns exit 2 with `will_launch_codex: false` and
`input_preflight_error: MISSING_REQUIRED_LOCAL_INPUT`; it never creates a lock or
changes state, STOP reasons or attempt counters. This is a readiness snapshot,
not permission to launch. The real lock holder rechecks inputs before consuming
an attempt. Neither presence check reads or admits private file contents.

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

## Protected adoption adapter (D-P-3963)

The adapter prepares and validates a canonical two-file plan for this existing
observer and its caller. It pins the observer source from merge `cc77f82c`, the
admitted objective digest, full native preimages and current state. All caller
fields except the bounded ProgramArguments transition remain unchanged.

`adoption_owner.py` runs with isolated Python. Its default `preflight` mode is
read-only; `install` and `rollback` require `POTVRZUJI` plus the exact admitted
plan digest. These are process controls, not identity authentication. Use only
through the existing owner-script emitter, after independent exact-head source
review, admission and owner approval of the concrete manifest. No generated
owner script or production manifest is included in this PR.

Native replacement and rollback reuse the pinned docs transaction/copy-admission
library from `9ac0ba9c`; its complete dependency bytes are verified before import.
The adapter does not replace metadata rules or permit generated xattrs. It uses
the observer's existing flock inode. The owner path stops the exact idle service,
installs both files, and reloads under that lock. RunAtLoad must complete exactly
once with exit zero while the lock prevents state writes/model launches. No PID
alone is not proof. Unknown, drift, partial install or failed verification leaves
the service stopped for explicit recovery; it is never silently accepted.

Every install digest reserves an exclusive directory with a bounded durable
hash-chain journal. Recovery requires a fresh recovery plan binding the original
plan, exact journal bytes, native receipt and CURRENT state. It restores files
only, not state/STOP/counters. Incomplete or ambiguous backup ownership fails
closed for manual disposition. Neither successful installation nor recovery
establishes a natural-run result or financial acceptance.

Validation includes the repository CI entrypoint, synthetic owner CLI round trip
(preflight/install/recovery with simulated launchd), and a macOS native
transaction/rollback integration test using the unmodified pinned dependency
bytes vendored under `fixtures/native-9ac0ba9c`. It loads those bytes through
`adoption_bundle.load`, drives the owner install and receipt-bound recovery on
temporary files, and simulates only launchd. The portable loader test also runs
on Linux; the native filesystem test explicitly skips there. The synthetic integration
does not prove real launchd activation. Real activation/readback remains a
separate owner step; do not execute this source against the host during review.

A newly bootstrapped job may report `(never exited)`; this is pending observation,
not success or failure. Completion still requires one run with numeric exit zero.
Both installation and recovery acquire the existing observer lock before checking
the loaded service and stopping it. Recovery accepts only an absent service or the
admitted current program/arguments in idle state without a PID; another loaded
identity or a busy observer is refused before bootout. The stop operation itself
re-reads the loaded service and checks its exact phase-specific arguments and idle
state before issuing bootout. This is a fresh admission snapshot, not an atomic
launchd compare-and-stop primitive; installation must still exclude other writers.

Binding reservation occurs only after lock acquisition, invariant checks and
old-service idle verification, immediately before `stop_intent`. Recovery likewise
reserves its packet only on `recovery_stop_intent`, after the recovery lock, guard,
idle/absence and state checks. Ordinary lock
contention therefore creates no reservation, and the unchanged plan can proceed
after contention ends. Once reserved, a binding remains consumed even if an
I/O failure occurs before the transaction receipt exists.
It is not safe to delete that directory or replay the same binding; preserve its
journal and obtain a fresh, explicitly reviewed recovery/disposition packet.

## Original caller compatibility

Adoption preflight and recovery admission now require the exact inspected legacy
observer SHA256 `ebb204f546915a39c1238ffa14381a630370cb3808eb814cc0ed723ea23fbf68`
paired with its original two-element Python/program argv. A different original
generation is refused with `previous_program_cli_and_contention_contract_unverified`
before any service mutation; it needs a separately reviewed compatibility contract.
In particular, the newer observer requiring `--objective` is not admitted with
the old no-argument caller. Recovery checks the actual preserved backup bytes,
not merely the current source or the manifest's assertion.

The exact legacy bytes were tested against temporary state/audit/lock paths and
a real held flock: no-argument parsing succeeded, exit was zero, state was unchanged
and no subprocess was invoked. This is compatibility evidence, not production
launchd evidence. The synthetic CLI glue fixture remains explicitly synthetic;
its successful launchd simulation cannot establish legacy compatibility. The
offline suite includes rejection of the actual new required-objective
source as a previous no-argument program and rejection of either byte or argv drift.
