import pytest

from cost_monitoring.alerting.thresholds import evaluate_gcp_thresholds
from cost_monitoring.report.writers import prepare_csv_rows, write_markdown


def sample_data():
    return {
        "month": "2026-08",
        "github": {
            "total_monthly_cost_usd": 100.0,
            "copilot": {"monthly_cost_usd": 100.0},
            "enterprise_cloud": {"monthly_cost_usd": 0.0},
            "total_members": None,
            "member_census_status": "not_collected_unpriced",
        },
        "gcp": {
            "currency": "CZK",
            "projects_monitored": 1,
            "total_cost": 2500.0,
            "total_credits": -500.0,
            "total_net": 2000.0,
            "project_costs": [
                {
                    "project_id": "project-a",
                    "currency": "CZK",
                    "total_net": 2000.0,
                    "services": [{"service": "BigQuery", "currency": "CZK", "net_cost": 2000.0}],
                }
            ],
            "budgets": [],
        },
        "alerts": [],
    }


def test_markdown_keeps_github_usd_and_gcp_czk_separate(tmp_path):
    output = tmp_path / "report.md"
    write_markdown(str(output), sample_data())
    report = output.read_text(encoding="utf-8")

    assert "**GitHub Costs**: $100.00" in report
    assert "**GCP Costs**: 2,000.00 CZK" in report
    assert "**Cross-currency total**: not calculated" in report
    assert "Total Monthly Cost" not in report
    assert "Not collected (Enterprise Cloud seat price is not configured)." in report


def test_csv_propagates_source_currencies():
    rows = prepare_csv_rows(sample_data())
    currencies = {(row["source"], row["currency"]) for row in rows if row["metric"] != "count"}

    assert ("github", "USD") in currencies
    assert ("gcp", "CZK") in currencies


def test_gcp_threshold_currency_mismatch_fails_closed():
    gcp_data = sample_data()["gcp"]
    thresholds = {"gcp": {"currency": "USD", "defaults": {"total_monthly": 10}}}

    with pytest.raises(ValueError, match="currency mismatch"):
        evaluate_gcp_thresholds(gcp_data, thresholds)


def test_gcp_threshold_alert_carries_currency():
    gcp_data = sample_data()["gcp"]
    thresholds = {"gcp": {"currency": "CZK", "defaults": {"total_monthly": 1000}}}

    alerts = evaluate_gcp_thresholds(gcp_data, thresholds)

    assert alerts == [
        {
            "scope": "gcp",
            "project": "project-a",
            "type": "total_cost",
            "currency": "CZK",
            "value": 2000.0,
            "threshold": 1000,
            "severity": "high",
        }
    ]
