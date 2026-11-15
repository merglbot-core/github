"""Source-only transaction core. Caller supplies bounded transport and activation.

No CLI, scheduler, credentials acquisition or automatic recovery. The enclosing
runtime must cap the entire process tree; callback deadlines alone are not a
physical-memory or hard-duration guarantee.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

from attempt_journal import locked_journal
from budget_reader import capture
from pair_exchange import exchange_existing
from pair_io import read_pair
from pair_stage import stage_pair
from refresh_policy import decide


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def exclusive_private(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)


def refresh_once(*, enabled, root, journal_path, endpoint, refresh_after, get,
                 clock=lambda: datetime.now(timezone.utc), ticks=monotonic, duration=120):
    """Require an existing admitted pair; reserve attempt before any network call.

    get(url, timeout=seconds) must be a bounded GET-only transport. A failed call
    or any interrupted publication leaves a consumed, unresolved journal attempt.
    A subsequent invocation never starts another observation over that attempt.
    """
    if type(enabled) is not bool:
        raise ValueError('explicit opt-in required')
    if not enabled:
        return {'state': 'disabled', 'request_count': 0}
    if type(duration) is not int or not 1 <= duration <= 600:
        raise ValueError('invalid whole-invocation duration')
    deadline = ticks() + duration
    root, journal_path = Path(root), Path(journal_path)
    if (not root.is_absolute() or not journal_path.is_absolute()
            or journal_path.is_relative_to(root)
            or journal_path.parent.resolve(strict=True) != journal_path.parent
            or root.parent.resolve(strict=True) != root.parent):
        raise ValueError('separate admitted absolute data and journal namespaces required')
    requests, response_bytes = 0, 0

    def check_deadline():
        remaining = deadline - ticks()
        if remaining <= 0:
            raise TimeoutError('refresh deadline exhausted; no retry')
        return remaining

    def bounded_get(url):
        nonlocal requests, response_bytes
        if requests >= 100:
            raise ValueError('whole-observation request limit exhausted')
        requests += 1
        result = get(url, timeout=min(30, check_deadline()))
        check_deadline()
        response_bytes += len(json.dumps(result).encode())
        if response_bytes > 4 * 1024 * 1024:
            raise ValueError('whole-observation response bound exhausted')
        return result

    with locked_journal(journal_path) as journal:
        check_deadline()
        last = journal.records[-1] if journal.records else None
        if last and last['state'] != 'verified':
            return {'state': 'held_unresolved_attempt', 'request_count': 0}
        old = read_pair(root)
        old_identity = root.stat()
        if set(p.name for p in root.iterdir()) != {'latest.json', 'latest.provenance.json'}:
            raise ValueError('unexpected live generation members')
        attempt_state = (dict(state='published_verified',
                              snapshot_sha256=last['pins']['snapshot_sha256'],
                              provenance_sha256=last['pins']['provenance_sha256']) if last else None)
        decision = decide(enabled=True, raw=old[0], provenance=old[1], attempt=attempt_state,
                          now=clock(), endpoint=endpoint, max_age=refresh_after)
        if decision != 'refresh_required':
            return {'state': decision, 'request_count': 0}
        attempt = uuid4().hex
        pins = dict(prior_snapshot_sha256=sha(old[0]), prior_provenance_sha256=sha(old[1]))
        journal.append(attempt, 'reserved', pins)
        quota = bounded_get('https://api.github.com/rate_limit')
        remaining = quota['resources']['core']['remaining']
        raw, provenance, evidence = capture(endpoint, get=bounded_get,
            remaining_requests=remaining, max_requests=99, clock=clock)
        provenance_raw = (json.dumps(provenance, sort_keys=True, separators=(',', ':'))+'\n').encode()
        capture_raw = (json.dumps(evidence, sort_keys=True, separators=(',', ':'))+'\n').encode()
        if len(capture_raw) > 5 * 1024 * 1024:
            raise ValueError('private capture bound exhausted')
        exclusive_private(journal_path.parent/('capture-'+attempt+'.json'), capture_raw)
        pins.update(snapshot_sha256=sha(raw), provenance_sha256=sha(provenance_raw), capture_sha256=sha(capture_raw))
        journal.append(attempt, 'captured', pins)
        check_deadline()
        staged = stage_pair(root.parent, raw, provenance_raw, attempt=attempt)
        journal.append(attempt, 'staged', pins)
        current_identity = root.stat()
        if ((old_identity.st_dev, old_identity.st_ino) !=
                (current_identity.st_dev, current_identity.st_ino) or read_pair(root) != old):
            raise ValueError('live pair drift; preserve evidence, no exchange')
        check_deadline()
        exchange_existing(root, staged)
        journal.append(attempt, 'exchanged', pins)
        # Readback/receipt finalization is mandatory even if the soft deadline has
        # just expired. The outer hard cap leaves an unresolved attempt on kill.
        if read_pair(root) != (raw, provenance_raw) or read_pair(staged) != old:
            raise ValueError('postimage readback failed; no blind rollback')
        journal.append(attempt, 'verified', pins)
        return {'state': 'published_verified', 'request_count': requests, 'attempt': attempt,
                'snapshot_sha256': sha(raw), 'provenance_sha256': sha(provenance_raw),
                'retained_prior_generation': str(staged)}
