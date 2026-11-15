"""Offline refresh decisions for the existing digest; no transport or host writes.

An execution adapter must hold its exclusion lock, re-read the input/state and
persist intent before fetching. This policy does not authorize activation.
"""
from datetime import timedelta
import hashlib
import json

from budget_reader import ENDPOINT
from budget_report import timestamp


def decide(*, enabled, raw, provenance, attempt, now, endpoint,
           max_age=timedelta(hours=24)):
    """Fail closed on unresolved attempts and invalid clocks; never reset state.

    A failed or interrupted operation requires explicit disposition. A completed
    observation may be refreshed only on a later invocation after expiry. Missing
    or malformed source evidence requires a new bounded observation, never a new
    timestamp over the same bytes. No quota/network operation is implied by reuse.
    """
    if type(enabled) is not bool:
        raise ValueError('explicit boolean opt-in required')
    if not enabled:
        return 'disabled'
    if now.tzinfo is None or now.utcoffset() is None or max_age <= timedelta(0):
        raise ValueError('invalid freshness clock')
    if not isinstance(endpoint, str) or not ENDPOINT.fullmatch(endpoint):
        raise ValueError('unsupported endpoint')
    if attempt is not None:
        if not isinstance(attempt, dict) or attempt.get('state') != 'published_verified':
            return 'held_unresolved_attempt'
        if (not isinstance(attempt.get('snapshot_sha256'), str)
                or not isinstance(attempt.get('provenance_sha256'), str)):
            return 'held_invalid_attempt'
        # Published state is evidence only when it still binds this exact pair.
        if (not isinstance(raw, bytes) or not isinstance(provenance, bytes)
                or hashlib.sha256(raw).hexdigest() != attempt['snapshot_sha256']
                or hashlib.sha256(provenance).hexdigest() != attempt['provenance_sha256']):
            return 'held_published_pair_drift'
    try:
        if not isinstance(raw, bytes) or not isinstance(provenance, bytes):
            return 'refresh_required'
        value = json.loads(provenance)
        snapshot = json.loads(raw)
        if (type(value.get('schema_version')) is not int or value['schema_version'] != 1
                or value.get('complete') is not True
                or value.get('source_endpoint') != endpoint
                or value.get('source_sha256') != hashlib.sha256(raw).hexdigest()
                or not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list)):
            return 'refresh_required'
        observed = timestamp(value['observed_at'])
        if observed > now:
            return 'held_future_observation'
        if now - observed <= max_age:
            return 'reuse_existing_pair'
        return 'refresh_required'
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return 'refresh_required'
