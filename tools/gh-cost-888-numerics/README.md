# #896 numeric acceptance guard

Refs merglbot-core/github#896. The installed #888 evaluator maps missing quantity to zero. Its actual function, run with synthetic data and stubbed I/O, returned FAKT and attempted closure with null after-window measurements. This is a false-acceptance regression, not realized savings.

Reuse the protected #922 numeric validators for complete response dimensions, UTC dates and finite values. Reject aggregate/derived overflow before any artifacts or mutations. Keep the existing #888 Linux/minute-based model, windows, DoD and owner decisions; it is a proxy and does not replace #934 settled-net acceptance. Other products and Linux storage are excluded. A row returned under the wrong requested month blocks acceptance.

The code-only installer reuses the protected numeric installer, sets only the #888 base path in a newly loaded module, and uses its existing lock, fresh source/state hashes, OWNER_HOLD checks, backup and atomic rollback. Published/closed states require reconciliation; state is never rewritten. Dry-run first, refresh hashes after intervening ticks, then verify a natural tick. Never manually invoke financial acceptance before its gate. Rollback restores only source under the same lock and fresh anchors, never older state.

Tests: capped-run 2 GB python3 -m unittest discover -s tools/gh-cost-888-numerics -p 'test_*.py'. Shared dependencies must come from the same protected main source, including gh-cost-922-numerics and gh-cost-922-window.
