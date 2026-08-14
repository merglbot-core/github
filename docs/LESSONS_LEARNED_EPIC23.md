---
title: "Lessons Learned – EPIC #23 (MERGLBOT Standards Implementation)"
summary: "Shrnutí klíčových poučení a best practices z implementace MERGLBOT standardů (security, bot-driven, release) v rámci EPIC #23. Změny promítnuty do tréninkových materiálů a projektových pravidel. Historický záznam uzavřeného EPICu; závěry ověřeny 2026-08-14."
owner: "platform"
last_updated: 2026-08-14
status: historical
---

# Lessons Learned – EPIC #23

> **Historický záznam uzavřeného EPICu (říjen 2025).** Obsah popisuje stav a rozhodnutí té doby a
> záměrně se nepřepisuje. Co se od té doby stalo s jeho závazky, je shrnuté v sekci
> [Ověření závěrů (2026-08-14)](#ověření-závěrů-2026-08-14) na konci — tam se dívej,
> pokud chceš vědět, co z EPICu doopravdy dojelo do estate.

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

# Doporučení pro MERGLBOT (globální)
- Do globální MERGLBOT AI policy doplnit explicitní zákaz: **NEVER** používat `git push --force --all`
- Do MERGLBOT Security playbooku přidat krok „Audit logs BEFORE rotation“
- Do MERGLBOT Container hardening guidelines přidat `su-exec` pattern a zákaz chown `/etc/*`

---

# Next Steps

*(Původní seznam z října 2025; stav k 2026-08-14 je v tabulce níže.)*

- Přenést vybrané části do kanonické dokumentace v `merglbot-public/docs` (MERGLBOT_*.md)
- Nastavit CODEOWNERS pro `training/security/certification-quiz-answers.md`
- Přidat GitHub Actions check na grep `--force --all` v markdown příkladech (lint)

---

# Ověření závěrů (2026-08-14)

Tenhle dokument je lessons-learned záznam, ne živý runbook — jeho hodnota není v tom, aby měl
čerstvé datum, ale v tom, aby se dalo dohledat, co z něj skutečně dojelo. Následující tabulka je
výsledek revize proti živému stavu (klon `merglbot-core/github` na `main`, klon
`merglbot-public/docs` na `main`, oboje čerstvě staženo 2026-08-14).

## Implemented Changes — všechny soubory existují

Všech sedm souborů z odstavce *Implemented Changes* je v tomto repozitáři přítomných
(`training/security/01-gitignore-security.md`, `certification-quiz.md`,
`certification-quiz-answers.md`, `03-iam-access-control.md`,
`training/quick-start/new-developer-day1.md`, `training/README.md`,
`training/quick-reference/ai-safety-cheatsheet.md`). Rozdělení kvízu a jeho odpovědí do dvou
souborů tedy drží.

## Doporučení pro MERGLBOT (globální) — splněno

| Doporučení | Stav | Kde to dnes žije |
|---|---|---|
| Zákaz kombinace `--force` + `--all` u `git push` v AI policy | ✅ splněno | `merglbot-public/docs/MERGLBOT_SECURITY_INCIDENT_RESPONSE.md` (explicitní NEVER-příklad) |
| „Audit logs BEFORE rotation" v Security playbooku | ✅ splněno | tamtéž, § *Audit-before-rotate (MUST)*; changelog dokumentu má řádek „2025-10-12: Přidán audit-before-rotate a guardrails k force push (EPIC #23)" |
| `su-exec` pattern + zákaz `chown /etc/*` v container guidelines | ✅ splněno | `merglbot-public/docs/MERGLBOT_CONTAINER_HARDENING.md` (a je vidět i v `MERGLBOT_TECHNOLOGY_INDEX.md`) |

## Next Steps — dva ze tří hotové

| Next step | Stav | Důkaz |
|---|---|---|
| Přenést části do kanonické dokumentace | ✅ hotovo | viz tabulka výše — vznikl kvůli tomu celý `MERGLBOT_SECURITY_INCIDENT_RESPONSE.md` |
| GitHub Actions lint na nebezpečný force-push příklad v markdownu | ✅ hotovo | `.github/workflows/markdown-danger-lint.yml` — na každém PR grepuje změněné `.md` a failuje, pokud najde ten vzor bez varovného kontextu (`never` / `do not` / `nepoužívej`) |
| Dedikovaný CODEOWNERS řádek pro `certification-quiz-answers.md` | ⚠️ nezaveden | `.github/CODEOWNERS` obsahuje jediné pravidlo `* @milan-merglevsky`. Soubor je tím sice pokrytý, ale wildcardem — samostatné pravidlo, které by přežilo případné zúžení `*`, tam není. Nízká priorita, dokud je repo single-maintainer. |

Metoda: existence souborů ověřena přímo v klonech (`rg --files`, ne `gh search code` — ten v tomhle
estate vrací prázdno i pro positive control, takže se z něj nedá číst absence). U hledání v
kanonických docs byl použit positive control (`rg -c -i 'wif'` → 135 zásahů), aby prázdný výsledek
u hledaného vzoru znamenal opravdovou absenci, a ne rozbitý dotaz.
