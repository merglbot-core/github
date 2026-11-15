"""Read an immutable data generation through one directory descriptor.

The publisher must retain displaced generations unchanged. This is a source-only
helper, not a publisher, installer or authority to modify live budget inputs.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

MAX_FILE_BYTES = 1024 * 1024


def read_pair(root):
    """Return exact snapshot and provenance bytes or refuse, without retry.

    Opening the directory once pins a generation even if its parent entry is
    atomically exchanged by the publisher. O_NOFOLLOW rejects symlink files.
    Hash binding additionally detects a writer that mutates a pinned generation.
    """
    root = Path(root)
    if not root.is_absolute() or root.parent.resolve(strict=True) != root.parent:
        raise ValueError('absolute non-symlink parent required')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        ds = os.fstat(directory)
        if ds.st_uid != os.getuid() or ds.st_mode & 0o022:
            raise ValueError('unsafe directory ownership or mode')
        values = []
        for name in ('latest.json', 'latest.provenance.json'):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                        or before.st_mode & 0o022 or before.st_nlink != 1
                        or not 0 < before.st_size <= MAX_FILE_BYTES):
                    raise ValueError('unsafe data file')
                chunks = []
                size = 0
                while True:
                    block = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size))
                    if not block:
                        break
                    chunks.append(block)
                    size += len(block)
                    if size > MAX_FILE_BYTES:
                        raise ValueError('data file exceeds bound')
                after = os.fstat(fd)
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns,
                                      s.st_ctime_ns, s.st_mode, s.st_uid, s.st_nlink)
                if identity(before) != identity(after) or size != before.st_size:
                    raise ValueError('data file changed during read')
                values.append(b''.join(chunks))
            finally:
                os.close(fd)
        raw, provenance = values
        p = json.loads(provenance)
        if (not isinstance(p, dict) or p.get('source_sha256') != hashlib.sha256(raw).hexdigest()
                or p.get('complete') is not True):
            raise ValueError('incomplete or mismatched pair')
        return raw, provenance
    finally:
        os.close(directory)
