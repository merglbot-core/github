# #922: refresh billing windows after relevant follow-up merges

The installed #910 autopilot originally calculated its window only when `due_at`
was absent. Eight #921 follow-up merges on 26 September therefore did not move
the old 10 October appointment. This patch revalidates the financial input set
before scheduling, posting or closing. It preserves all rollout and DoD state.

`billing_followups` is a separate financial registry: main merges referencing
`merglbot-core/github#921`, checked live by the installer. It never labels these
PRs `verified` or adds them to the rollout verifier. The billing repository set
includes these follow-ups. Future relevant follow-ups must likewise be registered;
this is not an automatic census of every new GitHub PR.

Windows retain the existing convention: 14 complete UTC days before the first
merge, 14 complete UTC days after the last, and the existing two-day allowance
at 06:00 UTC. Owner-facing appointment text uses the autopilot's Prague formatter.
With the latest observed merge on 26 September at 19:43:21 Prague, the conditional
appointment is 12 October 2026 at 08:00 Prague. New merges may move it again.

Pending/unknown rollout states, incomplete registration, malformed timestamps
and unverified follow-ups block acceptance. A changed published acceptance is
preserved with `scope_drift` and requires reconciliation; it cannot close the EPIC.
Input fingerprints distinguish changed scope even when the extrema stay equal.
Unchanged ticks do not post repeated scheduling comments.

The existing gross billing evaluator and separate #934 net/settled acceptance
contract remain unchanged. This patch does not prove savings, fulfill natural
DoD, change owner exceptions, close #930, create a scheduler, or alter credentials.

## Validation and activation

```bash
~/.merglbot/mem-guard/capped-run --max-gb 2 -- \
  python3 -m unittest discover -s tools/gh-cost-922-window -p 'test_*.py' -v
```

The activation installer is a separate dependent change. Do not edit the running
autopilot or add state records until that protected change has passed review.
