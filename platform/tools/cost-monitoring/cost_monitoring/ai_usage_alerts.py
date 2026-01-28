"""
AI Usage Telemetry Alerts

Reads budget configuration from Firestore (admin project) and compares it to
month-to-date spend in BigQuery `ai_usage` dataset (platform project).

This is designed to run in GitHub Actions using WIF/OIDC (no JSON keys).
"""

from __future__ import annotations

import os
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from google.cloud import bigquery
from google.cloud import firestore

from cost_monitoring.alerting.notifiers import send_slack
from cost_monitoring.utils.anomaly_config import parse_daily_spike_factor


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


@dataclass(frozen=True)
class BudgetItem:
    id: str
    provider: str
    product: Optional[str]
    monthly_budget_usd: float
    enabled: bool = True
    thresholds: Tuple[float, ...] = (0.8, 1.0)


def _parse_budget_item(raw: Dict[str, Any]) -> Optional[BudgetItem]:
    provider = str(raw.get("provider") or "").strip()
    if not provider:
        return None

    item_id = str(raw.get("id") or f"{provider}:{raw.get('product') or 'all'}").strip()
    product = raw.get("product")
    product_s = str(product).strip() if product is not None else None
    monthly_budget_usd = float(raw.get("monthly_budget_usd") or 0.0)
    enabled = raw.get("enabled", True) is not False

    thresholds_raw = raw.get("thresholds") or raw.get("alert_thresholds") or [0.8, 1.0]
    thresholds: List[float] = []
    if isinstance(thresholds_raw, list):
        for t in thresholds_raw:
            try:
                thresholds.append(float(t))
            except (TypeError, ValueError):
                continue
    thresholds = sorted({t for t in thresholds if t > 0})
    if not thresholds:
        thresholds = [0.8, 1.0]

    return BudgetItem(
        id=item_id,
        provider=provider,
        product=product_s,
        monthly_budget_usd=monthly_budget_usd,
        enabled=enabled,
        thresholds=tuple(thresholds),
    )


def load_budget_config(admin_project_id: str) -> Dict[str, Any]:
    db = firestore.Client(project=admin_project_id)
    doc = db.collection("settings").document("ai_usage_telemetry").get()
    if not doc.exists:
        return {}
    data = doc.to_dict() or {}
    return data


def get_mtd_spend(platform_project_id: str, dataset: str) -> List[Dict[str, Any]]:
    bq = bigquery.Client(project=platform_project_id)
    table = f"`{platform_project_id}.{dataset}.agg_ai_usage_daily`"

    query = f"""
      select
        provider,
        product,
        cast(sum(cost_usd) as float64) as cost_mtd_usd,
        safe_divide(cast(sum(cost_usd) as float64), extract(day from current_date())) * extract(day from last_day(current_date())) as projected_month_usd
      from {table}
      where event_date between date_trunc(current_date(), month) and current_date()
      group by 1, 2
      order by cost_mtd_usd desc
    """

    rows = list(bq.query(query).result())
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "provider": r.get("provider"),
                "product": r.get("product"),
                "cost_mtd_usd": float(r.get("cost_mtd_usd") or 0.0),
                "projected_month_usd": float(r.get("projected_month_usd") or 0.0),
            }
        )
    return out


def get_recent_daily_spend(platform_project_id: str, dataset: str, days: int = 14) -> List[Dict[str, Any]]:
    bq = bigquery.Client(project=platform_project_id)
    table = f"`{platform_project_id}.{dataset}.agg_ai_usage_daily`"

    query = f"""
      select
        event_date,
        provider,
        cast(sum(cost_usd) as float64) as cost_usd
      from {table}
      where event_date >= date_sub(current_date(), interval {int(days)} day)
      group by 1, 2
      order by 1 asc
    """

    rows = list(bq.query(query).result())
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "event_date": r.get("event_date"),
                "provider": r.get("provider"),
                "cost_usd": float(r.get("cost_usd") or 0.0),
            }
        )
    return out


def _sum_spend(spend_rows: List[Dict[str, Any]], provider: str, product: Optional[str]) -> Tuple[float, float]:
    used = 0.0
    projected = 0.0
    for row in spend_rows:
        if (row.get("provider") or "") != provider:
            continue
        if product is not None and (row.get("product") or "") != product:
            continue
        used += float(row.get("cost_mtd_usd") or 0.0)
        projected += float(row.get("projected_month_usd") or 0.0)
    return used, projected


def evaluate_budget_alerts(budgets: List[BudgetItem], spend_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    for b in budgets:
        if not b.enabled or b.monthly_budget_usd <= 0:
            continue
        used, projected = _sum_spend(spend_rows, b.provider, b.product)
        used_pct = used / b.monthly_budget_usd
        projected_pct = projected / b.monthly_budget_usd

        breached = [t for t in b.thresholds if used_pct >= t or projected_pct >= t]
        if not breached:
            continue

        alerts.append(
            {
                "type": "ai_usage_budget",
                "severity": "HIGH" if used_pct >= 1.0 or projected_pct >= 1.0 else "WARN",
                "budget_id": b.id,
                "provider": b.provider,
                "product": b.product,
                "monthly_budget_usd": b.monthly_budget_usd,
                "mtd_cost_usd": used,
                "projected_month_usd": projected,
                "used_pct": used_pct,
                "projected_pct": projected_pct,
                "thresholds": list(b.thresholds),
            }
        )
    return alerts


def evaluate_anomaly_alerts(
    daily_rows: List[Dict[str, Any]],
    *,
    spike_factor: float = 2.0,
    min_usd: float = 10.0,
) -> List[Dict[str, Any]]:
    # Group by provider -> date -> cost
    by_provider: Dict[str, Dict[str, float]] = {}
    for r in daily_rows:
        provider = str(r.get("provider") or "").strip()
        if not provider:
            continue
        date_key = str(r.get("event_date"))
        by_provider.setdefault(provider, {})[date_key] = float(r.get("cost_usd") or 0.0)

    # Evaluate yesterday vs avg of previous 7 days (excluding yesterday)
    today = _utc_now().date()
    yesterday = today - dt.timedelta(days=1)
    prev_start = today - dt.timedelta(days=8)
    prev_end = today - dt.timedelta(days=2)

    alerts: List[Dict[str, Any]] = []
    for provider, series in by_provider.items():
        y_cost = float(series.get(str(yesterday), 0.0))
        window: List[float] = []
        d = prev_start
        while d <= prev_end:
            window.append(float(series.get(str(d), 0.0)))
            d += dt.timedelta(days=1)
        avg_prev = sum(window) / len(window) if window else 0.0

        if y_cost < min_usd or avg_prev <= 0:
            continue
        if y_cost <= avg_prev * spike_factor:
            continue

        alerts.append(
            {
                "type": "ai_usage_anomaly",
                "severity": "WARN",
                "provider": provider,
                "yesterday_cost_usd": y_cost,
                "avg_prev7_cost_usd": avg_prev,
                "spike_factor": spike_factor,
            }
        )

    return alerts


def format_slack_message(month: str, platform_project_id: str, alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    header = f"🤖 AI Usage Telemetry Alerts - {month}"
    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*Platform project:* `{platform_project_id}`"},
                {"type": "mrkdwn", "text": f"*Generated:* {_utc_now().strftime('%Y-%m-%d %H:%M UTC')}"},
            ],
        },
        {"type": "divider"},
    ]

    lines: List[str] = []
    for a in alerts[:20]:
        if a.get("type") == "ai_usage_budget":
            scope = f"{a.get('provider')}" + (f" / {a.get('product')}" if a.get("product") else "")
            used_pct = float(a.get("used_pct") or 0.0) * 100
            proj_pct = float(a.get("projected_pct") or 0.0) * 100
            lines.append(
                f"- *{scope}*: MTD ${a.get('mtd_cost_usd', 0):,.2f} / ${a.get('monthly_budget_usd', 0):,.2f} "
                f"({used_pct:,.1f}%), projected ${a.get('projected_month_usd', 0):,.2f} ({proj_pct:,.1f}%)"
            )
        elif a.get("type") == "ai_usage_anomaly":
            lines.append(
                f"- *{a.get('provider')}*: anomaly spike — yesterday ${a.get('yesterday_cost_usd', 0):,.2f}, "
                f"prev7 avg ${a.get('avg_prev7_cost_usd', 0):,.2f} (>{a.get('spike_factor', 0)}×)"
            )

    text = "\n".join(lines) if lines else "No AI usage budget alerts."
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    if len(alerts) > 20:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"... and {len(alerts) - 20} more"}]}
        )

    return {"text": f"AI usage alerts ({month}): {len(alerts)}", "blocks": blocks}


def main() -> int:
    admin_project_id = _env("AI_USAGE_ADMIN_PROJECT_ID", "merglbot-admin-prd")
    platform_project_id = _env("AI_USAGE_PLATFORM_PROJECT_ID", "merglbot-platform-prd")
    dataset = _env("AI_USAGE_DATASET", "ai_usage")

    config = load_budget_config(admin_project_id)
    anomaly_cfg = config.get("anomaly") if isinstance(config.get("anomaly"), dict) else {}
    spike_factor = parse_daily_spike_factor(anomaly_cfg)

    raw_budgets = config.get("budgets") or []
    budgets: List[BudgetItem] = []
    if isinstance(raw_budgets, list):
        for b in raw_budgets:
            if isinstance(b, dict):
                parsed = _parse_budget_item(b)
                if parsed:
                    budgets.append(parsed)

    if not budgets:
        print("AI usage alerts: no budgets configured; skipping.")
        # Still run anomaly checks even without budgets (best-effort).
        budgets = []

    spend_rows = get_mtd_spend(platform_project_id, dataset)
    alerts = evaluate_budget_alerts(budgets, spend_rows) if budgets else []

    daily_rows = get_recent_daily_spend(platform_project_id, dataset, days=14)
    alerts += evaluate_anomaly_alerts(daily_rows, spike_factor=spike_factor)

    month = _utc_now().strftime("%Y-%m")
    if alerts:
        webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
        formatted = format_slack_message(month, platform_project_id, alerts)
        if webhook_url:
            send_slack(webhook_url, formatted["text"], formatted["blocks"])
        else:
            print("SLACK_WEBHOOK_URL not set; skipping Slack notification.")
        print(f"AI usage alerts: {len(alerts)} alert(s).")
        return 2

    print("AI usage alerts: OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
