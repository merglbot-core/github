# #922: missing billing measurements are not zero

The existing gross evaluator used `item.get("grossAmount") or 0` and the same
fallback for quantity. A sandbox using its actual source and a null after-window
row returned `FAKT`, a modeled USD 428.6/month, and attempted issue closeout. No
real billing request, issue mutation or runtime-state write occurred in that test.

This patch requires a complete `usageItems` array and valid Actions compute row
dimensions, dates, finite numeric quantity/gross values. Reported numeric signs
are preserved rather than changing correction/credit semantics. Missing,
null, string, boolean, nonfinite or invalid data block before artifacts, comments,
state writes or issue closure. Genuine numeric zero and a complete empty usage
array remain valid. Other products and storage retain their separate scope.
The fixed fixture regression compares old and patched evaluator behavior.

This does not establish settlement, net savings, attribution, API pagination or
freshness. The existing #934 acceptance contract remains required. It does not
change the #922 windows, DoD, owner decisions, credentials or V6 policy.

Run `python3 -m unittest discover -s tools/gh-cost-922-numerics -p 'test_*.py' -v`
through capped-run 2 GB. Activate only from substantively V6-reviewed protected
main, with fresh source/state SHA256 anchors. The installer reuses the reviewed
window installer atomic helper and existing lock, backs up source and never
writes state. Dry-run first; after any intervening tick re-read hashes. Replacement
failure restores source under the lock without rewinding newer state. A later
rollback likewise requires fresh anchors and the lock, never stale state restore.
Verify the next natural tick without a manual run or duplicate observer. Do not
invoke real billing acceptance before its time gate just to test this patch.
