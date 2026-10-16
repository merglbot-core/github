import pytest


from cost_monitoring.utils.anomaly_config import parse_daily_spike_factor


def test_daily_spike_pct_fraction() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 0.5}) == pytest.approx(1.5)


def test_daily_spike_pct_fraction_one() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 1.0}) == pytest.approx(2.0)


def test_daily_spike_pct_integer_percent() -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 50}) == pytest.approx(1.5)
    assert parse_daily_spike_factor({"daily_spike_pct": 150}) == pytest.approx(2.5)


def test_daily_spike_pct_invalid_warns_and_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    assert parse_daily_spike_factor({"daily_spike_pct": 1.01}) == pytest.approx(2.0)
    captured = capsys.readouterr()
    assert "WARN:" in captured.err


def test_daily_spike_factor_direct() -> None:
    assert parse_daily_spike_factor({"daily_spike_factor": 3.0}) == pytest.approx(3.0)


def test_alert_details_are_retained_without_slack(monkeypatch, tmp_path):
    # merglbot-core/infra#3826: Slack is retired, so a triggered alert must leave
    # its details in the uploaded report dir and the step summary.
    from cost_monitoring import ai_usage_alerts as m

    alert = {
        "type": "ai_usage_budget",
        "provider": "anthropic",
        "product": "claude_code",
        "mtd_cost_usd": 950.0,
        "monthly_budget_usd": 1000.0,
        "used_pct": 0.95,
        "projected_pct": 1.4,
        "projected_month_usd": 1400.0,
    }
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("AI_USAGE_REPORT_DIR", str(tmp_path / "reports"))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(m, "load_budget_config", lambda _p: {"budgets": []})
    monkeypatch.setattr(m, "get_mtd_spend", lambda *_a, **_k: [])
    monkeypatch.setattr(m, "get_recent_daily_spend", lambda *_a, **_k: [])
    monkeypatch.setattr(m, "evaluate_anomaly_alerts", lambda *_a, **_k: [alert])
    sent = []
    monkeypatch.setattr(m, "send_slack", lambda *a, **k: sent.append(a))

    assert m.main() == 2
    assert sent == []
    report = (tmp_path / "reports" / "ai_usage_alerts.md").read_text(encoding="utf-8")
    assert "anthropic / claude_code" in report
    assert "950.00" in report
    assert "anthropic / claude_code" in summary.read_text(encoding="utf-8")


def _patch_sources(monkeypatch, m, alerts):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(m, "load_budget_config", lambda _p: {"budgets": []})
    monkeypatch.setattr(m, "get_mtd_spend", lambda *_a, **_k: [])
    monkeypatch.setattr(m, "get_recent_daily_spend", lambda *_a, **_k: [])
    monkeypatch.setattr(m, "evaluate_anomaly_alerts", lambda *_a, **_k: alerts)


def test_retained_report_keeps_every_alert_beyond_twenty(monkeypatch, tmp_path):
    from cost_monitoring import ai_usage_alerts as m

    alerts = [
        {"type": "ai_usage_anomaly", "provider": f"provider-{i:02d}", "yesterday_cost_usd": 10.0 + i,
         "avg_prev7_cost_usd": 1.0, "spike_factor": 3}
        for i in range(25)
    ]
    _patch_sources(monkeypatch, m, alerts)
    monkeypatch.setenv("AI_USAGE_REPORT_DIR", str(tmp_path / "reports"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert m.main() == 2
    report = (tmp_path / "reports" / "ai_usage_alerts.md").read_text(encoding="utf-8")
    assert "provider-00" in report and "provider-24" in report
    assert report.count("anomaly spike") == 25


def test_alerts_without_any_retained_destination_fail_visibly(monkeypatch, tmp_path):
    from cost_monitoring import ai_usage_alerts as m

    alert = {"type": "ai_usage_anomaly", "provider": "p", "yesterday_cost_usd": 9.0,
             "avg_prev7_cost_usd": 1.0, "spike_factor": 3}
    _patch_sources(monkeypatch, m, [alert])
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("AI_USAGE_REPORT_DIR", str(blocker / "reports"))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert m.main() == 3
