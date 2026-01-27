# Merglbot Retro (SSOT-safe continuous learning)

Use this after finishing a task that required discovery (non-obvious debugging, workaround, tricky infra/config, new pattern).

## 0) Complete the task first
- Ensure the requested change is done and verified (Plan → Act → Verify).

## 1) Decide: SSOT update vs personal skill

### A) SSOT update (preferred for team knowledge)
If this learning should help the whole team (platform rule, recurrent pitfall, runbook gap):
1. Identify the best target in `merglbot-public/docs/` (existing doc first).
2. If no good home exists, create a new doc under `merglbot-public/docs/lessons/` using `merglbot-public/docs/lessons/TEMPLATE.md`.
3. Produce a **minimal** patch (only what’s needed), and include a verification note.

### B) Personal Agent Skill (only for local tactical memory)
If it’s too specific or too verbose for SSOT:
1. Create a local skill in `~/.cursor/skills/<skill-name>/SKILL.md` or `~/.claude/skills/<skill-name>/SKILL.md`.
2. Must include: trigger conditions (exact error/symptoms), solution steps, verification, and links to SSOT docs if relevant.
3. Do **not** commit personal skills to git.

## 2) Guardrails
- Never include secrets (values, tokens, private URLs, project numbers). Redact.
- Verify every claim against the codebase (imports, paths, commands, configs).
- Keep it short; avoid inventing new abstractions or “frameworks”.

## 3) Output format
Return:
1. **What we learned** (1–3 bullets)
2. **SSOT changes** (file paths + patch or exact edits)
3. **Optional personal skill** (only if justified; include file path under `~/.cursor/skills` or `~/.claude/skills`)
