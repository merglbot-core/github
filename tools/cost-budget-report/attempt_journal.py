"""Durable append-only attempt states for a future refresh adapter.

No automatic repair, truncation, deletion or retry after an incomplete attempt.
The journal lock must be held across capture, stage, exchange and verification.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

LIMIT = 1024 * 1024
NEXT = {'reserved': 'captured', 'captured': 'staged', 'staged': 'exchanged',
        'exchanged': 'verified'}
FIELDS = {'snapshot_sha256', 'provenance_sha256', 'prior_snapshot_sha256',
          'prior_provenance_sha256', 'capture_sha256'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


class Journal:
    def __init__(self, fd):
        self.fd = fd
        self.records = []
        self.previous = '0' * 64
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, LIMIT + 1)
        if len(raw) > LIMIT or (raw and not raw.endswith(b'\n')):
            raise ValueError('journal incomplete or exceeds bound; no retry')
        for line in raw.splitlines():
            value = json.loads(line)
            record = value['record']
            if value['previous'] != self.previous or value['sha256'] != hashlib.sha256(
                    self.previous.encode() + canonical(record)).hexdigest():
                raise ValueError('journal chain mismatch')
            self._validate(record)
            self.records.append(record)
            self.previous = value['sha256']

    def _validate(self, record):
        if set(record) != {'attempt', 'state', 'pins'}:
            raise ValueError('unexpected journal fields')
        if not re.fullmatch('[a-f0-9]{32}', record['attempt']):
            raise ValueError('invalid attempt identity')
        pins = record['pins']
        if not isinstance(pins, dict) or set(pins) - FIELDS or any(
                not isinstance(v, str) or not re.fullmatch('[a-f0-9]{64}', v) for v in pins.values()):
            raise ValueError('invalid evidence pins')
        previous = self.records[-1] if self.records else None
        if record['state'] == 'reserved':
            if previous and previous['state'] != 'verified':
                raise ValueError('prior attempt incomplete; no replay')
            if any(r['attempt'] == record['attempt'] for r in self.records):
                raise ValueError('attempt already consumed')
        elif (not previous or previous['attempt'] != record['attempt']
              or NEXT.get(previous['state']) != record['state']):
            raise ValueError('invalid transition')
        if previous and previous['attempt'] == record['attempt']:
            if any(pins.get(k) != v for k, v in previous['pins'].items()):
                raise ValueError('evidence binding changed')
        if record['state'] != 'reserved' and not {'snapshot_sha256', 'provenance_sha256'} <= pins.keys():
            raise ValueError('captured data pins required')

    def append(self, attempt, state, pins):
        record = dict(attempt=attempt, state=state, pins=dict(pins))
        self._validate(record)
        digest = hashlib.sha256(self.previous.encode() + canonical(record)).hexdigest()
        line = canonical(dict(previous=self.previous, record=record, sha256=digest)) + b'\n'
        if os.fstat(self.fd).st_size + len(line) > LIMIT:
            raise ValueError('journal full; preserve evidence')
        offset = 0
        while offset < len(line):
            count = os.write(self.fd, line[offset:])
            if count <= 0:
                raise OSError('journal write failed')
            offset += count
        os.fsync(self.fd)
        self.records.append(record)
        self.previous = digest


@contextmanager
def locked_journal(path):
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve(strict=True) != path.parent:
        raise ValueError('absolute non-symlink parent required')
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        ps = os.fstat(parent)
        if ps.st_uid != os.getuid() or ps.st_mode & 0o022:
            raise ValueError('unsafe journal parent')
        fd = os.open(path.name, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
        s = os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1
                or s.st_mode & 0o077):
            raise ValueError('unsafe journal')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fsync(parent)
        yield Journal(fd)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)
