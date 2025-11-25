# Merglbot Copilot Instructions

> **Purpose**: Custom instructions for GitHub Copilot when working with Merglbot repositories.

---

## Project Context

You are working on **Merglbot** - an AI-powered code assistant platform.

### Tech Stack
- **Frontend**: React 18, TypeScript 5, MUI v6, ECharts
- **Backend**: Google Cloud Run, Firestore, BigQuery
- **Infrastructure**: Terraform, GitHub Actions
- **Languages**: TypeScript (primary), Python, HCL

---

## Critical Rules

### Security (MUST FOLLOW)
1. **Never suggest hardcoded secrets** - Always use environment variables or `${{ secrets.* }}`
2. **Never suggest SA JSON keys** - Use Workload Identity Federation (WIF/OIDC)
3. **SHA-pin GitHub Actions** - Use full commit SHA, not version tags
4. **Never log sensitive data** - No passwords, tokens, or PII in logs

### Code Patterns
1. **Use explicit TypeScript types** - No `any` unless absolutely necessary
2. **Handle errors properly** - Try/catch with meaningful error messages
3. **Follow existing patterns** - Match the style of surrounding code
4. **Write self-documenting code** - Clear names over comments

---

## Preferred Patterns

### TypeScript
```typescript
// Prefer
const fetchUser = async (id: string): Promise<User | null> => {
  try {
    return await userService.get(id);
  } catch (error) {
    logger.error('Failed to fetch user', { id, error });
    return null;
  }
};

// Avoid
const fetchUser = async (id: any) => {
  return await userService.get(id);
};
```

### React Components
```tsx
// Prefer functional components with TypeScript
interface ButtonProps {
  label: string;
  onClick: () => void;
  variant?: 'primary' | 'secondary';
}

export const Button: React.FC<ButtonProps> = ({ 
  label, 
  onClick, 
  variant = 'primary' 
}) => (
  <MuiButton variant={variant} onClick={onClick}>
    {label}
  </MuiButton>
);
```

### GitHub Actions
```yaml
# Prefer SHA-pinned actions
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

# Prefer minimal permissions
permissions:
  contents: read

# Always add timeouts
timeout-minutes: 30
```

---

## When Suggesting Code

1. **Match existing style** - Follow the patterns already in the codebase
2. **Prefer simplicity** - Don't over-engineer solutions
3. **Include error handling** - Handle edge cases and failures
4. **Add types** - Always include TypeScript types
5. **Consider security** - Think about injection, auth, and data exposure

---

## When Reviewing Code

Focus on:
1. **Security issues** - Secrets, auth, injection
2. **Type safety** - Missing or incorrect types
3. **Error handling** - Unhandled exceptions
4. **Code quality** - Readability, maintainability

Output format:
- Use Czech for explanations (preferred)
- Use English for code examples
- Prioritize: Critical > High > Medium > Low

---

## Reference

Key documentation:
- `RULEBOOK_V2.md` - Platform rules
- `SECURITY.md` - Security policy
- `WARP_GLOBAL_RULES.txt` - Agent rules

---

**Version**: 1.0  
**Last Updated**: 2025-11-25

