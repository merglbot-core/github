# Release Process Overview

## Cíl

Jednotný, auditovatelný release proces pro Merglbot služby.

## High-level flow

1. Feature branch + PR
2. Checks + review (případně `@merglbot review`)
3. Merge (squash) do `main`
4. CI/CD deploy (digest-based images)
5. Post-deploy verifikace + rollback plán

## Šablony

- `templates/release/RELEASE_NOTES.md`
- `templates/release/POST_RELEASE_REPORT.md`

## Reference (SSOT)

- `merglbot-public/docs/PR_POLICY.md`
- `merglbot-public/docs/MERGLBOT_PRODUCTION_STATE_*.md`
- `merglbot-public/docs/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md`
