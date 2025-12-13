# First PR Checklist

## ✅ Před odesláním PR

- [ ] Mám správnou branch (`feat/…`, `fix/…`, `docs/…`, `ci/…`).
- [ ] Změna je malá a fokusovaná (ne mix unrelated věcí).
- [ ] Žádné secrety v kódu ani v logu.
- [ ] Aktualizoval jsem dokumentaci tam, kde je to potřeba.

## ✅ PR obsah

- [ ] Jasný title (conventional prefix: `feat:`, `fix:`, `docs:`, `ci:` …)
- [ ] Popis: **proč** změna existuje + dopad
- [ ] Test plan (co a jak ověřit)
- [ ] Pokud je relevantní: rollback / backout

## ✅ Po vytvoření PR

- [ ] Spustily se checks
- [ ] V případě potřeby spouštím PR review asistenta: komentář `@merglbot review`

## Reference (SSOT)

- `merglbot-public/docs/PR_POLICY.md`
- `merglbot-public/docs/MERGLBOT_PR_REVIEW_AUTONOMOUS_V5.md` (v `merglbot-core/ai_prompts/pr-review/`)
- `merglbot-public/docs/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md`
