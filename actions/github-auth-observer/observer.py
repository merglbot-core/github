"""Bounded diagnostic of an explicitly supplied credential, never migration proof."""

import datetime
import json
import os
import re
import subprocess
import tempfile

QUERIES = {
    "rate_limit": "{core: .resources.core, graphql: .resources.graphql}",
    "user": "{id: .id, type: .type}",
    "installation/repositories?per_page=1": "{total_count: .total_count}",
}
RESERVE = 2000


def counter(value):
    return type(value) is int and 0 <= value <= 10**12


def budget(value):
    if not isinstance(value, dict):
        return None
    fields = ("limit", "remaining", "used", "reset")
    if not all(counter(value.get(key)) for key in fields):
        return None
    if value["remaining"] > value["limit"] or value["used"] > value["limit"]:
        return None
    return {key: value[key] for key in fields}


def request(endpoint, child_env):
    """No caller-controlled host, path, headers, CLI options, retry or fallback."""
    if endpoint not in QUERIES:
        raise ValueError("endpoint_not_allowed")
    command = [
        "/usr/bin/gh",
        "api",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "--jq",
        QUERIES[endpoint],
        endpoint,
    ]
    try:
        result = subprocess.run(
            command,
            env=child_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode:
        # Discard provider text; only retain the CLI's numeric HTTP error class.
        found = re.findall(rb"\(HTTP ([1-5][0-9]{2})\)", result.stderr[-4096:])
        return (int(found[-1]) if found else None), None
    if len(result.stdout) > 16384:
        return None, None
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        return None, None
    return 200, value


def observe(source):
    report = {
        "schema_version": 1,
        "kind": "github_auth_diagnostic",
        "observed_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "outcome": "inconclusive",
        "identity": "unknown",
        "installation_id": None,
        "credential_selection_proven": False,
        "migration_accepted": False,
        "requests": [],
    }
    if not (
        source.get("GITHUB_ACTIONS") == "true"
        and source.get("GITHUB_SERVER_URL") == "https://github.com"
        and source.get("GITHUB_REF") == "refs/heads/main"
        and source.get("GITHUB_EVENT_NAME") in {"push", "schedule", "workflow_dispatch"}
    ):
        report["reason"] = "untrusted_caller_context"
        return report
    supplied = source.get("GH_TOKEN")
    if (
        not isinstance(supplied, str)
        or not supplied.strip()
        or source.get("GITHUB_TOKEN")
    ):
        report["reason"] = "explicit_unambiguous_gh_token_required"
        return report

    # The child cannot consult ambient CLI config, a second token, debug settings,
    # proxy variables, git credential helpers, or an enterprise host.
    with tempfile.TemporaryDirectory(prefix="github-auth-observer-") as config:
        child_env = {
            "GH_TOKEN": supplied,
            "GH_HOST": "github.com",
            "GH_CONFIG_DIR": config,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_TELEMETRY": "0",
            "GH_PAGER": "cat",
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
        }

        def get(endpoint):
            status, value = request(endpoint, child_env)
            report["requests"].append({"endpoint": endpoint, "status": status})
            return status, value

        status, value = get("rate_limit")
        core = budget(value.get("core")) if isinstance(value, dict) else None
        graphql = budget(value.get("graphql")) if isinstance(value, dict) else None
        if status != 200 or core is None or graphql is None:
            report["reason"] = "rate_budget_unavailable"
            return report
        report["budget_snapshot"] = {"core": core, "graphql": graphql}
        if core["remaining"] < RESERVE + 3:
            report["reason"] = "shared_quota_reserve"
            return report
        status, value = get("user")
        if status == 200:
            if (
                isinstance(value, dict)
                and counter(value.get("id"))
                and value["id"] > 0
                and value.get("type") in {"User", "Bot"}
            ):
                report.update(
                    outcome="observed",
                    identity="user_context",
                    reason="authenticated_user_endpoint_accepted",
                )
            else:
                report["reason"] = "user_response_invalid"
            return report
        if status != 403:
            report["reason"] = "user_endpoint_unavailable"
            return report
        status, value = get("installation/repositories?per_page=1")
        if (
            status == 200
            and isinstance(value, dict)
            and counter(value.get("total_count"))
        ):
            report.update(
                outcome="observed",
                identity="installation_endpoint_accepted",
                reason="installation_identity_requires_external_binding",
            )
        else:
            report["reason"] = "installation_endpoint_unavailable"
        return report


def main():
    report = observe(os.environ)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["outcome"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
