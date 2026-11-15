"""Bounded GET-only budget capture for an existing caller; no scheduler or writes."""
from datetime import datetime, timezone
import json
import re
import subprocess
from urllib.parse import quote

from snapshot_adapter import normalize

WRAPPER = '/Users/milanmerglevsky/.codex/bin/codex-guarded-command'
ENDPOINT = re.compile(r'https://api\.github\.com/(enterprises|organizations)/[A-Za-z0-9_.-]+/settings/billing/budgets')


def gh_get(url):
    """Use existing authenticated guarded transport; never expose stderr/token material."""
    if not re.fullmatch(r'https://api\.github\.com/(?:enterprises|organizations)/[A-Za-z0-9_.-]+/settings/billing/budgets(?:/[A-Za-z0-9%_.~-]+|\?page=[0-9]+&per_page=100)?', url):
        raise ValueError('unsupported budget URL')
    try:
        result = subprocess.run(['/bin/bash', WRAPPER, 'gh', 'api', '--hostname', 'github.com',
                                 '--method', 'GET', url.removeprefix('https://api.github.com/'),
                                 '-H', 'Accept: application/vnd.github+json',
                                 '-H', 'X-GitHub-Api-Version: 2026-03-10'],
                                capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('budget transport unavailable') from None
    if result.returncode:
        raise ValueError('budget GET failed; no retry')
    try:
        value = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ValueError('invalid budget response') from None
    if not isinstance(value, dict):
        raise ValueError('invalid budget response object')
    return value


def capture(endpoint, *, get, remaining_requests, max_requests=100, clock=None):
    """Caller supplies fresh quota evidence and transport. Never publish partial data.

    Returned captures are private source evidence, not safe for logs: they can contain
    recipient names. Public consumers receive only normalize's redacted projection.
    No implicit retry, credentials acquisition, filesystem publication or activation.
    """
    if not isinstance(endpoint, str) or not ENDPOINT.fullmatch(endpoint):
        raise ValueError('invalid budget endpoint')
    if type(max_requests) is not int or not 1 <= max_requests <= 100:
        raise ValueError('invalid request limit')
    if type(remaining_requests) is not int or remaining_requests - max_requests < 2000:
        raise ValueError('shared API reserve would be crossed')
    clock = clock or (lambda: datetime.now(timezone.utc))
    listing, details, ids = [], [], set()
    calls = 0

    def read(url):
        nonlocal calls
        if calls >= max_requests:
            raise ValueError('request limit reached; no partial publication')
        calls += 1
        observed = clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError('timezone-aware clock required')
        response = get(url)
        if not isinstance(response, dict):
            raise ValueError('invalid response object')
        return {'request_url': url, 'observed_at': observed.isoformat(),
                'exit_code': 0, 'response': response}

    total = None
    for page in range(1, max_requests + 1):
        row = read(endpoint + f'?page={page}&per_page=100')
        response = row['response']
        count = response.get('total_count')
        more = response.get('has_next_page')
        budgets = response.get('budgets')
        if type(count) is not int or count < 0 or (total is not None and count != total):
            raise ValueError('listing count changed or invalid')
        if type(more) is not bool or not isinstance(budgets, list) or len(budgets) > 100:
            raise ValueError('invalid pagination')
        total = count
        for budget in budgets:
            identity = budget.get('id') if isinstance(budget, dict) else None
            if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identity) or identity in ids:
                raise ValueError('duplicate or missing budget identity')
            ids.add(identity)
        if len(ids) > total or (more and (not budgets or len(ids) >= total)):
            raise ValueError('inconsistent pagination')
        listing.append(row)
        if not more:
            break
    else:
        raise ValueError('listing incomplete')
    if len(ids) != total or calls + len(ids) > max_requests:
        raise ValueError('incomplete listing or detail request limit')
    for identity in sorted(ids):
        details.append(read(endpoint + '/' + quote(identity, safe='')))
    raw, provenance = normalize(listing, details, source_endpoint=endpoint)
    return raw, provenance, {'listing_reads': listing, 'detail_reads': details,
                             'requests_used': calls}
