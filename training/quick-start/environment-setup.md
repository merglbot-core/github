# Setting Up Your Environment (Quick Start)

## ✅ Cíl

Za 30–60 minut mít funkční lokální prostředí pro práci na Merglbot repozitářích.

## 1) Základní nástroje

- Git
- GitHub CLI (`gh`)
- Node.js (doporučeno dle `.nvmrc` v repu)
- Python 3
- Google Cloud SDK (`gcloud`)

### Ověření

```bash
git --version
gh --version
node --version
python3 --version
gcloud --version
```

## 2) GitHub přístup

```bash
gh auth status
```

- Používej HTTPS auth přes `gh`.
- **Nikdy** nevkládej tokeny/secrety do souborů ani do logů.

## 3) GCP přístup

- Autentizace: preferuj WIF/OIDC v CI; lokálně používej ADC jen pro read-only debug.

```bash
gcloud auth list
gcloud config list
```

## 4) Repo workflow

- Vždy pracuj na branchi (`feat/…`, `fix/…`, `docs/…`, `ci/…`).
- PR je povinný pro production změny.

## Reference (SSOT)

- `merglbot-public/docs/RULEBOOK_V2.md`
- `merglbot-public/docs/MERGLBOT_AI_AGENT_APPENDIX_v2_15.md`
- `merglbot-public/docs/REPOSITORY_MAP.md`
