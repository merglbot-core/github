"""
Cost threshold evaluation and alerting logic.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def evaluate_github_thresholds(github_data: Dict[str, Any], thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate GitHub cost thresholds."""
    alerts = []

    github_thresholds = thresholds.get("github", {})

    # Check Copilot thresholds
    copilot_thresholds = github_thresholds.get("copilot", {})
    copilot_data = github_data.get("copilot", {})

    # Check total monthly cost
    if "total_monthly_usd" in copilot_thresholds:
        threshold = copilot_thresholds["total_monthly_usd"]
        actual = copilot_data.get("monthly_cost_usd", 0)

        if actual > threshold:
            alerts.append(
                {
                    "scope": "github",
                    "item": "copilot",
                    "type": "total_usd",
                    "value": actual,
                    "threshold": threshold,
                    "severity": "high" if actual > threshold * 1.5 else "medium",
                }
            )

    # Check seats
    if "seats" in copilot_thresholds:
        seats_threshold = copilot_thresholds["seats"].get("max", 0)
        seats_actual = copilot_data.get("seats_assigned", 0)

        if seats_threshold and seats_actual > seats_threshold:
            alerts.append(
                {
                    "scope": "github",
                    "item": "copilot",
                    "type": "seats",
                    "value": seats_actual,
                    "threshold": seats_threshold,
                    "severity": "medium",
                }
            )

    # Check Enterprise Cloud thresholds
    ec_thresholds = github_thresholds.get("enterprise_cloud", {})
    ec_data = github_data.get("enterprise_cloud", {})

    if "seats" in ec_thresholds:
        ec_seats_threshold = ec_thresholds["seats"].get("max", 0)
        ec_seats_actual = ec_data.get("seats", 0)

        if ec_seats_threshold and ec_seats_actual > ec_seats_threshold:
            alerts.append(
                {
                    "scope": "github",
                    "item": "enterprise_cloud",
                    "type": "seats",
                    "value": ec_seats_actual,
                    "threshold": ec_seats_threshold,
                    "severity": "medium",
                }
            )

    return alerts


def evaluate_gcp_thresholds(gcp_data: Dict[str, Any], thresholds: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate GCP cost thresholds."""
    alerts = []

    gcp_thresholds = thresholds.get("gcp", {})
    configured_currency = str(gcp_thresholds.get("currency", "")).upper()
    source_currency = str(gcp_data.get("currency", "")).upper()
    if not configured_currency:
        raise ValueError("gcp.currency is required in threshold configuration")
    if source_currency != configured_currency:
        raise ValueError(
            "GCP threshold currency mismatch: "
            f"configured {configured_currency}, observed {source_currency or 'missing'}"
        )

    defaults = gcp_thresholds.get("defaults", {})
    project_thresholds = gcp_thresholds.get("projects", {})

    project_costs = gcp_data.get("project_costs", [])

    for project_cost in project_costs:
        project_id = project_cost["project_id"]
        total_cost = project_cost["total_net"]
        services = project_cost["services"]

        # Get project-specific thresholds or use defaults
        if project_id in project_thresholds:
            project_specific = project_thresholds[project_id]
        else:
            project_specific = defaults

        # Check total monthly cost
        total_threshold = project_specific.get("total_monthly", 0)
        if total_threshold and total_cost > total_threshold:
            alerts.append(
                {
                    "scope": "gcp",
                    "project": project_id,
                    "type": "total_cost",
                    "currency": source_currency,
                    "value": total_cost,
                    "threshold": total_threshold,
                    "severity": "high" if total_cost > total_threshold * 1.5 else "medium",
                }
            )

        # Check per-service costs
        service_thresholds = project_specific.get("per_service_monthly", {})

        for service_cost in services:
            service_name = service_cost["service"]
            service_cost_value = service_cost["net_cost"]

            if service_name in service_thresholds:
                service_threshold = service_thresholds[service_name]

                if service_cost_value > service_threshold:
                    alerts.append(
                        {
                            "scope": "gcp",
                            "project": project_id,
                            "type": "service",
                            "currency": source_currency,
                            "service": service_name,
                            "value": service_cost_value,
                            "threshold": service_threshold,
                            "severity": "medium",
                        }
                    )

    return alerts


def evaluate_all_thresholds(
    github_data: Dict[str, Any], gcp_data: Dict[str, Any], thresholds: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluate all thresholds and return alerts."""

    github_alerts = []
    gcp_alerts = []

    if github_data:
        github_alerts = evaluate_github_thresholds(github_data, thresholds)
    else:
        logger.warning("No GitHub data provided for threshold evaluation")

    if gcp_data:
        gcp_alerts = evaluate_gcp_thresholds(gcp_data, thresholds)
    else:
        logger.warning("No GCP data provided for threshold evaluation")

    all_alerts = github_alerts + gcp_alerts

    # Ensure all alerts have a severity
    for alert in all_alerts:
        if "severity" not in alert:
            alert["severity"] = "low"

    # Sort by severity
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_alerts.sort(key=lambda x: severity_order.get(x.get("severity"), 3))

    threshold_exceeded = len(all_alerts) > 0

    logger.info(f"Threshold evaluation complete: {len(all_alerts)} alerts")

    return {
        "alerts": all_alerts,
        "threshold_exceeded": threshold_exceeded,
        "github_alerts_count": len(github_alerts),
        "gcp_alerts_count": len(gcp_alerts),
        "critical_count": sum(1 for a in all_alerts if a.get("severity") == "critical"),
        "high_count": sum(1 for a in all_alerts if a.get("severity") == "high"),
        "medium_count": sum(1 for a in all_alerts if a.get("severity") == "medium"),
    }


def format_alert_message(alert: Dict[str, Any]) -> str:
    """Format an alert for display."""
    scope = alert.get("scope", "unknown")

    if scope == "github":
        item = alert.get("item", "")
        type_ = alert.get("type", "")
        value = alert.get("value", 0)
        threshold = alert.get("threshold", 0)

        if type_ == "total_usd":
            return f"🚨 GitHub {item.title()}: Monthly cost ${value:.2f} exceeds threshold ${threshold:.2f}"
        elif type_ == "seats":
            return f"⚠️ GitHub {item.title()}: {value} seats exceeds limit of {threshold}"

    elif scope == "gcp":
        project = alert.get("project", "")
        type_ = alert.get("type", "")
        value = alert.get("value", 0)
        threshold = alert.get("threshold", 0)
        currency = alert.get("currency", "")

        if type_ == "total_cost":
            return (
                f"🚨 GCP Project '{project}': Monthly cost {value:.2f} {currency} "
                f"exceeds threshold {threshold:.2f} {currency}"
            )
        elif type_ == "service":
            service = alert.get("service", "")
            return (
                f"⚠️ GCP '{project}' - {service}: {value:.2f} {currency} " f"exceeds limit {threshold:.2f} {currency}"
            )

    return (
        f"Alert: {alert.get('scope', 'unknown')} - {alert.get('type', 'unknown')} "
        f"(value: {alert.get('value', 'N/A')}, threshold: {alert.get('threshold', 'N/A')})"
    )
