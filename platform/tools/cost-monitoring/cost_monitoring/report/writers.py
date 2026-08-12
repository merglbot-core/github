"""
Report writers for CSV, Markdown, and JSON formats.
"""

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..alerting.thresholds import format_alert_message

logger = logging.getLogger(__name__)


def format_money(value: float, currency: str) -> str:
    """Format a value with an explicit currency without implicit conversion."""

    if currency == "USD":
        return f"${value:,.2f}"
    return f"{value:,.2f} {currency}".strip()


def write_markdown(path: str, data: Dict[str, Any]) -> None:
    """Write markdown report."""
    with open(path, "w", encoding="utf-8") as f:
        month = data.get("month", dt.datetime.now().strftime("%Y-%m"))
        github_data = data.get("github", {})
        gcp_data = data.get("gcp", {})
        alerts = data.get("alerts", [])

        # Header
        f.write(f"# Cost Report {month}\n\n")
        f.write(f"Generated: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n")

        # Summary
        github_total = github_data.get("total_monthly_cost_usd", 0)
        gcp_total = gcp_data.get("total_net", 0)
        gcp_currency = gcp_data.get("currency", "")

        f.write("## Executive Summary\n\n")
        f.write(f"- **GitHub Costs**: {format_money(github_total, 'USD')}\n")
        f.write(f"- **GCP Costs**: {format_money(gcp_total, gcp_currency)}\n")
        f.write("- **Cross-currency total**: not calculated\n")
        f.write(f"- **Alerts**: {len(alerts)}\n\n")

        # GitHub Section
        f.write("## GitHub Enterprise\n\n")

        copilot = github_data.get("copilot", {})
        ec = github_data.get("enterprise_cloud", {})

        f.write("### Copilot\n")
        f.write(f"- Seats Assigned: {copilot.get('seats_assigned', 0)}\n")
        f.write(f"- Seats Purchased: {copilot.get('seats_purchased', 0)}\n")
        f.write(f"- Monthly Cost: ${copilot.get('monthly_cost_usd', 0):,.2f}\n")
        f.write(f"- Price per Seat: ${copilot.get('price_per_seat', 0):.2f}\n\n")

        f.write("### Enterprise Cloud\n")
        f.write(f"- Seats: {ec.get('seats', 0)}\n")
        f.write(f"- Monthly Cost: ${ec.get('monthly_cost_usd', 0):,.2f}\n")
        f.write(f"- Price per Seat: ${ec.get('price_per_seat', 0):.2f}\n\n")

        # Organization members
        org_members = github_data.get("org_members", [])
        if org_members:
            f.write("### Organization Members\n\n")
            f.write("| Organization | Members |\n")
            f.write("|--------------|--------:|\n")
            for om in org_members:
                f.write(f"| {om['org']} | {om['members']} |\n")
            f.write("\n")

        # GCP Section
        f.write("## GCP Costs\n\n")
        f.write(f"- **Projects Monitored**: {gcp_data.get('projects_monitored', 0)}\n")
        f.write(f"- **Total Cost**: {format_money(gcp_data.get('total_cost', 0), gcp_currency)}\n")
        f.write(f"- **Total Credits**: {format_money(gcp_data.get('total_credits', 0), gcp_currency)}\n")
        f.write(f"- **Net Cost**: {format_money(gcp_data.get('total_net', 0), gcp_currency)}\n\n")

        # Top projects by cost
        project_costs = gcp_data.get("project_costs", [])
        if project_costs:
            f.write("### Top Projects by Cost\n\n")
            f.write("| Project | Net Cost | Top Service | Service Cost |\n")
            f.write("|---------|----------:|-------------|-------------:|\n")

            # Sort by net cost
            sorted_projects = sorted(project_costs, key=lambda x: x["total_net"], reverse=True)

            for project in sorted_projects[:10]:
                project_id = project["project_id"]
                net_cost = project["total_net"]

                # Get top service
                services = project.get("services", [])
                if services:
                    top_service = max(services, key=lambda s: s["net_cost"])
                    service_name = top_service["service"][:30]  # Truncate long names
                    service_cost = top_service["net_cost"]
                else:
                    service_name = "N/A"
                    service_cost = 0

                f.write(
                    f"| {project_id} | {format_money(net_cost, gcp_currency)} | {service_name} | "
                    f"{format_money(service_cost, gcp_currency)} |\n"
                )

            f.write("\n")

        # Budgets
        budgets = gcp_data.get("budgets", [])
        if budgets:
            f.write("### Configured Budgets\n\n")
            f.write("| Budget | Amount | Projects |\n")
            f.write("|--------|--------:|----------|\n")

            for budget in budgets[:10]:
                name = budget.get("display_name", budget.get("name", ""))[:40]
                amount = budget.get("amount")
                budget_currency = budget.get("currency") or gcp_currency
                projects = ", ".join(budget.get("projects", [])[:3])
                if len(budget.get("projects", [])) > 3:
                    projects += f" (+{len(budget['projects']) - 3} more)"

                amount_display = "N/A" if amount is None else format_money(amount, budget_currency)
                f.write(f"| {name} | {amount_display} | {projects} |\n")

            f.write("\n")

        # Alerts
        if alerts:
            f.write("## ⚠️ Threshold Alerts\n\n")

            # Group by severity
            high_alerts = [a for a in alerts if a.get("severity") == "high"]
            medium_alerts = [a for a in alerts if a.get("severity") == "medium"]

            if high_alerts:
                f.write("### 🔴 High Priority\n\n")
                for alert in high_alerts:
                    f.write(f"- {format_alert_message(alert)}\n")
                f.write("\n")

            if medium_alerts:
                f.write("### 🟡 Medium Priority\n\n")
                for alert in medium_alerts[:10]:  # Limit to 10
                    f.write(f"- {format_alert_message(alert)}\n")
                if len(medium_alerts) > 10:
                    f.write(f"- ... and {len(medium_alerts) - 10} more\n")
                f.write("\n")

        # Footer
        f.write("---\n\n")
        f.write("*For full details, see the CSV and JSON reports.*\n")

    logger.info(f"Markdown report written to {path}")


def write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    """Write CSV report."""
    if not rows:
        logger.warning("No data to write to CSV")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        fields = ["source", "scope", "project", "service", "metric", "value", "currency", "month"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"CSV report written to {path} ({len(rows)} rows)")


def write_json(path: str, payload: Dict[str, Any]) -> None:
    """Write JSON report."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    logger.info(f"JSON report written to {path}")


def prepare_csv_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Prepare data rows for CSV export."""
    rows = []
    month = data.get("month", dt.datetime.now().strftime("%Y-%m"))

    # GitHub rows
    github_data = data.get("github", {})

    # Copilot
    copilot = github_data.get("copilot", {})
    rows.append(
        {
            "source": "github",
            "scope": "enterprise",
            "project": "",
            "service": "Copilot",
            "metric": "seats_assigned",
            "value": copilot.get("seats_assigned", 0),
            "currency": "count",
            "month": month,
        }
    )
    rows.append(
        {
            "source": "github",
            "scope": "enterprise",
            "project": "",
            "service": "Copilot",
            "metric": "monthly_cost",
            "value": copilot.get("monthly_cost_usd", 0),
            "currency": "USD",
            "month": month,
        }
    )

    # Enterprise Cloud
    ec = github_data.get("enterprise_cloud", {})
    rows.append(
        {
            "source": "github",
            "scope": "enterprise",
            "project": "",
            "service": "Enterprise Cloud",
            "metric": "seats",
            "value": ec.get("seats", 0),
            "currency": "count",
            "month": month,
        }
    )
    rows.append(
        {
            "source": "github",
            "scope": "enterprise",
            "project": "",
            "service": "Enterprise Cloud",
            "metric": "monthly_cost",
            "value": ec.get("monthly_cost_usd", 0),
            "currency": "USD",
            "month": month,
        }
    )

    # Organization members
    for om in github_data.get("org_members", []):
        rows.append(
            {
                "source": "github",
                "scope": "organization",
                "project": om["org"],
                "service": "Members",
                "metric": "count",
                "value": om["members"],
                "currency": "count",
                "month": month,
            }
        )

    # GCP rows
    gcp_data = data.get("gcp", {})

    # Project costs
    for project_cost in gcp_data.get("project_costs", []):
        project_id = project_cost["project_id"]

        # Total for project
        rows.append(
            {
                "source": "gcp",
                "scope": "project",
                "project": project_id,
                "service": "Total",
                "metric": "net_cost",
                "value": project_cost["total_net"],
                "currency": project_cost["currency"],
                "month": month,
            }
        )

        # Per service
        for service in project_cost.get("services", []):
            rows.append(
                {
                    "source": "gcp",
                    "scope": "service",
                    "project": project_id,
                    "service": service["service"],
                    "metric": "net_cost",
                    "value": service["net_cost"],
                    "currency": service["currency"],
                    "month": month,
                }
            )

    return rows


def write_all_reports(output_dir: str, data: Dict[str, Any], month: Optional[str] = None) -> Dict[str, str]:
    """Write all report formats."""

    # Ensure output directory exists
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine month
    if not month:
        month = dt.datetime.now().strftime("%Y-%m")

    # File paths
    base_name = f"costs_report_{month.replace('-', '_')}"
    csv_path = Path(output_dir) / f"{base_name}.csv"
    md_path = Path(output_dir) / f"{base_name}.md"
    json_path = Path(output_dir) / f"{base_name}.json"

    # Add month to data if not present
    data["month"] = month

    # Write CSV
    csv_rows = prepare_csv_rows(data)
    write_csv(str(csv_path), csv_rows)

    # Write Markdown
    write_markdown(str(md_path), data)

    # Write JSON
    data["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(str(json_path), data)

    return {"csv": str(csv_path), "markdown": str(md_path), "json": str(json_path)}
