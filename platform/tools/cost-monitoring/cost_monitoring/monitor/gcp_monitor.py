"""GCP billing and cost monitoring via the canonical BigQuery export."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from google.cloud import bigquery
from google.cloud.billing.budgets_v1 import BudgetServiceClient

logger = logging.getLogger(__name__)

MAXIMUM_BYTES_BILLED = 5 * 1024 * 1024 * 1024


class CostDataError(RuntimeError):
    """Raised when cost evidence is missing, inconsistent, or unreadable."""


def _month_bounds(month: Optional[str]) -> tuple[datetime, datetime]:
    """Return inclusive/exclusive UTC bounds for a YYYY-MM reporting month."""

    value = month or datetime.now(timezone.utc).strftime("%Y-%m")
    try:
        start = datetime.strptime(value, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Invalid month {value!r}; expected YYYY-MM") from exc

    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _validate_table_reference(table_fqn: str) -> None:
    """Validate a project.dataset.table-pattern reference before interpolation."""

    try:
        project_id, dataset, table_pattern = table_fqn.split(".")
    except ValueError as exc:
        raise ValueError(f"Invalid table reference format: {table_fqn}") from exc

    identifiers_ok = all(part.replace("_", "").replace("-", "").isalnum() for part in (project_id, dataset))
    table_ok = all(character.isalnum() or character in "_-*" for character in table_pattern)
    if not identifiers_ok or not table_ok:
        raise ValueError(f"Invalid table reference format: {table_fqn}")


def query_month_costs_by_service(
    bq: bigquery.Client,
    table_fqn: str,
    project_ids: List[str],
    month: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query one bounded month, enforcing a single export currency."""

    if not project_ids:
        raise ValueError("At least one project ID is required")
    _validate_table_reference(table_fqn)
    month_start, month_end = _month_bounds(month)

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("project_ids", "STRING", project_ids),
            bigquery.ScalarQueryParameter("month_start", "TIMESTAMP", month_start),
            bigquery.ScalarQueryParameter("month_end", "TIMESTAMP", month_end),
        ]
    )
    job_config.maximum_bytes_billed = MAXIMUM_BYTES_BILLED

    sql = f"""
    SELECT
        project.id AS project_id,
        service.description AS service,
        currency,
        SUM(cost) AS cost,
        SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS credits
    FROM `{table_fqn}`
    WHERE _PARTITIONTIME >= @month_start
      AND _PARTITIONTIME < @month_end
      AND usage_start_time >= @month_start
      AND usage_start_time < @month_end
      AND project.id IN UNNEST(@project_ids)
    GROUP BY 1, 2, 3
    ORDER BY 1, 4 DESC
    """

    logger.info("Querying bounded BigQuery billing data for %d projects", len(project_ids))
    try:
        rows = list(bq.query(sql, job_config=job_config).result())
    except Exception as exc:
        raise CostDataError("BigQuery billing query failed") from exc

    if not rows:
        raise CostDataError("BigQuery billing export returned no rows for the configured projects and month")

    currencies = {str(row.currency).upper() for row in rows if row.currency}
    if len(currencies) != 1:
        observed = ", ".join(sorted(currencies)) or "missing"
        raise CostDataError(f"Expected one billing export currency, observed: {observed}")
    currency = currencies.pop()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        row_currency = str(row.currency).upper() if row.currency else ""
        if row_currency != currency:
            raise CostDataError("Billing row currency changed during aggregation")
        cost = float(row.cost or 0)
        credits = float(row.credits or 0)
        grouped.setdefault(row.project_id, []).append(
            {
                "service": row.service,
                "currency": currency,
                "cost": cost,
                "credits": credits,
                # Billing export credit amounts are signed (normally negative).
                "net_cost": cost + credits,
            }
        )

    results: List[Dict[str, Any]] = []
    for project_id, services in grouped.items():
        results.append(
            {
                "project_id": project_id,
                "currency": currency,
                "services": services,
                "total_cost": sum(service["cost"] for service in services),
                "total_credits": sum(service["credits"] for service in services),
                "total_net": sum(service["net_cost"] for service in services),
            }
        )

    logger.info("Retrieved aggregate billing data for %d projects in %s", len(results), currency)
    return results


def list_budgets(billing_account_id: str) -> List[Dict[str, Any]]:
    """List optional billing-account budgets without affecting cost evidence."""

    try:
        client = BudgetServiceClient()
        parent = f"billingAccounts/{billing_account_id}"
        budgets = []
        for budget in client.list_budgets(parent=parent):
            amount = None
            currency = None
            if getattr(budget.amount, "specified_amount", None):
                specified = budget.amount.specified_amount
                amount = float(specified.units + specified.nanos / 1e9)
                currency = specified.currency_code or None
            budgets.append(
                {
                    "name": budget.name,
                    "display_name": budget.display_name,
                    "amount": amount,
                    "currency": currency,
                    "projects": list(budget.budget_filter.projects) if budget.budget_filter.projects else [],
                    "services": list(budget.budget_filter.services) if budget.budget_filter.services else [],
                }
            )
        logger.info("Retrieved %d budgets", len(budgets))
        return budgets
    except Exception as exc:
        logger.warning("Optional billing budget lookup failed: %s", type(exc).__name__)
        return []


def _get_all_projects_recursively(config_node: Any) -> List[str]:
    """Recursively extract configured project IDs without row-level data."""

    projects: List[str] = []
    if isinstance(config_node, list):
        projects.extend(project for project in config_node if isinstance(project, str))
    elif isinstance(config_node, dict):
        for value in config_node.values():
            projects.extend(_get_all_projects_recursively(value))
    return projects


def collect_gcp(config: Dict[str, Any], month: Optional[str] = None) -> Dict[str, Any]:
    """Collect GCP costs, failing closed on missing or inconsistent billing evidence."""

    billing_config = config.get("billing_export", {})
    project_id = billing_config.get("project_id")
    dataset = billing_config.get("dataset", "billing_export_euw1")
    table_pattern = billing_config.get("table_pattern", "gcp_billing_export_v1_*")
    expected_currency = str(billing_config.get("currency", "CZK")).upper()
    billing_account_id = config.get("billing_account_id")

    if not project_id:
        raise ValueError("gcp.billing_export.project_id is required")

    projects_config = config.get("projects", {})
    all_projects = sorted(set(_get_all_projects_recursively(projects_config)))
    if not all_projects:
        raise ValueError("No projects configured for GCP monitoring")

    current_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    table_fqn = f"{project_id}.{dataset}.{table_pattern}"
    bq_client = bigquery.Client(project=project_id)
    project_costs = query_month_costs_by_service(bq_client, table_fqn, all_projects, current_month)

    currencies = {project["currency"] for project in project_costs}
    if currencies != {expected_currency}:
        observed = ", ".join(sorted(currencies)) or "missing"
        raise CostDataError(f"Billing export currency mismatch: configured {expected_currency}, observed {observed}")

    budgets = []
    if billing_account_id and billing_account_id != "XXXX-XXXX-XXXX":
        budgets = list_budgets(billing_account_id)

    categorized_costs: Dict[str, Any] = {}
    for category, category_data in projects_config.items():
        categorized_costs[category] = {}
        if isinstance(category_data, list):
            category_costs = [project for project in project_costs if project["project_id"] in category_data]
            categorized_costs[category] = {
                "projects": category_costs,
                "currency": expected_currency,
                "total_cost": sum(project["total_cost"] for project in category_costs),
                "total_net": sum(project["total_net"] for project in category_costs),
            }
        elif isinstance(category_data, dict):
            for subcategory, project_list in category_data.items():
                if isinstance(project_list, list):
                    subcategory_costs = [project for project in project_costs if project["project_id"] in project_list]
                    categorized_costs[category][subcategory] = {
                        "projects": subcategory_costs,
                        "currency": expected_currency,
                        "total_cost": sum(project["total_cost"] for project in subcategory_costs),
                        "total_net": sum(project["total_net"] for project in subcategory_costs),
                    }

    return {
        "month": current_month,
        "billing_account": billing_account_id,
        "currency": expected_currency,
        "projects_monitored": len(all_projects),
        "project_costs": project_costs,
        "categorized_costs": categorized_costs,
        "budgets": budgets,
        "total_cost": sum(project["total_cost"] for project in project_costs),
        "total_credits": sum(project["total_credits"] for project in project_costs),
        "total_net": sum(project["total_net"] for project in project_costs),
    }
