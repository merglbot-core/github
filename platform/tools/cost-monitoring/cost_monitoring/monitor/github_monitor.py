"""
GitHub Enterprise and Copilot cost monitoring.
"""

import logging
import os
from typing import Any, Dict, List

import requests
from github import Github

logger = logging.getLogger(__name__)

API = "https://api.github.com"


def _enterprise_token() -> str:
    """Return the separately scoped token used for enterprise billing reads."""

    token = os.environ.get("ENTERPRISE_GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("Missing ENTERPRISE_GITHUB_TOKEN")
    return token


def _headers() -> Dict[str, str]:
    """Get headers for read-only enterprise and organization API calls."""

    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_enterprise_token()}",
    }


def list_org_members_count(gh: Github, org: str) -> int:
    """Get member count for an organization without logging PII."""
    members = gh.get_organization(org).get_members()
    count = members.totalCount
    logger.info("Organization %s has %d members", org, count)
    return count


def get_copilot_enterprise(enterprise: str) -> Dict[str, Any]:
    """Get Copilot billing data for enterprise."""
    response = requests.get(
        f"{API}/enterprises/{enterprise}/copilot/billing",
        headers=_headers(),
        timeout=30,
    )
    if response.ok:
        logger.info("Copilot enterprise data retrieved for %s", enterprise)
        return response.json()
    if response.status_code in (404, 422):
        logger.info("Copilot enterprise endpoint unavailable; trying organization endpoints")
        return {}
    raise RuntimeError(f"GitHub Copilot enterprise request failed with status {response.status_code}")


def get_copilot_org(org: str) -> Dict[str, Any]:
    """Get Copilot billing data for organization."""
    response = requests.get(
        f"{API}/orgs/{org}/copilot/billing",
        headers=_headers(),
        timeout=30,
    )
    if response.ok:
        logger.info("Copilot org data retrieved for %s", org)
        return response.json()
    if response.status_code == 404:
        return {}
    raise RuntimeError(f"GitHub Copilot organization request failed with status {response.status_code}")


def get_enterprise_cloud_seats(enterprise: str) -> Dict[str, Any]:
    """Get Enterprise Cloud seats data (best effort)."""
    response = requests.get(
        f"{API}/enterprises/{enterprise}/settings/billing/enterprise-cloud",
        headers=_headers(),
        timeout=30,
    )
    if response.ok:
        return response.json()
    if response.status_code in (404, 422):
        logger.info("Enterprise Cloud endpoint is not available for this account")
        return {}
    raise RuntimeError(f"GitHub Enterprise Cloud request failed with status {response.status_code}")


def collect_github(enterprise: str, orgs: List[str], pricing: Dict[str, float]) -> Dict[str, Any]:
    """Collect all GitHub cost data."""

    # Validate authentication before any best-effort endpoint handling.
    enterprise_token = _enterprise_token()
    gh = Github(enterprise_token)

    # Collect org member counts
    org_members = []
    # Note: Proper unique member counting would require fetching actual member IDs
    # and tracking them across orgs, which may have privacy implications.
    # For now, we'll use the sum as an upper bound estimate
    for org in orgs:
        member_count = list_org_members_count(gh, org)
        org_members.append({"org": org, "members": member_count})
    cop = get_copilot_enterprise(enterprise)

    if not cop or "seats" not in cop:
        # Fallback: sum from individual orgs
        logger.info("Falling back to per-org Copilot data")
        seats_assigned = 0
        seats_purchased = 0

        org_sources = 0
        for org in orgs:
            org_data = get_copilot_org(org)
            if org_data:
                org_sources += 1
                seats_assigned += org_data.get("seats_assigned", 0)
                seats_purchased += org_data.get("seats_purchased", 0)

        if org_sources == 0:
            raise RuntimeError("No GitHub Copilot billing source returned authoritative data")

        cop = {"seats_assigned": seats_assigned, "seats_purchased": seats_purchased}
    else:
        # Extract from enterprise response
        seats_data = cop.get("seats") or []
        cop = {
            "seats_assigned": seats_data[0].get("assigned", 0) if seats_data else 0,
            "seats_purchased": seats_data[0].get("purchased", 0) if seats_data else 0,
        }

    # Calculate Copilot costs (based on purchased seats for billing)
    copilot_price_per_seat = float(pricing.get("copilot_usd_per_seat", 19.0))
    cop_cost = cop.get("seats_purchased", 0) * copilot_price_per_seat

    # Try to get Enterprise Cloud seats
    ec = get_enterprise_cloud_seats(enterprise)
    ec_seats = int(ec.get("total_seats", 0) or 0)
    ec_price_per_seat = float(pricing.get("enterprise_cloud_usd_per_seat", 0.0))
    ec_cost = ec_seats * ec_price_per_seat

    # If no EC data, estimate from unique users
    if ec_seats == 0 and ec_price_per_seat > 0:
        # Use total members as estimate
        total_members = sum([om["members"] for om in org_members])
        ec_seats = total_members
        ec_cost = ec_seats * ec_price_per_seat

    return {
        "org_members": org_members,
        "total_members": sum([om["members"] for om in org_members]),
        "copilot": {
            "seats_assigned": cop.get("seats_assigned", 0),
            "seats_purchased": cop.get("seats_purchased", 0),
            "monthly_cost_usd": cop_cost,
            "price_per_seat": copilot_price_per_seat,
        },
        "enterprise_cloud": {"seats": ec_seats, "monthly_cost_usd": ec_cost, "price_per_seat": ec_price_per_seat},
        "total_monthly_cost_usd": cop_cost + ec_cost,
    }
