"""
GitHub Enterprise and Copilot cost monitoring.
"""

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

API = "https://api.github.com"
GRAPHQL_API = "https://api.github.com/graphql"
API_VERSION = "2026-03-10"
COPILOT_UNAVAILABLE_STATUSES = {404}


class GitHubCopilotOrgAuthorizationError(RuntimeError):
    """The aggregate organization endpoint rejected the configured token."""


class GitHubCopilotOrgConfigurationError(RuntimeError):
    """The aggregate organization endpoint reported a billing configuration problem."""


class GitHubCopilotOrgRequestError(RuntimeError):
    """The aggregate organization endpoint failed outside auth/configuration errors."""


class GitHubCopilotOrgSchemaError(RuntimeError):
    """The aggregate organization response did not match the reviewed schema."""


class GitHubCopilotAggregateUnavailableError(RuntimeError):
    """No configured organization returned authoritative aggregate billing data."""


def _enterprise_token() -> str:
    """Return the separately scoped token used for enterprise billing reads."""

    token = os.environ.get("ENTERPRISE_GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("Missing ENTERPRISE_GITHUB_TOKEN")
    return token


def _headers() -> dict[str, str]:
    """Get headers for read-only enterprise and organization API calls."""

    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "Authorization": f"Bearer {_enterprise_token()}",
    }


def get_org_members_count(org: str) -> int:
    """Get only the aggregate organization member count via GraphQL."""

    response = requests.post(
        GRAPHQL_API,
        headers=_headers(),
        json={
            "query": "query($login: String!) { organization(login: $login) { membersWithRole { totalCount } } }",
            "variables": {"login": org},
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"GitHub organization aggregate request failed with status {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("GitHub organization aggregate response is not valid JSON") from exc

    organization = payload.get("data", {}).get("organization") if isinstance(payload, dict) else None
    members = organization.get("membersWithRole") if isinstance(organization, dict) else None
    count = members.get("totalCount") if isinstance(members, dict) else None
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise RuntimeError("GitHub organization response has invalid aggregate member schema")

    logger.info("Organization %s has %d members", org, count)
    return count


def _normalize_copilot_org_billing(data: dict[str, Any]) -> dict[str, int]:
    """Normalize the current aggregate-only Copilot organization response.

    The endpoint's ``seat_breakdown.total`` is the billable seat total.  Do not
    use the enterprise seat-list endpoint: its response contains identities and
    is not needed for cost reporting.
    """

    breakdown = data.get("seat_breakdown")
    total = breakdown.get("total") if isinstance(breakdown, dict) else None
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise GitHubCopilotOrgSchemaError("GitHub Copilot organization response has invalid aggregate seat schema")

    return {"seats_assigned": total, "seats_purchased": total}


def get_copilot_org(org: str) -> dict[str, Any]:
    """Get privacy-preserving aggregate Copilot billing data for an organization."""
    response = requests.get(
        f"{API}/orgs/{org}/copilot/billing",
        headers=_headers(),
        timeout=30,
    )
    if response.ok:
        try:
            normalized = _normalize_copilot_org_billing(response.json())
        except ValueError as exc:
            raise GitHubCopilotOrgSchemaError("GitHub Copilot organization response is not valid JSON") from exc
        logger.info("Copilot aggregate org data retrieved for %s", org)
        return normalized
    if response.status_code in COPILOT_UNAVAILABLE_STATUSES:
        logger.info("Copilot aggregate org endpoint unavailable for %s (status %d)", org, response.status_code)
        return {}
    if response.status_code in (401, 403):
        raise GitHubCopilotOrgAuthorizationError(
            f"GitHub Copilot organization billing request failed with status {response.status_code}"
        )
    if response.status_code == 422:
        raise GitHubCopilotOrgConfigurationError("GitHub Copilot organization billing request failed with status 422")
    raise GitHubCopilotOrgRequestError(
        f"GitHub Copilot organization billing request failed with status {response.status_code}"
    )


def get_enterprise_cloud_seats(enterprise: str) -> dict[str, Any]:
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


def collect_github(enterprise: str, orgs: list[str], pricing: dict[str, float]) -> dict[str, Any]:
    """Collect all GitHub cost data."""

    # Validate authentication before any endpoint handling.
    _enterprise_token()

    # Enterprise member counts are only cost-relevant when that configured
    # price is non-zero. Avoid unnecessary member APIs for the normal zero-price
    # configuration; when needed, fetch only GraphQL aggregate totalCount.
    ec_price_per_seat = float(pricing.get("enterprise_cloud_usd_per_seat", 0.0))
    org_members = []
    if ec_price_per_seat > 0:
        for org in orgs:
            member_count = get_org_members_count(org)
            org_members.append({"org": org, "members": member_count})
    # Sum authoritative organization aggregates. The enterprise Copilot seat
    # endpoint is intentionally not used because it returns seat identities.
    seats_assigned = 0
    seats_purchased = 0
    org_sources = 0
    for org in orgs:
        org_data = get_copilot_org(org)
        if org_data:
            org_sources += 1
            seats_assigned += org_data["seats_assigned"]
            seats_purchased += org_data["seats_purchased"]

    if org_sources == 0:
        raise GitHubCopilotAggregateUnavailableError(
            "No GitHub Copilot billing source returned authoritative aggregate data"
        )

    cop = {"seats_assigned": seats_assigned, "seats_purchased": seats_purchased}

    # Calculate Copilot costs (based on purchased seats for billing)
    copilot_price_per_seat = float(pricing.get("copilot_usd_per_seat", 19.0))
    cop_cost = cop.get("seats_purchased", 0) * copilot_price_per_seat

    ec_seats = 0
    ec_cost = 0.0
    if ec_price_per_seat > 0:
        # This source is only relevant when Enterprise Cloud pricing is enabled.
        ec = get_enterprise_cloud_seats(enterprise)
        ec_seats = int(ec.get("total_seats", 0) or 0)
        ec_cost = ec_seats * ec_price_per_seat

        # If no EC data, estimate from aggregate organization member counts.
        if ec_seats == 0:
            ec_seats = sum(om["members"] for om in org_members)
            ec_cost = ec_seats * ec_price_per_seat

    return {
        "org_members": org_members,
        "total_members": sum(om["members"] for om in org_members) if org_members else None,
        "member_census_status": "collected" if org_members else "not_collected_unpriced",
        "copilot": {
            "seats_assigned": cop.get("seats_assigned", 0),
            "seats_purchased": cop.get("seats_purchased", 0),
            "monthly_cost_usd": cop_cost,
            "price_per_seat": copilot_price_per_seat,
        },
        "enterprise_cloud": {"seats": ec_seats, "monthly_cost_usd": ec_cost, "price_per_seat": ec_price_per_seat},
        "total_monthly_cost_usd": cop_cost + ec_cost,
    }
