import pytest

from cost_monitoring.monitor import github_monitor


def test_collect_github_requires_token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "run-token-must-not-be-used-for-enterprise-reads")
    monkeypatch.delenv("ENTERPRISE_GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="Missing ENTERPRISE_GITHUB_TOKEN"):
        github_monitor.collect_github("example", ["example"], {})


def test_enterprise_headers_do_not_use_run_token(monkeypatch):
    monkeypatch.setenv("ENTERPRISE_GITHUB_TOKEN", "enterprise-read-token")
    monkeypatch.setenv("GITHUB_TOKEN", "run-token-for-issues-only")

    headers = github_monitor._headers()

    assert headers["Authorization"] == "Bearer enterprise-read-token"
    assert "run-token-for-issues-only" not in headers["Authorization"]
