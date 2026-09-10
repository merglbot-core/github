import datetime as dt
import importlib.util
from pathlib import Path
import sys
import json
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/ci-pilot"))
import runtime
sys.path.pop(0)


class RuntimeTests(unittest.TestCase):
    def test_idle_error_retargets_five_minutes_without_losing_terminal_intent(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        for terminal in (False, True):
            plan = {"stopped": terminal, "next_due": None if terminal else (now + dt.timedelta(minutes=15)).isoformat()}
            with patch.object(runtime.heartbeat, "sync", side_effect=[{"status": "unverified"}, {"status": "pending"}]) as sync:
                self.assertFalse(runtime.sync_heartbeat(Path("/unused"), plan, now))
                self.assertEqual([call.args[1:3] for call in sync.call_args_list], [(False, terminal), (True, terminal)])
                self.assertFalse(plan["stopped"])
                self.assertEqual(runtime.instant(plan["next_due"]) - now, dt.timedelta(minutes=5))

    def test_terminal_waits_for_heartbeat_stop_readback_before_unload(self):
        now = runtime.dt.datetime.now(runtime.dt.timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime.atomic(root / "state.json", {"counted_prs": [str(i) for i in range(5)]})
            result = {"action": "cleanup_verified", "reason": "case_limit", "status": "inactive",
                      "history": {"unfinished_runs": 0, "data_gaps": 0}}
            def controller(path):
                runtime.atomic(path / "next_action.json", result)
            with patch.object(runtime, "run_controller", side_effect=controller), patch.object(runtime, "unload", return_value=0) as unload:
                with patch.object(runtime.heartbeat, "sync", side_effect=[{"status": "pending"}, {"status": "verified", "paused": True}]):
                    self.assertEqual(runtime.wake(root, now), 1)
                    self.assertFalse(json.loads((root / "runtime.json").read_text())["stopped"])
                    unload.assert_not_called()
                    self.assertEqual(runtime.wake(root, now), 0)
                    self.assertTrue(json.loads((root / "runtime.json").read_text())["stopped"])
                    unload.assert_called_once()

    def test_active_idle_and_unverified_cadence(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        for status, seconds in (("active", 300), ("inactive", 900), ("unverified", 300)):
            result = runtime.schedule({"status": status}, now)
            self.assertEqual(runtime.instant(result["next_due"]) - now, dt.timedelta(seconds=seconds))
            self.assertFalse(result["stopped"])

    def test_terminal_requires_proven_cleanup_and_records_unfinished_gap(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        for action, stopped in (("cleanup_required", False), ("cleanup_verified", True)):
            result = runtime.schedule({"action": action, "reason": "deadline",
                                       "history": {"unfinished_runs": 1}}, now)
            self.assertEqual(result["stopped"], stopped)
            self.assertEqual(result["admitted_runs"], "DATA_GAP")

    def test_missing_history_and_unverified_are_never_observed_complete(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        for result in ({"status": "unverified"}, {"status": "inactive"},
                       {"status": "unverified", "history": {"unfinished_runs": 0, "data_gaps": 0}}):
            self.assertEqual(runtime.schedule(result, now)["admitted_runs"], "DATA_GAP")

    def test_timeout_and_null_counter_attempt_independent_cleanup(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text(json.dumps({"counted_prs": None}))
            (root / "runtime.json").write_text(json.dumps({"next_due": "2026-09-11T20:00:00Z"}))
            with patch.object(runtime, "run_controller", side_effect=subprocess.TimeoutExpired("controller", 240)) as runner:
                with patch.object(runtime, "emergency_cleanup", return_value={"action": "cleanup_required", "status": "unverified"}) as cleanup:
                    self.assertEqual(runtime.wake(root, now), 0)
                    runner.assert_not_called()
                    cleanup.assert_called_once_with(root)
            plan = json.loads((root / "runtime.json").read_text())
            self.assertFalse(plan["stopped"])
            self.assertEqual(plan["admitted_runs"], "DATA_GAP")
            self.assertEqual(runtime.instant(plan["next_due"]) - now, dt.timedelta(minutes=5))

    def test_corrupt_state_cleanup_does_not_hide_recovery(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text("invalid fixture")
            with patch.object(runtime, "emergency_cleanup", return_value={"action": "cleanup_verified", "status": "inactive"}), patch.object(runtime, "unload") as unload:
                for _ in range(2):
                    runtime.wake(root, now)
                    result = json.loads((root / "next_action.json").read_text())
                    self.assertEqual((result["action"], result["status"]), ("recovery_required", "unverified"))
                    self.assertTrue(result["cleanup_verified"])
                    self.assertEqual((root / "state.json").read_text(), "invalid fixture")
                    self.assertEqual(runtime.instant(json.loads((root / "runtime.json").read_text())["next_due"]) - now, dt.timedelta(minutes=5))
                unload.assert_not_called()

    def test_non_boolean_stopped_and_timeout_cannot_unload_without_cleanup(self):
        now = runtime.instant("2026-09-10T20:00:00Z")
        for stopped in ("false", 1, None, [], {}, False):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "runtime.json").write_text(json.dumps({"stopped": stopped}))
                (root / "state.json").write_text(json.dumps({"receipt": {"head": "a" * 40}}))
                with patch.object(runtime, "run_controller", side_effect=subprocess.TimeoutExpired("controller", 240)), patch.object(runtime, "unload") as unload, patch.object(runtime, "emergency_cleanup", return_value={"action": "cleanup_required", "status": "unverified"}) as cleanup:
                    self.assertEqual(runtime.wake(root, now), 0)
                    cleanup.assert_called_once_with(root)
                    unload.assert_not_called()
                self.assertFalse(json.loads((root / "runtime.json").read_text())["stopped"])

    def test_emergency_cleanup_uses_separate_cleanup_command(self):
        output = {"action": "cleanup_verified", "status": "inactive"}
        process = MagicMock(returncode=0)
        process.communicate.return_value = (json.dumps(output), None)
        with patch.object(runtime.subprocess, "Popen", return_value=process) as run:
            self.assertEqual(runtime.emergency_cleanup(Path("/tmp/test-pilot")), output)
            self.assertIn("cleanup", run.call_args.args[0])
            self.assertNotIn("tick", run.call_args.args[0])
            self.assertTrue(run.call_args.kwargs["start_new_session"])

    def test_emergency_timeout_kills_process_group(self):
        process = MagicMock(pid=12345)
        process.communicate.side_effect = subprocess.TimeoutExpired("cleanup", 240)
        with patch.object(runtime.subprocess, "Popen", return_value=process):
            with patch.object(runtime.os, "killpg") as kill:
                result = runtime.emergency_cleanup(Path("/tmp/test-pilot"))
                kill.assert_called_once_with(12345, runtime.signal.SIGKILL)
                process.wait.assert_called_once()
                self.assertEqual(result["status"], "unverified")

    def test_cleanup_does_not_mutate_while_controller_lock_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / "controller.lock").open("a") as lock:
                runtime.fcntl.flock(lock, runtime.fcntl.LOCK_EX | runtime.fcntl.LOCK_NB)
                with patch.object(runtime, "cleanup") as cleanup:
                    self.assertFalse(runtime.locked_cleanup(root))
                    cleanup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
