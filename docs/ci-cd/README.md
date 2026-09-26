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

### Cloud Run platform manifest attestations

`reusable-build-attest.yml` builds and checks linux/amd64. `image_digest` retains
the bare root/index digest for existing callers. `platform_digest` is the bare
manifest digest selected by the successful local Trivy/config-parity check.
The build provenance includes both subjects, and the attestation job signs each
distinct digest. A child signing failure fails the job. Cloud Run callers use
`platform_digest` and `platform_attestation_id` together, with the unchanged
commit/provenance and enforced Binary Authorization checks. Empty platform
outputs (for example a non-pushing build) are not deployable release evidence.

For a verifier requiring exactly one subject, consume `platform_provenance`
with `platform_digest`. This is a builder-produced projection of the same
statement, retaining all commit/material claims, and is archived beside the
full `provenance.json` as `platform-provenance.json`. It is absent for non-push
builds. The existing `slsa_provenance` output retains its complete subject list.

## Bounded CI experiment

- [2026-09-11 closeout: NO-GO, insufficient evidence](ci-pilot-closeout-20260911.md)
