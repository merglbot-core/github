"""No real credentials or network requests are used by this suite."""

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ACTION = Path(__file__).resolve().parents[1] / "actions/github-auth-observer"
SPEC = importlib.util.spec_from_file_location(
    "github_auth_observer", ACTION / "observer.py"
)
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def caller():
    return {
        "GH_TOKEN": "synthetic-observer-credential",
        "GITHUB_ACTIONS": "true",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "schedule",
    }


def rates(remaining=4999):
    return {
        key: {
            "limit": 5000,
            "remaining": remaining,
            "used": 5000 - remaining,
            "reset": 2000000000,
        }
        for key in ("core", "graphql")
    }


class AuthObserverTests(unittest.TestCase):
    def test_only_closed_get_endpoints_and_child_environment_are_used(self):
        source = caller()
        source.update(
            {
                "GH_DEBUG": "api",
                "HTTPS_PROXY": "untrusted",
                "HOME": "untrusted",
                "GH_ENTERPRISE_TOKEN": "untrusted",
                "GH_HOST": "untrusted",
            }
        )
        replies = [
            subprocess.CompletedProcess([], 0, json.dumps(rates()).encode(), b""),
            subprocess.CompletedProcess(
                [], 1, b"", b"private provider text (HTTP 403)"
            ),
            subprocess.CompletedProcess([], 0, b'{"total_count":2}', b""),
        ]

        def respond(*args, **kwargs):
            self.assertEqual(list(Path(kwargs["env"]["GH_CONFIG_DIR"]).iterdir()), [])
            return replies.pop(0)

        with patch.object(observer.subprocess, "run", side_effect=respond) as run:
            report = observer.observe(source)
            self.assertEqual(run.call_count, 3)
            for index, call in enumerate(run.call_args_list):
                args = call.args[0]
                self.assertEqual(
                    args[:7],
                    [
                        "/usr/bin/gh",
                        "api",
                        "--hostname",
                        "github.com",
                        "--method",
                        "GET",
                        "--jq",
                    ],
                )
                self.assertEqual(args[-1], list(observer.QUERIES)[index])
                self.assertNotIn(source["GH_TOKEN"], args)
                env = call.kwargs["env"]
                self.assertEqual(
                    set(env),
                    {
                        "GH_TOKEN",
                        "GH_HOST",
                        "GH_CONFIG_DIR",
                        "GH_PROMPT_DISABLED",
                        "GH_NO_UPDATE_NOTIFIER",
                        "GH_TELEMETRY",
                        "GH_PAGER",
                        "PATH",
                        "LANG",
                    },
                )
                self.assertEqual(env["GH_TOKEN"], source["GH_TOKEN"])
                self.assertEqual(env["GH_NO_UPDATE_NOTIFIER"], "1")
                self.assertEqual(env["GH_TELEMETRY"], "0")
                self.assertEqual(call.kwargs["timeout"], 15)
            config = env["GH_CONFIG_DIR"]
        self.assertFalse(Path(config).exists())
        self.assertEqual(report["identity"], "installation_endpoint_accepted")
        self.assertIsNone(report["installation_id"])
        self.assertFalse(report["credential_selection_proven"])
        self.assertFalse(report["migration_accepted"])
        serialized = json.dumps(report)
        for forbidden in (
            source["GH_TOKEN"],
            "private provider text",
            "total_count",
            "untrusted",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_user_context_is_not_pat_or_account_identity_proof(self):
        with patch.object(
            observer,
            "request",
            side_effect=[
                (200, rates()),
                (200, {"id": 9123456, "type": "User", "login": "private-login"}),
            ],
        ) as get:
            report = observer.observe(caller())
        self.assertEqual(get.call_count, 2)
        self.assertEqual(report["identity"], "user_context")
        self.assertNotIn("9123456", json.dumps(report))
        self.assertNotIn("private-login", json.dumps(report))
        self.assertFalse(report["migration_accepted"])

    def test_ambient_or_absent_credential_never_falls_back(self):
        for change in (
            {"GH_TOKEN": ""},
            {"GH_TOKEN": "   "},
            {"GITHUB_TOKEN": "synthetic-second-source"},
        ):
            with self.subTest(change=change), patch.object(observer, "request") as get:
                report = observer.observe(caller() | change)
                self.assertEqual(
                    report["reason"], "explicit_unambiguous_gh_token_required"
                )
                get.assert_not_called()

    def test_untrusted_actions_context_does_not_probe(self):
        for change in (
            {"GITHUB_ACTIONS": "false"},
            {"GITHUB_SERVER_URL": "https://other.invalid"},
            {"GITHUB_REF": "refs/pull/12/merge"},
            {"GITHUB_EVENT_NAME": "pull_request_target"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "workflow_run"},
        ):
            with self.subTest(change=change), patch.object(observer, "request") as get:
                report = observer.observe(caller() | change)
                self.assertEqual(report["reason"], "untrusted_caller_context")
                get.assert_not_called()

    def test_low_quota_invalid_counters_and_missing_rates_stop_before_identity(self):
        invalid = [rates(2002), {}, {"core": rates()["core"]}]
        for field, value in (
            ("remaining", True),
            ("remaining", 5001),
            ("used", -1),
            ("reset", "tomorrow"),
            ("limit", None),
        ):
            mutated = rates()
            mutated["core"][field] = value
            invalid.append(mutated)
        for value in invalid:
            with self.subTest(value=value), patch.object(
                observer, "request", return_value=(200, value)
            ) as get:
                report = observer.observe(caller())
                self.assertEqual(report["outcome"], "inconclusive")
                self.assertEqual(get.call_count, 1)
        with patch.object(
            observer,
            "request",
            side_effect=[(200, rates(2003)), (200, {"id": 1, "type": "Bot"})],
        ):
            self.assertEqual(observer.observe(caller())["outcome"], "observed")

    def test_only_forbidden_user_endpoint_permits_installation_probe(self):
        for status in (None, 401, 404, 429, 500):
            with self.subTest(status=status), patch.object(
                observer, "request", side_effect=[(200, rates()), (status, None)]
            ) as get:
                report = observer.observe(caller())
                self.assertEqual(get.call_count, 2)
                self.assertEqual(report["identity"], "unknown")
        for status, response in (
            (403, None),
            (200, {"total_count": True}),
            (200, {"total_count": -1}),
            (200, {"total_count": "2"}),
        ):
            with self.subTest(status=status, response=response), patch.object(
                observer,
                "request",
                side_effect=[(200, rates()), (403, None), (status, response)],
            ) as get:
                report = observer.observe(caller())
                self.assertEqual(get.call_count, 3)
                self.assertEqual(report["identity"], "unknown")

    def test_malformed_success_is_not_identity(self):
        for body in (
            None,
            [],
            {"id": True, "type": "User"},
            {"id": 1, "type": "Organization"},
            {},
        ):
            with self.subTest(body=body), patch.object(
                observer, "request", side_effect=[(200, rates()), (200, body)]
            ):
                self.assertEqual(observer.observe(caller())["outcome"], "inconclusive")

    def test_provider_failures_are_redacted_and_not_retried(self):
        failures = [
            OSError("private secret detail"),
            subprocess.TimeoutExpired("secret argv", 15),
            subprocess.CompletedProcess(
                [], 1, b"private body", b"private detail (HTTP 429)"
            ),
            subprocess.CompletedProcess([], 0, b"not JSON secret", b""),
            subprocess.CompletedProcess([], 0, b"x" * 16385, b""),
        ]
        for failure in failures:
            with self.subTest(kind=type(failure).__name__):
                kwargs = (
                    {"side_effect": failure}
                    if isinstance(failure, Exception)
                    else {"return_value": failure}
                )
                with patch.object(observer.subprocess, "run", **kwargs) as run:
                    report = observer.observe(caller())
                self.assertEqual(run.call_count, 1)
                self.assertEqual(report["outcome"], "inconclusive")
                for forbidden in ("private", "secret", "argv", "body"):
                    self.assertNotIn(forbidden, json.dumps(report))

    def test_unknown_endpoint_rejected_before_cli(self):
        with patch.object(observer.subprocess, "run") as run:
            for endpoint in (
                "repos/other/repo",
                "https://other.invalid",
                "user -X POST",
            ):
                with self.assertRaises(ValueError):
                    observer.request(endpoint, {})
            run.assert_not_called()

    def test_actual_composite_wrapper_retains_failure_without_logging_report(self):
        with tempfile.TemporaryDirectory(prefix="observer test ") as directory:
            root = Path(directory)
            outputs = root / "outputs"
            env = {
                "PATH": os.environ["PATH"],
                "RUNNER_TEMP": str(root),
                "GITHUB_OUTPUT": str(outputs),
                "PROJECT39_AUTH_OBSERVER_PATH": str(ACTION),
            }
            result = subprocess.run(
                ["bash", str(ACTION / "collect.sh")],
                env=env,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")
            key, report_path = outputs.read_text().strip().split("=", 1)
            self.assertEqual(key, "report_path")
            path = Path(report_path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                json.loads(path.read_text())["reason"], "untrusted_caller_context"
            )

    def test_output_newline_injection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = root / "outputs"
            env = {
                "PATH": os.environ["PATH"],
                "RUNNER_TEMP": str(root) + "\nforged=true",
                "GITHUB_OUTPUT": str(outputs),
                "PROJECT39_AUTH_OBSERVER_PATH": str(ACTION),
            }
            result = subprocess.run(
                ["bash", str(ACTION / "collect.sh")],
                env=env,
                capture_output=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse(outputs.exists())
