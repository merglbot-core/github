# PR Assistant Trigger Visibility And Owner Alignment

The PR Assistant v3 dispatcher emits a machine-readable skip receipt when an
`@merglbot review` comment reaches the workflow but does not match the v3
command contract. Invalid flags such as unsupported rerun aliases are reported
with `skip_reason=invalid_v3_review_trigger` and the step summary includes a
single-line JSON payload using schema
`merglbot.pr_assistant.v3.trigger_skip.v1`.

Enterprise rollout audit also checks that branch-protection required PR
Assistant checks match the active review owner. If
`MERGLBOT_PR_ASSISTANT_V3_DISABLED=true` selects v4 ownership, a protected branch
must not keep requiring `Merglbot PR Assistant v3`; the audit emits
`branch_protection_review_owner_mismatch` for that drift. Repositories without a
required PR Assistant check are treated as `no_review_check_required`, not as an
owner mismatch.
