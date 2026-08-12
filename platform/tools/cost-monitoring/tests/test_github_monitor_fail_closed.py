import pytest

from cost_monitoring.monitor import github_monitor


def test_collect_github_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="Missing GITHUB_TOKEN"):
        github_monitor.collect_github("example", ["example"], {})
