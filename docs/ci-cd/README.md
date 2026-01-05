# CI/CD Documentation (Local Entry Point)

Tento repozitář (`merglbot-core/github`) obsahuje **reusable GitHub Actions workflows**, policy checks a release templates.

## SSOT (Canonical)

CI/CD standardy a governance jsou SSOT v `merglbot-public/docs`:

- [MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md)
- [MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md)
- [PR_POLICY.md](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md)
- [MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md)

## Proč tento soubor existuje

- Aby interní odkazy v tomto repu zůstaly funkční (training/release-management).
- Abychom měli *lokální rozcestník* bez duplikace pravidel.
