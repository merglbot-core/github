"""V6 reader trust/coverage controls; no network, credentials or publications."""
import copy
import importlib.util
import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "v6_receipt", ROOT / "scripts/pr-assistant/verify-review-receipt.py"
)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
HEAD = "a" * 40
REPO = "example/repo"
CALLER_SPEC = importlib.util.spec_from_file_location(
    "v6_closeout_test", ROOT / "scripts/dependabot/ent_dependabot_closeout.py"
)
caller = importlib.util.module_from_spec(CALLER_SPEC)
sys.modules[CALLER_SPEC.name] = caller
CALLER_SPEC.loader.exec_module(caller)


def check(check_id=1):
    markers = {
        "MERGLBOT_PR_ASSISTANT_V6": "true",
        "MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION": "1",
        "MERGLBOT_REVIEW_SOURCE": REPO + "#7",
        "MERGLBOT_REVIEW_HEAD_SHA": HEAD,
        "MERGLBOT_RECEIPT_SURFACE": "check_run_summary",
        "MERGLBOT_MARKER_STATUS": "parseable_receipt",
        "MERGLBOT_REVIEW_STATUS": "success",
        "MERGLBOT_REVIEW_VERDICT": "approved_for_closeout",
        "MERGLBOT_PROVIDER_DEGRADED": "false",
        "MERGLBOT_ACTIONABLE_FINDINGS_COUNT": "0",
        "MERGLBOT_AUTONOMOUS_NEXT_ACTION": "safe_to_merge",
        "MERGLBOT_LOCAL_PRIMARY_ENGINE_EVIDENCE": "codex:pass,claude:pass",
        "MERGLBOT_RUN_ID": "pr-assistant-v6:local-primary:synthetic-round",
    }
    return {
        "id": check_id, "name": reader.V6_CHECK_NAME, "app": {"id": reader.V6_APP_ID},
        "head_sha": HEAD, "status": "completed", "conclusion": "success",
        "html_url": "https://github.com/example/repo/runs/1",
        "output": {"summary": "\n".join(f"<!-- {k}: {v} -->" for k, v in markers.items())},
    }


class V6ReviewReceiptTests(unittest.TestCase):
    def evaluate(self, rows=None, pages=None, final_head=HEAD):
        if pages is None:
            rows = [check()] if rows is None else rows
            pages = [{"total_count": len(rows), "check_runs": rows}]
        pr = {"head": {"sha": HEAD}, "html_url": "https://github.com/example/repo/pull/7"}
        end = copy.deepcopy(pr)
        end["head"]["sha"] = final_head
        head_reads = 0
        def get(args):
            nonlocal head_reads
            path = args[-1]
            if "/pulls/" in path:
                head_reads += 1
                return copy.deepcopy(pr if head_reads == 1 else end)
            if "/check-suites?" in path:
                return [{"total_count": 1, "check_suites": [{"id": 10, "app": {"id": reader.V6_APP_ID}, "head_sha": HEAD}]}]
            if "/check-runs?" in path:
                return copy.deepcopy(pages)
            if "/check-runs/" in path:
                check_id = int(path.rsplit("/", 1)[1])
                return copy.deepcopy(next(c for page in pages for c in page["check_runs"] if c["id"] == check_id))
            raise AssertionError(path)
        with patch.object(reader, "gh_json", side_effect=get) as api:
            result = reader.verify(REPO, 7, "v6")
        # Only the existing PR and the App check surface are queried.
        for call in api.call_args_list:
            self.assertNotIn("comments", " ".join(call.args[0]))
        self.assertIn("check-suites?app_id=", api.call_args_list[1].args[0][-1])
        self.assertIn("filter=all", api.call_args_list[2].args[0][-1])
        return result

    def test_complete_canonical_receipt_without_legacy_fields(self):
        r = self.evaluate()
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["check_run_id"], 1)
        self.assertNotIn("run_url", r)

    def test_bare_family_marker_supported_without_ambiguous_duplicates(self):
        c = check()
        c["output"]["summary"] = c["output"]["summary"].replace(
            "<!-- MERGLBOT_PR_ASSISTANT_V6: true -->", "<!-- MERGLBOT_PR_ASSISTANT_V6 -->")
        self.assertTrue(self.evaluate([c])["ok"])
        c["output"]["summary"] += "\n<!-- MERGLBOT_PR_ASSISTANT_V6: true -->"
        self.assertFalse(self.evaluate([c])["ok"])

    def test_all_required_marker_values_fail_closed(self):
        for key in reader.parse_markers(check()["output"]["summary"]):
            with self.subTest(key=key):
                c = check()
                value = reader.parse_markers(c["output"]["summary"])[key]
                c["output"]["summary"] = c["output"]["summary"].replace(
                    f"<!-- {key}: {value} -->", f"<!-- {key}: unknown -->")
                self.assertFalse(self.evaluate([c])["ok"])

    def test_duplicate_even_identical_markers_rejected(self):
        c = check()
        c["output"]["summary"] += "\n<!-- MERGLBOT_ACTIONABLE_FINDINGS_COUNT: 0 -->"
        self.assertEqual(self.evaluate([c])["blockers"], ["duplicate_receipt_markers"])

    def test_produced_engine_allowlist_preserves_legitimate_single_engine(self):
        for evidence, expected in [
            ("codex:pass,claude:skipped", True),
            ("codex:skipped,claude:pass", True),
            ("codex:skipped,claude:skipped", False),
            ("codex:fail,claude:pass", False),
            ("codex:pass,claude:fail", False),
            ("codex:fail,claude:fail", False),
            ("codex:fail,claude:skipped", False),
            ("codex:cancelled,claude:pending", False),
            ("codex:timeout,claude:error", False),
            ("pass,:pass", False), ("fake:pass", False), ("", False),
        ]:
            with self.subTest(evidence=evidence):
                c = check()
                c["output"]["summary"] = c["output"]["summary"].replace(
                    "codex:pass,claude:pass", evidence)
                self.assertEqual(self.evaluate([c])["ok"], expected)

    def test_newest_round_cannot_be_hidden_by_old_approval(self):
        for status, conclusion in [("in_progress", None), ("completed", "failure")]:
            with self.subTest(status=status):
                newer = check(2)
                newer.update(status=status, conclusion=conclusion)
                r = self.evaluate([newer, check()])
                self.assertEqual(r["check_run_id"], 2)
                self.assertFalse(r["ok"])

    def test_older_pending_round_still_blocks_closeout(self):
        pending = check(1)
        pending.update(status="in_progress", conclusion=None)
        result = self.evaluate([pending, check(2)])
        self.assertFalse(result["ok"])
        self.assertIn("v6_review_in_progress", result["blockers"])

    def test_foreign_producer_or_name_is_not_accepted(self):
        for field, value in [("app", {"id": 123}), ("name", "Other check")]:
            c = check()
            c[field] = value
            self.assertFalse(self.evaluate([c])["ok"])

    def test_transport_head_and_marker_head_both_required(self):
        c = check()
        c["head_sha"] = "b" * 40
        self.assertFalse(self.evaluate([c])["ok"])
        self.assertEqual(self.evaluate(final_head="b" * 40)["blockers"],
                         ["head_changed_during_read"])
        self.assertTrue(caller.merglbot_pr_head_changed(
            self.evaluate(final_head="b" * 40), HEAD))

    def test_caller_does_not_retrigger_actual_v6_authority_hold(self):
        payload = {"ok": False, "head_sha": HEAD, "review_head_sha": HEAD,
                   "current_head_match": True, "status": "failure",
                   "verdict": "blocked_missing_authority",
                   "blockers": ["invalid_or_missing:MERGLBOT_REVIEW_VERDICT"]}
        with patch.object(caller, "verify_merglbot", return_value=payload), \
                patch.object(caller, "trigger_merglbot_review") as trigger:
            result = caller.wait_for_merglbot(REPO, 7, "test", HEAD, apply=True)
        self.assertFalse(result["ok"])
        trigger.assert_not_called()

    def test_complete_multiple_pages_and_latest_candidate(self):
        rows = [check(i) for i in range(1, 102)]
        r = self.evaluate(pages=[{"total_count": 101, "check_runs": rows[:100]},
                                 {"total_count": 101, "check_runs": rows[100:]}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["check_run_id"], 101)

    def test_incomplete_changing_duplicate_and_malformed_inventories(self):
        cases = [[], {}, [{"total_count": 2, "check_runs": [check()]}],
                 [{"total_count": 2, "check_runs": [check(), check()]}],
                 [{"total_count": 1, "check_runs": [check()]},
                  {"total_count": 2, "check_runs": []}],
                 [{"total_count": True, "check_runs": [check()]}],
                 [{"total_count": 1, "check_runs": [{}]}],
                 [{"total_count": 1, "check_runs": "not-list"}]]
        for pages in cases:
            with self.subTest(pages=pages):
                self.assertFalse(self.evaluate(pages=pages)["ok"])

    def test_suite_inventory_cannot_hide_newer_failure(self):
        suites = [{"id": i, "app": {"id": reader.V6_APP_ID}, "head_sha": HEAD}
                  for i in range(1, 1002)]
        pages = [{"total_count": len(suites), "check_suites": suites[i:i+100]}
                 for i in range(0, len(suites), 100)]
        pr = {"head": {"sha": HEAD}, "html_url": "https://github.com/example/repo/pull/7"}
        responses = [pr, pages]
        for suite in suites:
            c = check(suite["id"])
            if suite["id"] == 1001:
                c.update(status="in_progress", conclusion=None)
            responses.append([{"total_count": 1, "check_runs": [c]}])
        responses.extend(copy.deepcopy(responses[1:]))
        responses.extend([c, pr])
        with patch.object(reader, "gh_json", side_effect=responses):
            result = reader.verify(REPO, 7, "v6")
        self.assertFalse(result["ok"])
        self.assertEqual(result["check_run_id"], 1001)

    def test_foreign_or_incomplete_suite_inventory_blocks(self):
        pr = {"head": {"sha": HEAD}, "html_url": "https://github.com/example/repo/pull/7"}
        for suite in [{"id": 1, "head_sha": HEAD, "app": None},
                      {"id": 1, "head_sha": HEAD, "app": {"id": 123}}]:
            with patch.object(reader, "gh_json", side_effect=[pr, [{"total_count": 1, "check_suites": [suite]}]]):
                self.assertFalse(reader.verify(REPO, 7, "v6")["ok"])
        with patch.object(reader, "gh_json", side_effect=[pr, [{"total_count": 2, "check_suites": []}]]):
            self.assertFalse(reader.verify(REPO, 7, "v6")["ok"])

    def test_unavailable_api_does_not_expose_error_payload(self):
        with patch.object(reader, "gh_json", side_effect=RuntimeError("PRIVATE_SENTINEL")):
            r = reader.verify(REPO, 7, "v6")
        self.assertFalse(r["ok"])
        self.assertNotIn("PRIVATE_SENTINEL", str(r))

    def test_same_head_mutation_new_round_and_final_check_failure(self):
        # Two suites: the first receipt can mutate while a later suite is
        # collected without changing the head or any inventory denominator.
        for change in ("summary", "conclusion", "new_round", "late_selected", "unavailable"):
            with self.subTest(change=change):
                reads = 0
                current = check(2)
                calls = []
                def get(args):
                    nonlocal reads, current
                    path = args[-1]
                    calls.append(path)
                    if "/pulls/" in path:
                        return {"head": {"sha": HEAD}, "html_url": ""}
                    if "/check-suites?" in path:
                        reads += 1
                        suites = [{"id": i, "app": {"id": reader.V6_APP_ID}, "head_sha": HEAD} for i in (10, 20)]
                        return [{"total_count": 2, "check_suites": suites}]
                    if "/check-suites/10/" in path:
                        return [{"total_count": 1, "check_runs": [copy.deepcopy(current)]}]
                    if "/check-suites/20/" in path:
                        if change in ("summary", "late_selected"):
                            if change == "summary" or reads == 2:
                                current["output"]["summary"] = current["output"]["summary"].replace(
                                    "FINDINGS_COUNT: 0", "FINDINGS_COUNT: 1")
                        if change == "conclusion":
                            current["conclusion"] = "failure"
                        rows = [check(3)] if change == "new_round" and reads == 2 else []
                        return [{"total_count": len(rows), "check_runs": rows}]
                    if path.endswith("/check-runs/2"):
                        if change == "unavailable":
                            raise RuntimeError("PRIVATE_SENTINEL")
                        return copy.deepcopy(current)
                    raise AssertionError(path)
                with patch.object(reader, "gh_json", side_effect=get):
                    result = reader.verify(REPO, 7, "v6")
                self.assertFalse(result["ok"], result)
                self.assertNotIn("PRIVATE_SENTINEL", str(result))
                self.assertLessEqual(reads, 2)
                if change != "unavailable":
                    self.assertIn("v6_evidence_changed_during_read", result["blockers"])

    def test_no_receipt_or_truncated_summary_blocks(self):
        self.assertFalse(self.evaluate([])["ok"])
        for summary in [None, "", "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->"]:
            c = check()
            c["output"]["summary"] = summary
            self.assertFalse(self.evaluate([c])["ok"])


if __name__ == "__main__":
    unittest.main()
