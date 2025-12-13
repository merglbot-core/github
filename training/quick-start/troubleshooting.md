# Troubleshooting (Quick Start)

## `gh auth status` hlásí, že nejsi přihlášený

```bash
gh auth login
```

## Git push nemá práva

- Ověř, že pushuješ do správného remote.
- Ověř org membership a repo permissions.

## `gcloud` chyby / permission denied

- Ověř aktivní účet a projekt:

```bash
gcloud auth list
gcloud config list
```

- Pro production změny používej CI/WIF, lokálně jen read-only.

## Linky v dokumentaci nefungují

- SSOT dokumentace: `merglbot-public/docs/`
- Repo map: `REPOSITORY_MAP.md`

## Reference

- `merglbot-public/docs/DOCUMENTATION_INDEX.md`
