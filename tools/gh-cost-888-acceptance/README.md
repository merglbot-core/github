# EPIC #888 literal DoD repair

The existing #888 autopilot closed #892 with a proxy count despite only one
qualifying natural Terraform workflow run. This patch counts successful first
attempts in the actual path-filtered `terraform-validate.yml` after its merge,
checks full run and PR pagination, and reopens #892 until its literal five-run
criterion is met. It never dispatches an artificial run.

The acquisition-analysis wrapper PR #184 is closed unmerged. The #889 economic
exception is allowed only if the PR remains unmerged and current main branch
protection still requires gitleaks, dependency review and V6. The exception
does not create a `met_at` mark or a claimed saving. Other repo DoD remains.

Install only after exact-current-head substantive V6 approval and merge.
`install.py --expected-sha SHA --dry-run` validates the live code; without
`--dry-run` it installs under the existing autopilot lock with code/state
backup. `install.py --rollback DIR` restores code only if both installed files
still match the recorded hashes. It deliberately leaves `state.json` and any
measured `literal_verified`, `qualified_runs` or `met_at` values intact; a
rollback requires separate state revalidation. OWNER_HOLD blocks either mutation.

Verify the next natural tick, issue and Project status, current main, API
pagination and unchanged state fields outside the scoped DoD. Leave billing
and the other natural-run counts open until their own criteria mature.

Refs merglbot-core/github#889
Refs merglbot-core/github#892
Refs merglbot-core/github#894
