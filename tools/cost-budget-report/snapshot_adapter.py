"""Normalize captured budget API reads without issuing requests or refreshing time."""
from datetime import timedelta
import hashlib
import json
import re
from urllib.parse import urlsplit, parse_qs, quote

from budget_report import amount, timestamp


def normalize(listing_reads, detail_reads, *, source_endpoint, max_spread=timedelta(minutes=5)):
    try:
        return _normalize(listing_reads, detail_reads, source_endpoint=source_endpoint, max_spread=max_spread)
    except (KeyError, TypeError, AttributeError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid captured budget observation") from exc


def _normalize(listing_reads, detail_reads, *, source_endpoint, max_spread):
    """Each capture contains response, observed_at and exit_code from the existing reader.

    Refuse incomplete enumeration, inconsistent detail/configuration, and historical
    joins. Callers retain the original captures; the output contains hashes, never
    notification recipient identities. No production billing period is inferred.
    """
    if not listing_reads or max_spread.total_seconds() <= 0:
        raise ValueError("missing listing or invalid spread")
    if not isinstance(source_endpoint, str) or not re.fullmatch(
            r"https://api\.github\.com/(?:enterprises|organizations)/[A-Za-z0-9_.-]+/settings/billing/budgets",
            source_endpoint):
        raise ValueError("invalid budget source endpoint")
    captures = [*listing_reads, *detail_reads]
    observed = []
    for capture in captures:
        if type(capture["exit_code"]) is not int or capture["exit_code"] != 0:
            raise ValueError("read failed")
        observed.append(timestamp(capture["observed_at"]))
    if max(observed) - min(observed) > max_spread:
        raise ValueError("captures belong to different observations")
    budgets, total = {}, None
    for index, capture in enumerate(listing_reads):
        url = urlsplit(capture['request_url'])
        endpoint = url._replace(query='').geturl()
        query = parse_qs(url.query, keep_blank_values=True, strict_parsing=True) if url.query else {}
        if endpoint != source_endpoint or set(query) - {'page', 'per_page'}:
            raise ValueError("listing endpoint mismatch")
        if query.get('page', ['1']) != [str(index + 1)]:
            raise ValueError("listing page mismatch")
        if 'per_page' in query and (len(query['per_page']) != 1 or not query['per_page'][0].isdigit()
                                   or not 1 <= int(query['per_page'][0]) <= 100):
            raise ValueError("invalid page size")
        page = capture["response"]
        count = page["total_count"]
        if type(count) is not int or count < 0 or (total is not None and total != count):
            raise ValueError("listing count changed")
        total = count
        if type(page["has_next_page"]) is not bool or page["has_next_page"] != (index < len(listing_reads) - 1):
            raise ValueError("incomplete or redundant pages")
        if not isinstance(page["budgets"], list):
            raise ValueError("invalid budgets list")
        for budget in page["budgets"]:
            identity = budget["id"]
            if not isinstance(identity, str) or not identity or identity in budgets:
                raise ValueError("duplicate or missing id")
            budgets[identity] = budget
    if len(budgets) != total:
        raise ValueError("incomplete enumeration")
    rows, seen = [], set()
    for capture in detail_reads:
        detail = capture["response"]
        identity = detail["id"]
        if not isinstance(identity, str) or not identity or capture['request_url'] != source_endpoint + '/' + quote(identity, safe=''):
            raise ValueError("detail endpoint mismatch")
        if identity not in budgets or identity in seen:
            raise ValueError("foreign or repeated detail")
        seen.add(identity)
        listed = budgets[identity]
        for field in ("budget_type", "budget_product_sku", "budget_scope", "budget_entity_name",
                      "budget_amount", "prevent_further_usage", "exclude_cost_center_usage", "budget_alerting"):
            if detail.get(field) != listed.get(field):
                raise ValueError("budget changed during observation")
        if type(detail["prevent_further_usage"]) is not bool:
            raise ValueError("unknown stop state")
        alert = detail.get("budget_alerting", {}).get("will_alert")
        rows.append({"id": identity, "product": detail["budget_product_sku"],
                     "budget_scope": detail["budget_scope"],
                     "budget_amount": amount(detail["budget_amount"]),
                     "consumed_amount": amount(detail["consumed_amount"]),
                     "prevent_further_usage": detail["prevent_further_usage"],
                     "alert_enabled": alert if type(alert) is bool else None})
    if seen != set(budgets):
        raise ValueError("missing details")
    raw = (json.dumps({"rows": rows}, sort_keys=True, separators=(",", ":")) + "\n").encode()
    provenance = {"schema_version": 1, "complete": True, "source_endpoint": source_endpoint,
                  "source_sha256": hashlib.sha256(raw).hexdigest(),
                  "observed_at": min(observed).isoformat(), "period": None,
                  "capture_sha256": [hashlib.sha256(json.dumps(c, sort_keys=True,
                         separators=(",", ":")).encode()).hexdigest() for c in captures]}
    return raw, provenance
