---
title: docs#1383 carried-forward receipt fixtures
description: Fixtures replayed by verify-review-receipt.py --self-test for carried-forward V6 receipts (docs#1383, platform#1693).
lifecycle: active
last_updated: 2026-09-28
---

# docs#1383 — carried-forward receipt fixtures

Replayed by `scripts/pr-assistant/verify-review-receipt.py --self-test` (`run_fixture_cases`). Each file: `summary` (check-run `output.summary` markers, shape of the first production carried receipt on merglbot-core/infra#3277, 28. 9. 2026), `head_sha`, `conclusion`, `require_verified_engine_run` and `expected_blockers`.

| fixture | consumer | result |
|---|---|---|
| 01 | default | PASS — current-head evidence (PR_POLICY §3.4.1) |
| 02 | `--require-verified-engine-run` | FAIL `carried_forward_not_verified_engine_run` |
| 03 | default, moved head | FAIL `merglbot_review_head_sha_mismatch` |
| 04 | strict, engine rows `carried-forward`, no synthesis marker | FAIL `carried_forward_not_verified_engine_run` |
| 05 | strict, ordinary engine rows | PASS — engine-run approval |
