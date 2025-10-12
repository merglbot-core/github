"""
Notification handlers for Slack and GitHub Issues.
"""

import os
import json
import requests
from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def send_slack(webhook_url: str, markdown: str, blocks: Optional[List] = None) -> bool:
    """Send notification to Slack."""
    try:
        payload = {"text": markdown}
        
        if blocks:
            payload["blocks"] = blocks
        
        response = requests.post(webhook_url, json=payload, timeout=15)
        
        if response.ok:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.error(f"Failed to send Slack notification: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending Slack notification: {str(e)}")
        return False


def format_slack_cost_report(
    github_data: Dict[str, Any],
    gcp_data: Dict[str, Any],
    alerts: List[Dict[str, Any]],
    month: str
) -> Dict[str, Any]:
    """Format cost report for Slack with rich blocks."""
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"💰 Cost Report - {month}",
                "emoji": True
            }
        }
    ]
    
    # Summary section
    github_total = github_data.get("total_monthly_cost_usd", 0)
    gcp_total = gcp_data.get("total_net_usd", 0)
    grand_total = github_total + gcp_total
    
    blocks.append({
        "type": "section",
        "fields": [
            {
                "type": "mrkdwn",
                "text": f"*Total Monthly Cost:*\n${grand_total:,.2f}"
            },
            {
                "type": "mrkdwn",
                "text": f"*Alert Count:*\n{len(alerts)} ⚠️"
            }
        ]
    })
    
    blocks.append({"type": "divider"})
    
    # GitHub section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*GitHub Enterprise Costs*"
        }
    })
    
    copilot = github_data.get("copilot", {})
    ec = github_data.get("enterprise_cloud", {})
    
    github_fields = [
        {
            "type": "mrkdwn",
            "text": f"*Copilot:*\n${copilot.get('monthly_cost_usd', 0):,.2f} ({copilot.get('seats_assigned', 0)} seats)"
        },
        {
            "type": "mrkdwn",
            "text": f"*Enterprise Cloud:*\n${ec.get('monthly_cost_usd', 0):,.2f} ({ec.get('seats', 0)} seats)"
        }
    ]
    
    blocks.append({
        "type": "section",
        "fields": github_fields
    })
    
    blocks.append({"type": "divider"})
    
    # GCP section
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*GCP Costs*"
        }
    })
    
    gcp_fields = [
        {
            "type": "mrkdwn",
            "text": f"*Projects Monitored:*\n{gcp_data.get('projects_monitored', 0)}"
        },
        {
            "type": "mrkdwn",
            "text": f"*Total Net Cost:*\n${gcp_data.get('total_net_usd', 0):,.2f}"
        }
    ]
    
    blocks.append({
        "type": "section",
        "fields": gcp_fields
    })
    
    # Alerts section if any
    if alerts:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*🚨 Threshold Alerts*"
            }
        })
        
        # Show top 5 alerts
        for alert in alerts[:5]:
            from ..alerting.thresholds import format_alert_message
            alert_text = format_alert_message(alert)
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": alert_text
                }
            })
    
    # Footer
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {
                "type": "mrkdwn",
                "text": f"Generated at {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} | View full report in GitHub Actions"
            }
        ]
    })
    
    text = f"Cost Report {month}: Total ${grand_total:,.2f} with {len(alerts)} alerts"
    
    return {
        "text": text,
        "blocks": blocks
    }


def send_cost_report_to_slack(
    github_data: Dict[str, Any],
    gcp_data: Dict[str, Any],
    alerts: List[Dict[str, Any]],
    month: str
) -> bool:
    """Send formatted cost report to Slack."""
    
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("SLACK_WEBHOOK_URL not set, skipping Slack notification")
        return False
    
    formatted = format_slack_cost_report(github_data, gcp_data, alerts, month)
    
    return send_slack(
        webhook_url,
        formatted["text"],
        formatted["blocks"]
    )


def __init__():
    """Module initialization."""
    pass