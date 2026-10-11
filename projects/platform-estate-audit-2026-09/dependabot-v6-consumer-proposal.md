# Dependabot V6 consumer migration proposal

Status: proposed source change only. No running worker, gate, workflow, branch protection or credential configuration is changed by preparing this branch. Activation requires the separately authorized review-machinery decision; this proposal must not be autonomously merged as ordinary application code.

The current closeout helper defaults to the historical receipt verifier, permits a local docs-authority exception and reads required checks before waiting for review. The proposed consumer selects the canonical V6 surface, keeps technical review distinct from merge authority, removes the local exception, and replays required checks and content-validated dependency-pin docs scope on the final head.

A BEHIND branch is updated only after review approval and green checks, and only when freshly read strict protection requires it. Updated-head checks and auto-fired review are observed within a bounded diagnosis window before the final gate replay. Waiting for an already-running review does not require retrigger eligibility. A trusted pending rerun supersedes the previous receipt's failed or authority-held outcome in both waits; transport, publisher, binding, schema and failed required-check errors still stop immediately. Review/update polling is bounded to five to ten minutes. Without a pending rerun, authority holds remain terminal. Trusted comment credentials alone do not permit retriggering: fresh repo/PR/head-bound purpose-built status must allow it.

The candidate verifier follows canonical V6 section 5a: select the newest completed trusted receipt on the exact head by completion time and ID, then validate its PR binding. A newer sibling receipt fails binding; it never causes fallback to an older approval. Any pending trusted run blocks approval, and any untrusted same-name publisher fails closed.

A nonzero verifier exit always clears approval, even when its JSON claims success; existing failure blockers remain intact. After strict-sync gate completion, the consumer refreshes PR merge state and verifies the updated head again before final checks and the exact-head merge. A concurrent head change requires review rebind, never use of the prior approval.

Head changes detected inside the updated-head wait re-enter the outer review loop. Any trusted pending run yields WAIT before selecting a completed receipt. After it finishes, newest-completed selection and PR binding apply without fallback; publisher errors always fail closed.

The current weekly caller does not supply that status. Its normal open/synchronize and keeper producers remain the review sources; explicit manual retriggers fail closed until a legitimate status consumer is wired. This proposal does not claim that a new continuation is running. Provider-recovery exceptions still need their canonical integration evidence. Changed-head convergence is iterative and no longer capped by the legacy review-count parameter; an unchanged head or the thirty-minute job diagnosis deadline produces an explicit continuation requirement, never approval. The fix-loop metadata remains a separate compatibility surface and does not authorize launching retired saved flows.

Dependency landing order: substantive current-head V6 and required checks first; separate activation authorization second; protected merge and natural-run runtime acceptance only afterward. No admin bypass or retrospective approval is implied. The workflow source was not changed. The observed same-repository local caller binds checkout correctly; frozen inventory does not prove global current caller coverage.

Validation: offline consumer integration exercises clean merges, stale heads/checks, docs replay, authority holds, sync ordering, cadence and retrigger eligibility. The canonical verifier and existing consumer self-tests are also required. Repository suite results and exact source hashes are retained in the audit evidence; passing tests alone do not prove rollout or runtime acceptance.

Canonical references: [PR policy](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md), [V6](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_PR_ASSISTANT_V6.md), [docs obligations](https://github.com/merglbot-public/docs/blob/main/DOCUMENTATION_OBLIGATION_METHODOLOGY.md), [honest wait](https://github.com/merglbot-public/docs/blob/main/claude-code/rules/honest-wait.md).

## Receipt completion semantics

`ok` reports successful scan execution; it is not an assertion that every PR
was delivered. `final_verdict` is `..._PARTIAL` when apply leaves blocked or
allowlist-excluded PRs, `..._DRY_RUN` for a successful dry run, and
`..._BLOCKED` when repository-level execution warnings exist. Only an apply
with no remaining work reports `..._COMPLETE`. The distinction changes
reporting only, not merge authority, review requirements or workflow exit policy.

This successor includes the unmerged github#953 proposal and its reporting
repair on the current main. It requires its own exact-head review and legitimate
review-machinery activation authority; it does not depend on merging the stale
original draft first. Neither the preserved draft approval nor local tests prove
that this successor is running.
