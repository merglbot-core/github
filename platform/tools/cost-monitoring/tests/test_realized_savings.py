from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from cost_monitoring import realized_savings


def action(**overrides):
    values = {
        "action_id": "test-action",
        "cutoff": datetime(2026, 1, 31, tzinfo=UTC),
        "project_ids": ("project-a",),
        "services": ("BigQuery",),
        "sku_contains": (),
        "billing_lag_days": 3,
        "mismatch_tolerance_percent": 5.0,
        "mismatch_tolerance_absolute": 1.0,
        "requires_receipted_expansion": False,
        "expansion_receipt_sha256": None,
        "parent_scenario": None,
    }
    values.update(overrides)
    return realized_savings.Action(**values)


def rows(*values):
    return [
        {"action_id": "test-action", "window": window, "currency": currency, "net_cost": amount}
        for window, currency, amount in values
    ]


def health(**overrides):
    values = {
        "latest_usage_date": date(2026, 3, 5),
        "latest_partition_date": date(2026, 3, 5),
        "missing_partition_days": 0,
    }
    values.update(overrides)
    return values


def eligible_time(item):
    return item.eligible_at + timedelta(seconds=1)


def test_billing_lag_is_not_eligible_and_does_not_infer_savings():
    item = action()
    result = realized_savings.classify_action(item, [], {}, item.post_end + timedelta(days=2))
    assert result["state"] == "NOT_ELIGIBLE_YET"
    assert result["amounts_by_currency"] == {}


def test_incomplete_equal_window_is_data_gap():
    item = action()
    result = realized_savings.classify_action(item, rows(("pre", "CZK", 100)), health(), eligible_time(item))
    assert result["state"] == "DATA_GAP"
    assert result["reason"] == "incomplete_equal_window_aggregates"


def test_missing_partitions_are_data_gap():
    item = action()
    result = realized_savings.classify_action(
        item,
        rows(("pre", "CZK", 100), ("post", "CZK", 50)),
        health(missing_partition_days=1),
        eligible_time(item),
    )
    assert result["state"] == "DATA_GAP"
    assert result["reason"] == "missing_post_window_partitions"


def test_negative_credits_are_reflected_in_net_savings():
    item = action()
    # Aggregate query semantics are cost + signed credits: 100-20 vs 50-10.
    result = realized_savings.classify_action(
        item,
        rows(("pre", "CZK", 80), ("post", "CZK", 40)),
        health(),
        eligible_time(item),
    )
    assert result["state"] == "REALIZED"
    assert result["amounts_by_currency"]["CZK"]["realized_savings"] == 40


def test_mixed_currencies_remain_separate_and_have_no_portfolio_total(tmp_path):
    item = action()
    result = realized_savings.classify_action(
        item,
        rows(
            ("pre", "CZK", 100),
            ("post", "CZK", 50),
            ("pre", "USD", 20),
            ("post", "USD", 10),
        ),
        health(),
        eligible_time(item),
    )
    assert result["state"] == "REALIZED"
    assert set(result["amounts_by_currency"]) == {"CZK", "USD"}


def test_no_material_reduction_is_mismatch():
    item = action()
    result = realized_savings.classify_action(
        item,
        rows(("pre", "CZK", 100), ("post", "CZK", 99)),
        health(),
        eligible_time(item),
    )
    assert result["state"] == "MISMATCH"


def test_secret_is_ineligible_without_receipted_expansion():
    item = action(cutoff=None, requires_receipted_expansion=True)
    result = realized_savings.classify_action(item, [], {}, datetime(2030, 1, 1, tzinfo=UTC))
    assert result["state"] == "NOT_ELIGIBLE_YET"
    assert result["reason"] == "receipted_expansion_not_available"


def test_query_has_exact_source_partition_usage_and_five_gib_guards():
    sql, parameters = realized_savings._query_sql([action()])
    names = {parameter.name for parameter in parameters}
    assert f"`{realized_savings.CANONICAL_TABLE}`" in sql
    assert "_PARTITIONTIME >= @earliest_usage" in sql
    assert "_PARTITIONTIME < @partition_end" in sql
    assert "usage_start_time >= @earliest_usage" in sql
    assert "usage_start_time < @latest_usage" in sql
    assert "cost + IFNULL" in sql
    assert " AS window" not in sql
    assert "STRPOS(LOWER(sku), LOWER(needle))" in sql
    assert {"earliest_usage", "latest_usage", "partition_end"} <= names


class DryRunClient:
    def __init__(self, processed):
        self.processed = processed
        self.sql = None
        self.config = None

    def query(self, sql, job_config):
        self.sql = sql
        self.config = job_config
        return SimpleNamespace(total_bytes_processed=self.processed)


def test_bounded_query_dry_run_reads_no_rows():
    client = DryRunClient(1234)
    result_rows, result_health, processed = realized_savings.query_aggregates(client, [action()], query_dry_run=True)
    assert result_rows == []
    assert result_health == {}
    assert processed == 1234
    assert client.config.dry_run is True
    assert client.config.maximum_bytes_billed == 5 * 1024 * 1024 * 1024


def test_query_dry_run_fails_if_provider_estimate_exceeds_cap():
    client = DryRunClient(realized_savings.MAXIMUM_BYTES_BILLED + 1)
    with pytest.raises(realized_savings.RealizedSavingsError, match="exceeds the 5 GiB"):
        realized_savings.query_aggregates(client, [action()], query_dry_run=True)


def test_versioned_config_keeps_secret_unstarted_and_actions_independent():
    config = Path(__file__).resolve().parents[1] / "config/realized-savings.yml"
    actions, digest = realized_savings.load_actions(config)
    assert len(digest) == 64
    assert {item.action_id for item in actions} >= {
        "gcs_cerano",
        "gcs_denatura",
        "bq07_monitor_pause",
        "run01_v1_pause",
        "orphan_cloud_armor",
        "legacy_admin_lb",
        "ruzovyslon_offboarding",
        "secret_retention_full_expansion",
    }
    secret = next(item for item in actions if item.action_id == "secret_retention_full_expansion")
    assert secret.cutoff is None
    assert secret.expansion_receipt_sha256 is None
