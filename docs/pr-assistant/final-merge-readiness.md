# Final Merge Readiness Policy

The Final Merge Readiness check is a read-only policy gate for Merglbot pull
requests. It does not merge, deploy, run Terraform, or create documentation
pull requests. The check only decides whether the current PR head has enough
evidence for a human merge.

The policy manifest lives in `.github/policies/final-merge-readiness.json`.
The evaluator lives in `scripts/pr-assistant/final-merge-readiness.py`, and the
GitHub Actions check is `.github/workflows/final-merge-readiness.yml`.

The evaluator emits a JSON receipt with these gates:

- `path_risk_gate`: classifies changed paths, blocks committed secret-like
  files, private key material, service account JSON keys, Terraform execution
  commands, and branch-protection bypass language.
- `pr_assistant_review_only_evidence`: parses the latest trusted PR Assistant
  v4 or v3 receipt comment from `github-actions[bot]` or the approved
  Merglbot v4 GitHub App bot, requires review-only markers where the runtime
  emits them, current-head SHA, approved verdict, successful status, satisfied
  or not-required documentation state, verified PR check surface, and either an
  allowed PR Assistant workflow run path or an approved external v4 run id.
- `required_checks_gate`: reads branch-protection required checks from GitHub
  and falls back to the manifest context list when that surface is unavailable.
- `draft_state_gate`: blocks draft pull requests.

Docs-only pull requests can pass without a PR Assistant receipt. Any non-docs
or high-risk path still requires trusted PR Assistant evidence for the current
head.

Local validation:

```bash
python3 -m py_compile scripts/pr-assistant/final-merge-readiness.py
python3 scripts/pr-assistant/final-merge-readiness.py --self-test
```
