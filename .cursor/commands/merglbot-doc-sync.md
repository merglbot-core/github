# Merglbot Doc Sync (SSOT Synchronization Check)

Use this after any code/infra change to keep documentation aligned (“Documentation = Reality”).

## Task
1. List documentation files that must be updated (SSOT: `merglbot-public/docs/`).
2. If changes are small and unambiguous, apply the minimal doc edits.
3. If docs are large/unclear, propose exact sections to update and why.

## Checklist (must cover)
- README / runbooks still match behavior?
- API docs match endpoints/params?
- Infra docs match Terraform reality?
- Any duplicated docs should be removed or replaced with links to SSOT.

## Output
- Files to update (paths)
- Proposed changes (bullets or patch)
- Verification steps (how to confirm docs are correct)
