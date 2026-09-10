# Bounded CI delay supervisor

Standard-library Python; no reviews, merges or bot repairs.
Share one durable state directory; preserve distinct-PR case history.

```sh
python3 scripts/ci-pilot/controller.py tick --state-dir /absolute/state
python3 scripts/ci-pilot/controller.py activate --state-dir /absolute/state --receipt /absolute/receipt.json --apply
python3 scripts/ci-pilot/controller.py tick --state-dir /absolute/state --apply
python3 -m unittest discover -s tests -p test_ci_pilot.py
```

Without `--apply`, GitHub is read-only. Local state and `next_action.json` use
atomic writes and a process lock. Mutations use the guarded wrapper, restricted
to CI_DELAY_PILOT_PR/SHA on three repositories. Install SHA then PR; remove PR
then SHA. Incomplete restarts, holds, changed/missing evidence and expiry trigger
cleanup/readback. Failed readback remains unverified. OWNER_HOLD in the state
directory, global preauth OWNER_HOLD, or a PR hold label stops the pilot.
Limit: one active PR, five distinct PRs with observed delay,
2026-09-14T19:03:19Z, and 24 hours without observed delay.

An agent must review the entire exact diff: ordinary application code/unit tests
and companion docs only; exclude Terraform, IAM, auth, secrets, workflows,
deployment, dependencies and persistent data schemas. Suffixes prove nothing.
This semantic review is authorized by the owner; fresh per-head permission is
not required. Receipt: repo, integer pr, head/base SHAs, sorted unique paths,
eligible=true, assessment, diff_sha256 of full GitHub diff, workflow_sha256 of
base workflow text, protection_sha256 of Python json.dumps({"protection": branch
protection, "rules": effective branch rules}, sort_keys=True). Verify the paired
selector actually controls delay, not merely that its text exists.

Ticks recheck bindings, protection, required checks, environment timer/policies
and empty variable/secret/custom-app lists. Aggregate new target-workflow PR
runs: completed runner seconds, current pending waits, runner_id=0/steps=[]
cancellations. Samples may miss waits and do not prove savings. New receipts need an agent;
ticks do not. Persist no diff, transcripts or raw logs.
