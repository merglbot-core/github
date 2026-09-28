"""Offline consumer integration: never invoke GitHub, writes or merge commands."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/dependabot/ent_dependabot_closeout.py"
SOURCE_BYTES = None
HEAD = "a" * 40


def load_consumer():
  module = types.ModuleType("candidate_consumer_integration")
  module.__file__ = str(SOURCE)
  sys.modules[module.__name__] = module
  exec(compile(SOURCE.read_bytes() if SOURCE_BYTES is None else SOURCE_BYTES,
               str(SOURCE), "exec"), module.__dict__)
  def deny(*args, **kwargs):
    raise AssertionError("unmocked_external_io")
  for name in ("run_cmd", "gh_json", "gh_api_json", "gh_api_json_with_input",
               "github_api_direct", "urlopen", "write_json"):
    setattr(module, name, deny)
  return module


class FinalGateIntegration(unittest.TestCase):
  def setUp(self):
    self.m = load_consumer()
    self.pr = self.m.PullRequest("fixture/example", 7, "Bump package", "https://github.com/fixture/example/pull/7",
      "dependabot[bot]", HEAD, "b" * 40, "main", "dependabot/package", False, "CLEAN", "fixture")
    self.check_calls = []
    self.merge_calls = []
    self.final_checks_ok = True
    self.final_receipt = {"ok": True, "head_sha": HEAD, "blockers": [],
                          "autonomous_next_action": "safe_to_merge",
                          "docs_obligation_requires_external_evidence": True}
    self.original_scope = self.m.validate_file_scope_for_current_head
    self.scope_calls = 0
    self.final_scope_class = "LOCKFILE_ONLY"
    def scope(pr, receipt, **kw):
      self.scope_calls += 1
      receipt.validated_scope_class = self.final_scope_class
      return True, pr
    self.m.validate_file_scope_for_current_head = scope
    self.m.refresh_pr = lambda *args: self.pr
    self.m.wait_for_merglbot = lambda *args, **kw: dict(self.final_receipt)
    self.m.named_review_bot_status = lambda *args: (True, "advisory")
    self.m.named_review_bot_findings_ledger = lambda *args: []
    def checks(*args):
      self.check_calls.append(args)
      ok = len(self.check_calls) == 1 or self.final_checks_ok
      return ok, [], [] if ok else ["changed_to_failure"], []
    self.m.required_checks = checks
    def command(argv, check=False):
      self.assertEqual(argv[-2:], ["--surface", "v6"])
      return types.SimpleNamespace(returncode=0, stdout=json.dumps(self.final_receipt), stderr="")
    self.m.run_cmd = command
    def merge(repo, number, head):
      self.merge_calls.append((repo, number, head))
      return {"state": "MERGED", "merge_commit": "c" * 40}
    self.m.merge_pr = merge

  def execute(self, mode="apply", max_reviews=5):
    return self.m.process_pr(self.pr, mode=mode, output_dir=Path("unused-fixture"),
      allow_policy_alignment=False, workflow_url="fixture", validator_profile="maximum_autonomy_v2",
      sibling_prs=[], autonomous_fix_loop=False, max_fix_iterations=5, max_review_iterations=max_reviews)

  def test_clean_current_head_uses_final_checks_and_pinned_merge(self):
    result = self.execute()
    self.assertEqual(result.action, "merged")
    self.assertEqual(len(self.check_calls), 2)
    self.assertEqual(self.merge_calls, [("fixture/example", 7, HEAD)])

  def test_check_changed_after_review_blocks_merge(self):
    self.final_checks_ok = False
    result = self.execute()
    self.assertEqual(result.classification, "BLOCKED_FINAL_REQUIRED_CHECKS")
    self.assertEqual(self.merge_calls, [])

  def test_final_authority_hold_blocks_merge(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": True, "head_sha": HEAD}
    self.final_receipt["autonomous_next_action"] = "request_authority"
    result = self.execute()
    self.assertIn("merglbot:merglbot_merge_authority_not_accepted", result.blockers)
    self.assertEqual(self.merge_calls, [])

  def test_missing_final_authority_blocks_merge(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": True, "head_sha": HEAD}
    del self.final_receipt["autonomous_next_action"]
    result = self.execute()
    self.assertIn("merglbot:merglbot_merge_authority_not_accepted", result.blockers)
    self.assertEqual(self.merge_calls, [])

  def test_final_head_change_blocks_merge(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": True, "head_sha": HEAD}
    self.final_receipt["head_sha"] = "d" * 40
    result = self.execute()
    self.assertIn("head_changed_at_final_merglbot", result.blockers)
    self.assertEqual(self.merge_calls, [])

  def test_final_provider_or_finding_failure_blocks_merge(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": True, "head_sha": HEAD}
    self.final_receipt.update(ok=False, blockers=["provider_degraded_or_missing"])
    result = self.execute()
    self.assertEqual(result.classification, "BLOCKED_FINAL_MERGLBOT")
    self.assertEqual(self.merge_calls, [])

  def test_dry_run_also_revalidates_without_merge(self):
    result = self.execute("dry-run")
    self.assertEqual(result.action, "would_merge")
    self.assertEqual(len(self.check_calls), 2)
    self.assertEqual(self.merge_calls, [])

  def test_dry_run_missing_receipt_does_not_claim_trigger_eligibility(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": False, "head_sha": HEAD, "blockers": []}
    result = self.execute("dry-run")
    self.assertEqual(result.classification, "REVIEW_STATUS_REQUIRED")
    self.assertFalse(result.would_dispatch_merglbot_review)
    self.assertEqual(self.merge_calls, [])


  def test_unclassified_docs_impact_cannot_merge_on_technical_approval(self):
    self.final_scope_class = "NO_CHANGE"
    result = self.execute()
    self.assertEqual(result.classification, "BLOCKED_FINAL_DOCS_OBLIGATION")
    self.assertEqual(self.merge_calls, [])

  def test_real_lockfile_classifier_replays_on_final_head(self):
    self.m.validate_file_scope_for_current_head = self.original_scope
    self.m.pr_files = lambda *args: ["package-lock.json"]
    self.m.classify_close_candidate = lambda *args: None
    result = self.execute()
    self.assertEqual(result.action, "merged")
    self.assertIn("docs_obligation=no-doc-impact:current-head-content-validated-dependency-pins", result.evidence)

  def test_real_classifier_blocks_new_runtime_file_at_final_replay(self):
    initial = self.m.validate_file_scope_for_current_head
    def scope(pr, receipt, **kw):
      if self.scope_calls == 0:
        return initial(pr, receipt, **kw)
      return self.original_scope(pr, receipt, **kw)
    self.m.validate_file_scope_for_current_head = scope
    self.m.pr_files = lambda *args: ["package-lock.json", "src/runtime.py"]
    self.m.classify_close_candidate = lambda *args: None
    result = self.execute()
    self.assertEqual(result.classification, "BLOCKED_FINAL_DOCS_OBLIGATION")
    self.assertEqual(self.merge_calls, [])

  def test_final_docs_replay_head_change_blocks_merge(self):
    initial = self.m.validate_file_scope_for_current_head
    def scope(pr, receipt, **kw):
      if self.scope_calls == 0:
        return initial(pr, receipt, **kw)
      return True, types.SimpleNamespace(head_sha="d" * 40)
    self.m.validate_file_scope_for_current_head = scope
    result = self.execute()
    self.assertEqual(result.classification, "BLOCKED_FINAL_DOCS_OBLIGATION")
    self.assertEqual(self.merge_calls, [])


  def test_real_pipeline_rebinds_more_heads_than_legacy_counter(self):
    from dataclasses import replace
    current = [self.pr]
    changes = [0]
    self.m.refresh_pr = lambda *args: current[0]
    def review(*args, **kw):
      if changes[0] < 7:
        changes[0] += 1
        current[0] = replace(current[0], head_sha=str(changes[0]).zfill(40))
        return {"ok": False, "head_sha": current[0].head_sha,
                "head_changed_during_review_wait": True, "blockers": []}
      self.final_receipt["head_sha"] = current[0].head_sha
      return dict(self.final_receipt)
    self.m.wait_for_merglbot = review
    result = self.execute(max_reviews=1)
    self.assertEqual(result.action, "merged")
    self.assertEqual(result.review_iterations, 8)
    self.assertEqual(self.merge_calls, [("fixture/example", 7, current[0].head_sha)])

  def test_rebind_claim_without_new_head_stops_without_merge(self):
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": False,
      "head_changed_during_review_wait": True, "head_sha": HEAD, "blockers": []}
    result = self.execute(max_reviews=1)
    self.assertEqual(result.classification, "REVIEW_REBIND_CONTINUATION_REQUIRED")
    self.assertEqual(self.merge_calls, [])

  def test_behind_authority_hold_does_not_sync(self):
    self.pr.merge_state = "BEHIND"
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": False, "head_sha": HEAD,
      "autonomous_next_action": "request_authority", "blockers": ["authority_hold"]}
    self.m.request_update_branch = lambda *args, **kw: self.fail("premature sync")
    result = self.execute()
    self.assertEqual(self.merge_calls, [])
    self.assertIn("merglbot:authority_hold", result.blockers)

  def test_strict_updated_head_waits_for_checks_and_review_then_merges(self):
    from dataclasses import replace
    self.pr.merge_state = "BEHIND"
    self.m.branch_protection = lambda *args: {"required_status_checks": {"strict": True}}
    next_head = "e" * 40
    def update(*args, **kw):
      self.pr = replace(self.pr, head_sha=next_head, merge_state="CLEAN")
      return {"ok": True, "final_head_sha": next_head}
    self.m.request_update_branch = update
    self.m.wait_for_merglbot = lambda *args, **kw: {"ok": True, "head_sha": HEAD}
    checks = []
    def gates(*args):
      checks.append(1)
      pending = len(checks) == 2
      return not pending, [{"bucket": "pending" if pending else "pass"}], ["pending"] if pending else [], []
    self.m.required_checks = gates
    reviews = []
    def verify(*args):
      reviews.append(1)
      if len(reviews) == 1:
        return {"ok": False, "head_sha": next_head, "blockers": ["v6_review_in_progress"]}
      return {"ok": True, "head_sha": next_head, "autonomous_next_action": "safe_to_merge",
              "docs_obligation_requires_external_evidence": True}
    self.m.verify_merglbot = verify
    clock = [0]
    delays = []
    def wait(n):
      delays.append(n)
      clock[0] += n
    self.m.time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=wait)
    self.m.trigger_merglbot_review = lambda *args, **kw: self.fail("updated-head duplicate trigger")
    result = self.execute()
    self.assertEqual(result.action, "merged")
    self.assertEqual(self.merge_calls, [("fixture/example", 7, next_head)])
    self.assertEqual(delays, [self.m.MERGLBOT_REVIEW_POLL_SECONDS])

  def test_approved_behind_non_strict_does_not_sync(self):
    self.pr.merge_state = "BEHIND"
    self.m.branch_protection = lambda *args: {"required_status_checks": {"strict": False}}
    self.m.request_update_branch = lambda *args, **kw: self.fail("unnecessary sync")
    result = self.execute()
    self.assertEqual(result.action, "merged")

  def test_strict_sync_occurs_only_after_review_and_green_checks(self):
    self.pr.merge_state = "BEHIND"
    events = []
    self.m.wait_for_merglbot = lambda *args, **kw: events.append("review") or dict(self.final_receipt)
    self.m.branch_protection = lambda *args: {"required_status_checks": {"strict": True}}
    def update(*args, **kw):
      self.assertTrue(self.check_calls)
      events.append("update")
      return {"ok": False, "blockers": ["fixture_update_not_executed"]}
    self.m.request_update_branch = update
    self.execute()
    self.assertEqual(events, ["review", "update"])
    self.assertEqual(self.merge_calls, [])

  def test_unknown_strict_contract_blocks_without_sync(self):
    self.pr.merge_state = "BEHIND"
    self.m.branch_protection = lambda *args: None
    self.m.request_update_branch = lambda *args, **kw: self.fail("unknown protection sync")
    result = self.execute()
    self.assertIn("update_branch:effective_strict_contract_unknown", result.blockers)
    self.assertEqual(self.merge_calls, [])


class ReviewCadenceIntegration(unittest.TestCase):
  def test_pending_checks_exit_eight_is_parsed_as_pending(self):
    m = load_consumer()
    m.run_cmd = lambda *args, **kw: types.SimpleNamespace(returncode=8,
      stdout=json.dumps([{"name": "ci", "bucket": "pending", "state": "IN_PROGRESS"}]), stderr="")
    ok, checks, blockers, diagnostics = m.required_checks("fixture/example", 7)
    self.assertFalse(ok)
    self.assertEqual(checks[0]["bucket"], "pending")
    self.assertNotIn("required_checks_lookup_failed", blockers)

  def test_updated_gates_stop_on_authority_failure_or_head_change(self):
    for kind in ["authority", "failure", "head"]:
      with self.subTest(kind=kind):
        m = load_consumer()
        pr = types.SimpleNamespace(repo="fixture/example", number=7, head_sha=HEAD)
        m.refresh_pr = lambda *args: types.SimpleNamespace(head_sha="d" * 40 if kind == "head" else HEAD)
        m.required_checks = lambda *args: (False, [{"bucket": "fail" if kind == "failure" else "pending"}], ["check_failure"], [])
        m.verify_merglbot = lambda *args: {"ok": False, "head_sha": HEAD,
          "autonomous_next_action": "request_authority" if kind == "authority" else None,
          "blockers": ["authority_hold"] if kind == "authority" else ["v6_review_in_progress"]}
        m.time = types.SimpleNamespace(monotonic=lambda: 0, sleep=lambda *args: self.fail("terminal state sleep"))
        ok, review, blockers = m.wait_for_updated_head_gates(pr)
        self.assertFalse(ok)
        self.assertTrue(blockers)

  def test_trusted_login_alone_cannot_trigger(self):
    m = load_consumer()
    m.MERGLBOT_REVIEW_TRIGGER_TRUSTED = True
    m.post_comment_with_stdin = lambda *args: self.fail("missing status comment")
    with self.assertRaises(m.GhError):
      m.trigger_merglbot_review("fixture/example", 7, "fixture", HEAD)

  def test_retrigger_false_stale_or_running_status_cannot_trigger(self):
    m = load_consumer()
    m.MERGLBOT_REVIEW_TRIGGER_TRUSTED = True
    m.post_comment_with_stdin = lambda *args: self.fail("ineligible comment")
    base = {"ok": True, "repo": "fixture/example", "pr": 7, "head": HEAD,
            "should_retrigger": True, "v6_in_progress": False}
    for change in [{"should_retrigger": False}, {"head": "d" * 40}, {"v6_in_progress": True}, {"repo": "fixture/other"}]:
      with self.subTest(change=change), self.assertRaises(m.GhError):
        m.trigger_merglbot_review("fixture/example", 7, "fixture", HEAD, review_status={**base, **change})

  def test_exact_eligible_status_and_trusted_login_trigger_once(self):
    m = load_consumer()
    m.MERGLBOT_REVIEW_TRIGGER_TRUSTED = True
    calls = []
    m.post_comment_with_stdin = lambda *args: calls.append(args) or "fixture-comment"
    result = m.trigger_merglbot_review("fixture/example", 7, "fixture", HEAD,
      review_status={"ok": True, "repo": "fixture/example", "pr": 7, "head": HEAD,
                     "should_retrigger": True, "v6_in_progress": False})
    self.assertEqual(len(calls), 1)
    self.assertEqual(result["head_sha"], HEAD)

  def test_invalid_transport_output_is_error_without_trigger_or_raw_diagnostics(self):
    m = load_consumer()
    m.run_cmd = lambda *args, **kw: types.SimpleNamespace(returncode=1, stdout="not-json",
      stderr="fixture-private-diagnostic")
    m.trigger_merglbot_review = lambda *args, **kw: self.fail("transport failure trigger")
    result = m.wait_for_merglbot("fixture/example", 7, "fixture", HEAD, apply=True)
    self.assertTrue(result["transport_error"])
    self.assertNotIn("fixture-private-diagnostic", json.dumps(result))

  def test_timeout_has_no_zero_delay_or_extra_poll(self):
    m = load_consumer()
    clock = [0]
    calls = []
    delays = []
    def wait(n):
      delays.append(n)
      clock[0] += n
    m.time = types.SimpleNamespace(time=lambda: clock[0], sleep=wait)
    m.verify_merglbot = lambda *args: calls.append(clock[0]) or {"ok": False, "head_sha": HEAD, "blockers": ["v6_review_in_progress"]}
    m.trigger_merglbot_review = lambda *args, **kw: {"method": "fixture"}
    result = m.wait_for_merglbot("fixture/example", 7, "fixture", HEAD, apply=True)
    self.assertIn("merglbot_review_poll_timeout", result["blockers"])
    self.assertTrue(all(300 <= n <= 600 for n in delays))
    self.assertEqual(len(calls), len(delays) + 1)
    self.assertLessEqual(clock[0], 1800)

  def test_approved_technical_authority_hold_is_terminal_without_trigger(self):
    m = load_consumer()
    m.verify_merglbot = lambda *args: {"ok": False, "head_sha": HEAD, "review_head_sha": HEAD,
      "current_head_match": True, "verdict": "approved_for_closeout", "status": "success",
      "autonomous_next_action": "request_authority", "blockers": ["merglbot_merge_authority_not_accepted"]}
    m.trigger_merglbot_review = lambda *args, **kw: self.fail("terminal hold trigger")
    result = m.wait_for_merglbot("fixture/example", 7, "fixture", HEAD, apply=True)
    self.assertFalse(result["ok"])

  def test_pending_poll_uses_bounded_five_minute_cadence(self):
    m = load_consumer()
    clock = [0]
    delays = []
    def wait(n):
      delays.append(n)
      clock[0] += n
    m.time = types.SimpleNamespace(time=lambda: clock[0], sleep=wait)
    calls = []
    def verify(*args):
      calls.append(clock[0])
      return {"ok": len(calls) == 2, "head_sha": HEAD, "blockers": [] if len(calls) == 2 else ["v6_review_in_progress"]}
    m.verify_merglbot = verify
    m.trigger_merglbot_review = lambda *args, **kw: {"method": "fixture"}
    result = m.wait_for_merglbot("fixture/example", 7, "fixture", HEAD, apply=True)
    self.assertTrue(result["ok"])
    self.assertEqual(delays, [m.MERGLBOT_REVIEW_POLL_SECONDS])
    self.assertGreaterEqual(delays[0], 300)
    self.assertLessEqual(delays[0], 600)


class CanonicalReceiptBridge(unittest.TestCase):
  def result(self, replacement=None):
    path = ROOT / "scripts/pr-assistant/verify-review-receipt.py"
    module = types.ModuleType("candidate_verifier_bridge")
    module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    case = json.loads((ROOT / "projects/ai-efficiency-2026-09/fixtures/1383/05_ordinary_engine_run_passes_strict.json").read_text())
    summary = case["summary"]
    if replacement:
      summary = summary.replace(*replacement)
    run = {"id": 1, "name": module.V6_CHECK_NAME, "status": "completed", "conclusion": "success",
      "app": {"id": module.TRUSTED_V6_CHECK_APP_ID, "slug": module.TRUSTED_V6_CHECK_APP_SLUG,
              "owner": {"login": module.TRUSTED_V6_CHECK_APP_OWNER}},
      "pull_requests": [{"number": 3277}], "output": {"summary": summary}}
    payload = module.verify_v6("merglbot-core/infra", {"number": 3277}, case["head_sha"], 3277,
                              read_json=lambda args: [{"check_runs": [run]}])
    consumer = load_consumer()
    consumer.run_cmd = lambda argv, check=False: types.SimpleNamespace(
      returncode=0 if payload["ok"] else 1, stdout=json.dumps(payload), stderr="")
    return payload, consumer.verify_merglbot("merglbot-core/infra", 3277)

  def test_current_clean_receipt_preserves_authority_projection(self):
    technical, merge = self.result()
    self.assertTrue(technical["ok"])
    self.assertTrue(merge["ok"])
    self.assertEqual(merge["autonomous_next_action"], "safe_to_merge")

  def test_technical_pass_with_authority_hold_is_not_merge_permission(self):
    technical, merge = self.result(("AUTONOMOUS_NEXT_ACTION: safe_to_merge", "AUTONOMOUS_NEXT_ACTION: request_authority"))
    self.assertTrue(technical["ok"])
    self.assertFalse(merge["ok"])
    self.assertIn("merglbot_merge_authority_not_accepted", merge["blockers"])

  def test_missing_authority_does_not_default_to_merge(self):
    technical, merge = self.result(("<!-- MERGLBOT_AUTONOMOUS_NEXT_ACTION: safe_to_merge -->", ""))
    self.assertTrue(technical["ok"])
    self.assertFalse(merge["ok"])

  def test_all_skipped_remains_review_incomplete(self):
    technical, merge = self.result(("ENGINE_EVIDENCE: codex:pass,claude:pass", "ENGINE_EVIDENCE: codex:skipped,claude:skipped"))
    self.assertFalse(technical["ok"])
    self.assertFalse(merge["ok"])
    self.assertIn("no_engine_produced_verdict", merge["blockers"])

  def test_unknown_findings_remain_blocking(self):
    technical, merge = self.result(("ACTIONABLE_FINDINGS_COUNT: 0", "ACTIONABLE_FINDINGS_COUNT: unknown"))
    self.assertFalse(technical["ok"])
    self.assertFalse(merge["ok"])
    self.assertIn("actionable_findings_count_not_delivered", merge["blockers"])


if __name__ == "__main__":
  unittest.main()
