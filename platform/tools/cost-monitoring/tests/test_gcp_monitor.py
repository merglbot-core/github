from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cost_monitoring.monitor import gcp_monitor


class FakeQueryJob:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class FakeBigQueryClient:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error
        self.sql = None
        self.job_config = None

    def query(self, sql, job_config):
        self.sql = sql
        self.job_config = job_config
        if self.error:
            raise self.error
        return FakeQueryJob(self.rows)


def billing_row(*, project="project-a", service="BigQuery", currency="CZK", cost=100.0, credits=-25.0):
    return SimpleNamespace(
        project_id=project,
        service=service,
        currency=currency,
        cost=cost,
        credits=credits,
    )


def test_query_is_partition_and_usage_bounded_with_five_gib_guard():
    client = FakeBigQueryClient([billing_row()])

    result = gcp_monitor.query_month_costs_by_service(
        client,
        "merglbot-platform-prd.billing_export_euw1.gcp_billing_export_v1_*",
        ["project-a"],
        "2026-08",
    )

    assert "_PARTITIONTIME >= @month_start" in client.sql
    assert "_PARTITIONTIME < @month_end" in client.sql
    assert "usage_start_time >= @month_start" in client.sql
    assert "usage_start_time < @month_end" in client.sql
    assert client.job_config.maximum_bytes_billed == 5 * 1024 * 1024 * 1024
    assert result[0]["total_cost"] == pytest.approx(100.0)
    assert result[0]["total_credits"] == pytest.approx(-25.0)
    assert result[0]["total_net"] == pytest.approx(75.0)
    assert result[0]["currency"] == "CZK"


def test_query_rejects_mixed_currencies():
    client = FakeBigQueryClient([billing_row(currency="CZK"), billing_row(project="project-b", currency="USD")])

    with pytest.raises(gcp_monitor.CostDataError, match="Expected one billing export currency"):
        gcp_monitor.query_month_costs_by_service(
            client,
            "billing-project.billing_export_euw1.gcp_billing_export_v1_*",
            ["project-a", "project-b"],
            "2026-08",
        )


def test_query_rejects_empty_export_result():
    client = FakeBigQueryClient([])

    with pytest.raises(gcp_monitor.CostDataError, match="returned no rows"):
        gcp_monitor.query_month_costs_by_service(
            client,
            "billing-project.billing_export_euw1.gcp_billing_export_v1_*",
            ["project-a"],
            "2026-08",
        )


def test_query_failure_is_sanitized_and_propagated():
    client = FakeBigQueryClient(error=RuntimeError("provider payload must not escape"))

    with pytest.raises(gcp_monitor.CostDataError, match="BigQuery billing query failed") as exc_info:
        gcp_monitor.query_month_costs_by_service(
            client,
            "billing-project.billing_export_euw1.gcp_billing_export_v1_*",
            ["project-a"],
            "2026-08",
        )

    assert "provider payload" not in str(exc_info.value)


def test_collect_gcp_fails_on_configured_currency_mismatch(monkeypatch):
    monkeypatch.setattr(gcp_monitor.bigquery, "Client", Mock(return_value=object()))
    monkeypatch.setattr(
        gcp_monitor,
        "query_month_costs_by_service",
        Mock(
            return_value=[
                {
                    "project_id": "project-a",
                    "currency": "USD",
                    "services": [],
                    "total_cost": 1.0,
                    "total_credits": 0.0,
                    "total_net": 1.0,
                }
            ]
        ),
    )
    config = {
        "billing_export": {"project_id": "billing-project", "currency": "CZK"},
        "projects": {"core": ["project-a"]},
    }

    with pytest.raises(gcp_monitor.CostDataError, match="currency mismatch"):
        gcp_monitor.collect_gcp(config, "2026-08")
