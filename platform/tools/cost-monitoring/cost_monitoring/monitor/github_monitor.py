"""
GitHub Enterprise and Copilot cost monitoring.
"""

import os
import time
import math
import requests
from typing import Dict, Any, List, Tuple, Optional
from github import Github
import logging

logger = logging.getLogger(__name__)

API = "https://api.github.com"


def _headers() -> Dict[str, str]:
    """Get headers with GitHub token for API calls."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN")
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"token {token}",
    }


def list_org_members_count(org: str) -> int:
    """Get member count for an organization without logging PII."""
    try:
        gh = Github(os.environ.get("GITHUB_TOKEN"))
        members = gh.get_organization(org).get_members()
        count = members.totalCount
        logger.info(f"Organization {org} has {count} members")
        return count
    except Exception as e:
        logger.error(f"Failed to get members for {org}: {str(e)}")
        return 0


def get_copilot_enterprise(enterprise: str) -> Dict[str, Any]:
    """Get Copilot billing data for enterprise."""
    try:
        r = requests.get(
            f"{API}/enterprises/{enterprise}/copilot/billing",
            headers=_headers(),
            timeout=30
        )
        if r.ok:
            data = r.json()
            logger.info(f"Copilot enterprise data retrieved for {enterprise}")
            return data
        else:
            logger.warning(f"Failed to get Copilot enterprise data: {r.status_code}")
            return {}
    except Exception as e:
        logger.error(f"Error getting Copilot enterprise data: {str(e)}")
        return {}


def get_copilot_org(org: str) -> Dict[str, Any]:
    """Get Copilot billing data for organization."""
    try:
        r = requests.get(
            f"{API}/orgs/{org}/copilot/billing",
            headers=_headers(),
            timeout=30
        )
        if r.ok:
            data = r.json()
            logger.info(f"Copilot org data retrieved for {org}")
            return data
        else:
            logger.warning(f"Failed to get Copilot org data: {r.status_code}")
            return {}
    except Exception as e:
        logger.error(f"Error getting Copilot org data: {str(e)}")
        return {}


def get_enterprise_cloud_seats(enterprise: str) -> Dict[str, Any]:
    """Get Enterprise Cloud seats data (best effort)."""
    try:
        # This endpoint may not be available for all accounts
        r = requests.get(
            f"{API}/enterprises/{enterprise}/settings/billing/enterprise-cloud",
            headers=_headers(),
            timeout=30
        )
        if r.ok:
            return r.json()
        else:
            logger.info(f"Enterprise Cloud endpoint not available: {r.status_code}")
            return {}
    except Exception as e:
        logger.info(f"Enterprise Cloud API not available: {str(e)}")
        return {}


def collect_github(
    enterprise: str, 
    orgs: List[str], 
    pricing: Dict[str, float]
) -> Dict[str, Any]:
    """Collect all GitHub cost data."""
    
    # Collect org member counts
    org_members = []
    total_unique_members = set()
        org_members = []
        # Note: Proper unique member counting would require fetching actual member IDs
        # and tracking them across orgs, which may have privacy implications.
        # For now, we'll use the sum as an upper bound estimate
    
        for org in orgs:
            member_count = list_org_members_count(org)
            org_members.append({
                "org": org,
                "members": member_count
            })
    cop = get_copilot_enterprise(enterprise)
    
    if not cop or "seats" not in cop:
        # Fallback: sum from individual orgs
        logger.info("Falling back to per-org Copilot data")
        seats_assigned = 0
        seats_purchased = 0
        
        for org in orgs:
            org_data = get_copilot_org(org)
            if org_data:
                seats_assigned += org_data.get("seats_assigned", 0)
                seats_purchased += org_data.get("seats_purchased", 0)
        
        cop = {
            "seats_assigned": seats_assigned,
            "seats_purchased": seats_purchased
        }
    else:
        # Extract from enterprise response
        seats_data = cop.get("seats") or []
        cop = {
            "seats_assigned": seats_data[0].get("assigned", 0) if seats_data else 0,
            "seats_purchased": seats_data[0].get("purchased", 0) if seats_data else 0
        }
    
    # Calculate Copilot costs
    copilot_price_per_seat = float(pricing.get("copilot_usd_per_seat", 19.0))
    cop_cost = cop.get("seats_assigned", 0) * copilot_price_per_seat
    
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
            "price_per_seat": copilot_price_per_seat
        },
        "enterprise_cloud": {
            "seats": ec_seats,
            "monthly_cost_usd": ec_cost,
            "price_per_seat": ec_price_per_seat
        },
        "total_monthly_cost_usd": cop_cost + ec_cost
    }


def __init__():
    """Module initialization."""
    pass