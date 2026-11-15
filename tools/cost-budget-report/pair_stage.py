"""Prepare an immutable private generation; never replace a consumer input.

A failed preparation is retained for inspection. Publication, locking and the
attempt journal are separate obligations of the future transaction adapter.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import re
import tempfile

from pair_io import MAX_FILE_BYTES, read_pair


def stage_pair(parent, raw, provenance, *, attempt=None):
    parent = Path(parent)
    if not parent.is_absolute() or parent.resolve(strict=True) != parent:
        raise ValueError('absolute non-symlink parent required')
    identity = parent.stat()
    if (not stat.S_ISDIR(identity.st_mode) or identity.st_uid != os.getuid()
            or identity.st_mode & 0o022):
        raise ValueError('unsafe parent')
    if any(not isinstance(value, bytes) or not 0 < len(value) <= MAX_FILE_BYTES
           for value in (raw, provenance)):
        raise ValueError('invalid bounded data bytes')
    record = json.loads(provenance)
    if (not isinstance(record, dict) or record.get('complete') is not True
            or record.get('source_sha256') != hashlib.sha256(raw).hexdigest()):
        raise ValueError('invalid pair binding')
    # Verify the endpoint/time/schema through the admitted producer separately.
    # This helper preserves bytes; it never invents or repairs provenance.
    snapshot = json.loads(raw)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('rows'), list):
        raise ValueError('invalid snapshot')
    if attempt is None:
        directory = Path(tempfile.mkdtemp(prefix='.budget-generation-', dir=parent))
    else:
        if not isinstance(attempt, str) or not re.fullmatch('[a-f0-9]{32}', attempt):
            raise ValueError('invalid attempt identity')
        directory = parent / ('.budget-generation-' + attempt)
        directory.mkdir(mode=0o700)
    for name, value in (('latest.json', raw), ('latest.provenance.json', provenance)):
        fd = os.open(directory/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if read_pair(directory) != (raw, provenance):
        raise ValueError('prepared pair readback mismatch')
    # A changed parent is not permission to publish the prepared generation.
    after = parent.stat()
    if (identity.st_dev, identity.st_ino, identity.st_uid, identity.st_mode) != (
            after.st_dev, after.st_ino, after.st_uid, after.st_mode):
        raise ValueError('parent identity changed')
    return directory
