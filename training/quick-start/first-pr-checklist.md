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

- [PR_POLICY.md](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md)
- [MERGLBOT_PR_REVIEW_AUTONOMOUS_V5.md](https://github.com/merglbot-core/ai_prompts/blob/main/pr-review/MERGLBOT_PR_REVIEW_AUTONOMOUS_V5.md)
- [MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md)
