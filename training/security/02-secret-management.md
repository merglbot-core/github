# Secret Management Best Practices

## ✅ Základní pravidla

- **Nikdy** nepiš secret values do logů ani do chatu.
- Secrety patří do **Secret Manager** (GCP) nebo GitHub Secrets/Variables (CI).
- V kódu používej jen názvy env vars.

## Cloud Run + cross-project secrets (nejčastější chyba)

- Cross-project Secret Manager se na Cloud Run váže přes alias mapping `run.googleapis.com/secrets`.
- Nepiš `projects/.../secrets/...` do `secretKeyRef.name` (Cloud Run validace to odmítne).
- Recovery-safe deploy pattern je povinný: nejdřív base rollout image/plain env při zachování stávajících cross-project bindingů, až potom export fresh service YAML, patch alias mappingu a `gcloud run services replace`.
- Nikdy nereplayuj exportovaný current service YAML před target rolloutem; u rozbité služby to může zacyklit stejnou broken revision a zablokovat recovery deploy.
- Secret resource identifiers ber jako citlivá metadata: neposílej plné `projects/.../secrets/...` reference přes `$GITHUB_ENV`, `$GITHUB_OUTPUT`, workflow outputs ani job summaries.
- Pokud workflow-owned krok (step definovaný v reusable workflow, ne v caller repu) v tomtéž jobu musí zalogovat neočekávanou hodnotu, zavolej nejdřív `echo "::add-mask::$VALUE"`. Nikdy nevypisuj raw hodnotu před maskováním; po maskování vypisuj jen redigovanou diagnostiku.
- Caller-visible outputs a summaries smí nést maximálně aliasy, count nebo boolean status. Plné resource identifiers ani jiná raw metadata do nich nepatří.

## Praktický checklist

- [ ] Secret value není v repu
- [ ] Secret name je konzistentní podle SSOT
- [ ] Runtime ověřen přes:
      `gcloud run services describe SERVICE_NAME --project PROJECT_ID --region REGION --format="value(spec.template.metadata.annotations['run.googleapis.com/secrets'])"`
- [ ] `secretKeyRef.name` používá alias, ne full resource path

## Reference (SSOT)

- [MERGLBOT_SECRETS_NAMING_AND_LOGGING.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md)
- [MERGLBOT_CROSS_PROJECT_SECRETS_SSOT.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_CROSS_PROJECT_SECRETS_SSOT.md)
- [MERGLBOT_AI_AGENT_APPENDIX_v2_15.md](https://github.com/merglbot-core/ai_prompts/blob/main/agent-appendix/MERGLBOT_AI_AGENT_APPENDIX_v2_15.md) (MERGLBOT-SECRET-XPROJ-001)
