# Secret Management Best Practices

## ✅ Základní pravidla

- **Nikdy** nepiš secret values do logů ani do chatu.
- Secrety patří do **Secret Manager** (GCP) nebo GitHub Secrets/Variables (CI).
- V kódu používej jen názvy env vars.

## Cloud Run + cross-project secrets (nejčastější chyba)

- Cross-project Secret Manager se na Cloud Run váže přes alias mapping `run.googleapis.com/secrets`.
- Nepiš `projects/.../secrets/...` do `secretKeyRef.name` (Cloud Run validace to odmítne).

## Praktický checklist

- [ ] Secret value není v repu
- [ ] Secret name je konzistentní podle SSOT
- [ ] Runtime ověřen přes `gcloud run services describe --format=yaml`

## Reference (SSOT)

- `merglbot-public/docs/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md`
- `merglbot-public/docs/MERGLBOT_CROSS_PROJECT_SECRETS_SSOT.md`
- `merglbot-public/docs/MERGLBOT_AI_AGENT_APPENDIX_v2_15.md` (WARP-SECRET-XPROJ-001)
