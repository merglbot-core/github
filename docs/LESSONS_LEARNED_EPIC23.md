---
title: "Lessons Learned – EPIC #23 (WARP Standards Implementation)"
summary: "Shrnutí klíčových poučení a best practices z implementace WARP standardů (security, bot-driven, release) v rámci EPIC #23. Změny promítnuty do tréninkových materiálů a projektových pravidel."
owner: "platform"
last_updated: 2025-10-12
status: stable
---

# Lessons Learned – EPIC #23

## 1) Security & Git Hygiene
- Audit-before-rotate: Před rotací tajemství vždy audit logů (zachování důkazů)
- Force push guardrails: Nikdy `--force --all`; pouze konkrétní větev a s koordinací
- `.gitignore` z lokálních šablon: Nepoužívat `curl` z `main` větve; šablony držíme v repu
- Quiz answers odděleně: Odpovědi nejsou ve stejném souboru, interní klíč zvlášť

## 2) AI Safety (praktická doplnění)
- Konkrétní check-list SAFE vs NEVER (názvy secretů vs hodnoty)
- Přidán rychlý cheat sheet do `training/quick-reference/`

## 3) Container & Web Hardening
- Nginx: nepsát do `/etc/nginx`, běh pod ne-root uživatelem přes `su-exec`
- Security headers: odstranit `X-XSS-Protection`, spolehnout se na CSP; inline styly jen s vědomým trade-offem

## 4) CI/CD & OIDC/WIF
- Doplněn modul IAM & Access Control (WIF/OIDC) – minimální permissions, bez JSON klíčů
- Pinned Terraform verze v onboarding guide (konsistence)

## 5) Documentation Quality
- Rozlišení Production vs Staging odkazů v tréninkových materiálech
- Označení WIP modulů a jasné očekávání, co je hotovo

---

# Implemented Changes

- training/security/01-gitignore-security.md
  - remove remote curl; přidán postup s lokální šablonou + bezpečnostní poznámka
  - doplněn audit-before-rotate a bezpečné force-push instrukce
- training/security/certification-quiz.md
  - odstraněny inline odpovědi; přesun do `certification-quiz-answers.md`
- training/quick-start/new-developer-day1.md
  - pin Terraform (`terraform@1.6`), commit error handling
- training/README.md
  - odlišení Production vs Staging odkazů na tréninkovou platformu
- training/security/03-iam-access-control.md (NOVÉ)
  - WIF/OIDC modul s minimálním setupem a IAM rolemi
- training/quick-reference/ai-safety-cheatsheet.md (NOVÉ)
  - AI bezpečnostní rychlokarta (SAFE vs NEVER)

---

# Doporučení pro WARP (globální)
- Do globální WARP AI policy doplnit explicitní zákaz `git push --force --all`
- Do WARP Security playbooku přidat krok „Audit logs BEFORE rotation“
- Do WARP Container hardening guidelines přidat `su-exec` pattern a zákaz chown `/etc/*`

---

# Next Steps
- Přenést vybrané části do kanonické dokumentace v `merglbot-public/docs` (MERGLBOT_*.md)
- Nastavit CODEOWNERS pro `training/security/certification-quiz-answers.md`
- Přidat GitHub Actions check na grep `--force --all` v markdown příkladech (lint)
