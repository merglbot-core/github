"""Privacy-safe equal-window verification of realized GCP FinOps savings.

The verifier intentionally produces only aggregate action/currency totals.  It
never exposes billing rows, resource identities, or provider error payloads.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from google.cloud import bigquery

from .monitor.gcp_monitor import MAXIMUM_BYTES_BILLED

CANONICAL_TABLE = "merglbot-platform-prd.billing_export_euw1." "gcp_billing_export_v1_01E453_6322D7_41E77E"
WINDOW_DAYS = 30


class RealizedSavingsError(RuntimeError):
    """A sanitized fail-closed verifier error."""


@dataclass(frozen=True)
class Action:
    action_id: str
    cutoff: datetime | None
    project_ids: tuple[str, ...]
    services: tuple[str, ...]
    sku_contains: tuple[str, ...]
    billing_lag_days: int
    mismatch_tolerance_percent: float
    mismatch_tolerance_absolute: float
    requires_receipted_expansion: bool
    expansion_receipt_sha256: str | None
    parent_scenario: str | None

    @property
    def pre_start(self) -> datetime:
        assert self.cutoff is not None
        return self.cutoff - timedelta(days=WINDOW_DAYS)

    @property
    def post_end(self) -> datetime:
        assert self.cutoff is not None
        return self.cutoff + timedelta(days=WINDOW_DAYS)

    @property
    def eligible_at(self) -> datetime | None:
        if self.cutoff is None:
            return None
        return self.post_end + timedelta(days=self.billing_lag_days)


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid action cutoff; expected an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("Action cutoff must include a timezone")
    return parsed.astimezone(UTC)


def _is_sha256(value: str | None) -> bool:
    return bool(value and len(value) == 64 and all(character in "0123456789abcdef" for character in value))


def load_actions(path: str | Path) -> tuple[list[Action], str]:
    """Load and strictly validate the versioned measurement contract."""

    config_path = Path(path)
    raw = config_path.read_bytes()
    try:
        config = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Invalid realized-savings configuration") from exc

    if config.get("billing_table") != CANONICAL_TABLE:
        raise ValueError("billing_table must be the canonical billing_export_euw1 standard export")
    if config.get("window_days") != WINDOW_DAYS:
        raise ValueError("window_days must be exactly 30")
    if config.get("maximum_bytes_billed") != MAXIMUM_BYTES_BILLED:
        raise ValueError("maximum_bytes_billed must be exactly 5 GiB")

    actions: list[Action] = []
    seen: set[str] = set()
    for item in config.get("actions", []):
        action_id = str(item.get("id", ""))
        if not action_id or action_id in seen:
            raise ValueError("Action IDs must be present and unique")
        seen.add(action_id)
        project_ids = tuple(sorted(set(item.get("project_ids") or [])))
        if not project_ids:
            raise ValueError(f"Action {action_id} requires project_ids")
        services = tuple(sorted(set(item.get("services") or [])))
        sku_contains = tuple(sorted(set(item.get("sku_contains") or [])))
        cutoff = _utc(item["cutoff"]) if item.get("cutoff") else None
        requires_receipt = bool(item.get("requires_receipted_expansion", False))
        receipt = item.get("expansion_receipt_sha256")
        if receipt is not None:
            receipt = str(receipt).lower()
            if not _is_sha256(receipt):
                raise ValueError(f"Action {action_id} has an invalid expansion receipt SHA-256")
        if requires_receipt and ((cutoff is None) != (receipt is None)):
            raise ValueError(f"Action {action_id} requires both cutoff and expansion receipt, or neither")
        billing_lag_days = int(item.get("billing_lag_days", 3))
        if not 1 <= billing_lag_days <= 14:
            raise ValueError(f"Action {action_id} billing_lag_days must be between 1 and 14")
        actions.append(
            Action(
                action_id=action_id,
                cutoff=cutoff,
                project_ids=project_ids,
                services=services,
                sku_contains=sku_contains,
                billing_lag_days=billing_lag_days,
                mismatch_tolerance_percent=float(item.get("mismatch_tolerance_percent", 5.0)),
                mismatch_tolerance_absolute=float(item.get("mismatch_tolerance_absolute", 1.0)),
                requires_receipted_expansion=requires_receipt,
                expansion_receipt_sha256=receipt,
                parent_scenario=item.get("parent_scenario"),
            )
        )
    if not actions:
        raise ValueError("At least one action is required")
    return actions, hashlib.sha256(raw).hexdigest()


def classify_action(
    action: Action,
    rows: list[dict[str, Any]],
    health: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    """Classify one action from aggregate-only offline evidence."""

    base: dict[str, Any] = {
        "action_id": action.action_id,
        "parent_scenario": action.parent_scenario,
        "cutoff": action.cutoff.isoformat().replace("+00:00", "Z") if action.cutoff else None,
        "eligible_at": action.eligible_at.isoformat().replace("+00:00", "Z") if action.eligible_at else None,
        "state": "NOT_ELIGIBLE_YET",
        "amounts_by_currency": {},
    }
    if action.requires_receipted_expansion and not (
        action.cutoff is not None and _is_sha256(action.expansion_receipt_sha256)
    ):
        base["reason"] = "receipted_expansion_not_available"
        return base
    if action.eligible_at is None or as_of < action.eligible_at:
        base["reason"] = "equal_post_window_or_billing_lag_incomplete"
        return base

    latest_usage = health.get("latest_usage_date")
    latest_partition = health.get("latest_partition_date")
    if not latest_usage or not latest_partition:
        base.update(state="DATA_GAP", reason="export_freshness_missing")
        return base
    if int(health.get("missing_partition_days", 0)):
        base.update(state="DATA_GAP", reason="missing_post_window_partitions")
        return base
    required_last_day = (action.post_end - timedelta(microseconds=1)).date()
    if latest_usage < required_last_day or latest_partition < required_last_day:
        base.update(state="DATA_GAP", reason="post_window_export_incomplete")
        return base

    by_currency: dict[str, dict[str, float]] = {}
    for row in rows:
        if row["action_id"] != action.action_id:
            continue
        currency = str(row["currency"]).upper()
        window = str(row["window"]).lower()
        if window not in {"pre", "post"} or not currency:
            base.update(state="DATA_GAP", reason="invalid_aggregate_schema")
            return base
        values = by_currency.setdefault(currency, {})
        if window in values:
            base.update(state="DATA_GAP", reason="duplicate_currency_window")
            return base
        values[window] = float(row["net_cost"])
    if not by_currency or any(set(values) != {"pre", "post"} for values in by_currency.values()):
        base.update(state="DATA_GAP", reason="incomplete_equal_window_aggregates")
        return base

    mismatch = False
    for currency, values in sorted(by_currency.items()):
        pre = values["pre"]
        post = values["post"]
        savings = pre - post
        tolerance = max(action.mismatch_tolerance_absolute, abs(pre) * action.mismatch_tolerance_percent / 100)
        if savings <= tolerance:
            mismatch = True
        base["amounts_by_currency"][currency] = {
            "pre_net": round(pre, 6),
            "post_net": round(post, 6),
            "realized_savings": round(savings, 6),
            "tolerance": round(tolerance, 6),
        }
    base["state"] = "MISMATCH" if mismatch else "REALIZED"
    base["reason"] = "no_material_reduction" if mismatch else "equal_window_reduction_verified"
    return base


def _query_sql(actions: list[Action]) -> tuple[str, list[bigquery.QueryParameter]]:
    """Build one bounded aggregate query for all currently eligible actions."""

    if not actions:
        raise ValueError("No eligible actions to query")
    parameters: list[bigquery.QueryParameter] = []
    action_parts: list[str] = []
    health_parts: list[str] = []
    for index, action in enumerate(actions):
        assert action.cutoff is not None
        parameters.extend(
            [
                bigquery.ScalarQueryParameter(f"action_{index}", "STRING", action.action_id),
                bigquery.ScalarQueryParameter(f"pre_start_{index}", "TIMESTAMP", action.pre_start),
                bigquery.ScalarQueryParameter(f"cutoff_{index}", "TIMESTAMP", action.cutoff),
                bigquery.ScalarQueryParameter(f"post_end_{index}", "TIMESTAMP", action.post_end),
                bigquery.ArrayQueryParameter(f"projects_{index}", "STRING", list(action.project_ids)),
                bigquery.ArrayQueryParameter(f"services_{index}", "STRING", list(action.services)),
                bigquery.ArrayQueryParameter(f"skus_{index}", "STRING", list(action.sku_contains)),
            ]
        )
        action_parts.append(f"""
            SELECT @action_{index} AS action_id,
                   IF(usage_start_time < @cutoff_{index}, 'pre', 'post') AS period,
                   currency,
                   cost + IFNULL((SELECT SUM(credit.amount) FROM UNNEST(credits) credit), 0) AS net_cost
              FROM source
             WHERE project_id IN UNNEST(@projects_{index})
               AND usage_start_time >= @pre_start_{index}
               AND usage_start_time < @post_end_{index}
               AND (ARRAY_LENGTH(@services_{index}) = 0 OR service IN UNNEST(@services_{index}))
               AND (ARRAY_LENGTH(@skus_{index}) = 0 OR EXISTS (
                     SELECT 1 FROM UNNEST(@skus_{index}) needle WHERE STRPOS(LOWER(sku), LOWER(needle)) > 0))
            """)
        health_parts.append(f"""
            SELECT @action_{index} AS action_id,
                   COUNTIF(day NOT IN (SELECT partition_day FROM partitions)) AS missing_partition_days
              FROM UNNEST(GENERATE_DATE_ARRAY(DATE(@cutoff_{index}), DATE(@post_end_{index}) - 1)) day
            """)
    earliest = min(action.pre_start for action in actions)
    latest = max(action.post_end for action in actions)
    parameters.extend(
        [
            bigquery.ScalarQueryParameter("earliest_usage", "TIMESTAMP", earliest),
            bigquery.ScalarQueryParameter("latest_usage", "TIMESTAMP", latest),
            bigquery.ScalarQueryParameter("partition_end", "TIMESTAMP", datetime.now(UTC)),
        ]
    )
    sql = f"""
    WITH source AS (
      SELECT usage_start_time, _PARTITIONTIME AS partition_time, project.id AS project_id,
             service.description AS service, sku.description AS sku, currency, cost, credits
        FROM `{CANONICAL_TABLE}`
       WHERE _PARTITIONTIME >= @earliest_usage
         AND _PARTITIONTIME < @partition_end
         AND usage_start_time >= @earliest_usage
         AND usage_start_time < @latest_usage
    ),
    partitions AS (SELECT DISTINCT DATE(partition_time) AS partition_day FROM source),
    matched AS ({' UNION ALL '.join(action_parts)}),
    currencies AS (SELECT DISTINCT action_id, currency FROM matched WHERE currency IS NOT NULL),
    periods AS (SELECT 'pre' AS period UNION ALL SELECT 'post'),
    totals AS (SELECT action_id, period, currency, SUM(net_cost) AS net_cost
                 FROM matched GROUP BY 1, 2, 3),
    aggregate_rows AS (
      SELECT currencies.action_id, periods.period, currencies.currency,
             IFNULL(totals.net_cost, 0) AS net_cost
        FROM currencies CROSS JOIN periods
        LEFT JOIN totals USING (action_id, period, currency)
    ),
    health AS ({' UNION ALL '.join(health_parts)}),
    freshness AS (
      SELECT MAX(DATE(usage_start_time)) AS latest_usage_date,
             MAX(DATE(partition_time)) AS latest_partition_date FROM source
    )
    SELECT aggregate_rows.*, health.missing_partition_days,
           freshness.latest_usage_date, freshness.latest_partition_date
      FROM aggregate_rows JOIN health USING (action_id) CROSS JOIN freshness
     ORDER BY action_id, currency, period
    """
    return sql, parameters


def query_aggregates(
    client: bigquery.Client, actions: list[Action], *, query_dry_run: bool = False
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
    """Execute one privacy-safe query under the 5 GiB hard cap."""

    sql, parameters = _query_sql(actions)
    config = bigquery.QueryJobConfig(query_parameters=parameters, dry_run=query_dry_run, use_query_cache=False)
    config.maximum_bytes_billed = MAXIMUM_BYTES_BILLED
    try:
        job = client.query(sql, job_config=config)
        processed = int(job.total_bytes_processed or 0)
        if processed > MAXIMUM_BYTES_BILLED:
            raise RealizedSavingsError("BigQuery dry-run estimate exceeds the 5 GiB safety cap")
        if query_dry_run:
            return [], {}, processed
        raw_rows = list(job.result())
    except RealizedSavingsError:
        raise
    except Exception as exc:
        raise RealizedSavingsError("BigQuery realized-savings query failed") from exc

    rows: list[dict[str, Any]] = []
    health: dict[str, dict[str, Any]] = {}
    for row in raw_rows:
        action_id = str(row.action_id)
        rows.append(
            {
                "action_id": action_id,
                "window": str(row.period),
                "currency": str(row.currency),
                "net_cost": float(row.net_cost or 0),
            }
        )
        health[action_id] = {
            "missing_partition_days": int(row.missing_partition_days or 0),
            "latest_usage_date": row.latest_usage_date,
            "latest_partition_date": row.latest_partition_date,
        }
    return rows, health, processed


def verify(
    config_path: str | Path,
    *,
    as_of: datetime | None = None,
    query_dry_run: bool = False,
    client: bigquery.Client | None = None,
) -> dict[str, Any]:
    """Run the verifier and return a sanitized aggregate receipt."""

    now = (as_of or datetime.now(UTC)).astimezone(UTC)
    actions, config_sha = load_actions(config_path)
    eligible = [
        action
        for action in actions
        if action.eligible_at is not None
        and now >= action.eligible_at
        and (not action.requires_receipted_expansion or _is_sha256(action.expansion_receipt_sha256))
    ]
    rows: list[dict[str, Any]] = []
    health: dict[str, dict[str, Any]] = {}
    bytes_processed = 0
    if eligible:
        rows, health, bytes_processed = query_aggregates(
            client or bigquery.Client(project="merglbot-platform-prd"), eligible, query_dry_run=query_dry_run
        )

    results = []
    for action in actions:
        if query_dry_run and action in eligible:
            results.append(
                {
                    "action_id": action.action_id,
                    "parent_scenario": action.parent_scenario,
                    "cutoff": action.cutoff.isoformat().replace("+00:00", "Z") if action.cutoff else None,
                    "eligible_at": (
                        action.eligible_at.isoformat().replace("+00:00", "Z") if action.eligible_at else None
                    ),
                    "state": "NOT_ELIGIBLE_YET",
                    "reason": "query_dry_run_only",
                    "amounts_by_currency": {},
                }
            )
        else:
            results.append(classify_action(action, rows, health.get(action.action_id, {}), now))

    states = {result["state"] for result in results}
    if "DATA_GAP" in states:
        verdict = "DATA_GAP"
    elif "MISMATCH" in states:
        verdict = "MISMATCH"
    elif "REALIZED" in states:
        verdict = "REALIZED"
    else:
        verdict = "NOT_ELIGIBLE_YET"
    return {
        "schema_version": 1,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "verdict": verdict,
        "billing_table": CANONICAL_TABLE,
        "config_sha256": config_sha,
        "window_days": WINDOW_DAYS,
        "maximum_bytes_billed": MAXIMUM_BYTES_BILLED,
        "query_dry_run": query_dry_run,
        "total_bytes_processed": bytes_processed,
        "portfolio_total": None,
        "portfolio_total_reason": "action scenarios and currencies are intentionally not summed",
        "actions": results,
        "privacy": {
            "row_level_data_emitted": 0,
            "identity_data_emitted": 0,
            "raw_provider_payloads_emitted": 0,
        },
    }


def write_receipt(path: str | Path, receipt: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
