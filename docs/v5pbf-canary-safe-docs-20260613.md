# V5PBF Safe Docs Canary

This temporary file is a low-risk documentation-only canary for validating the
PR Assistant v5 receipt contract after the provider-budget fallback rollout.

Expected result:

- The Merglbot PR Assistant v5 check is parseable and current-head.
- The review remains provider healthy.
- The terminal state is `AUTONOMOUS_MERGE_SAFE`.
- No bounded fallback marker is needed for this small docs-only change.

Cleanup:

- Close the pull request after evidence is captured.
- Delete the canary branch.
