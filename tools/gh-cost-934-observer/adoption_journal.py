"""Exclusive bounded hash-chain journal for owner installation/recovery.

A journal binds records, not authority. Truncation and write/fsync failures must
be diagnosed from actual state, never hidden by replaying the same operation.
"""
import hashlib
import json
import os
import stat

MAX_BYTES = 4 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def parse(data, binding):
    if len(data) > MAX_BYTES or not data or not data.endswith(b'\n'):
        raise ValueError('journal_incomplete_or_oversized')
    previous = '0' * 64
    result = []
    for index, line in enumerate(data.splitlines()):
        row = json.loads(line)
        if set(row) != {'sequence', 'binding', 'previous', 'event'}:
            raise ValueError('journal_schema')
        if row['sequence'] != index or row['binding'] != binding or row['previous'] != previous:
            raise ValueError('journal_chain_mismatch')
        if line != canonical(row):
            raise ValueError('journal_noncanonical')
        previous = digest(line)
        result.append(row['event'])
    return result, previous


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise OSError('short_journal_write')
        data = data[count:]


class Journal:
    def __init__(self, path, binding):
        if len(binding) != 64 or any(c not in '0123456789abcdef' for c in binding):
            raise ValueError('manifest_digest_required')
        self.path, self.binding = path, binding
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.sequence = 0
        self.previous = '0' * 64
        self.size = 0
        self.content_sha256 = digest(b'')
        self.failed = False
        try:
            self.emit({'phase': 'reserved'})
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            self.close()
            raise

    def emit(self, event):
        if self.failed or self.fd is None:
            raise ValueError('journal_not_writable')
        try:
            info = os.fstat(self.fd)
            current = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != self.size:
                raise ValueError('journal_file_drift')
            if (info.st_ino, info.st_dev) != (current.st_ino, current.st_dev):
                raise ValueError('journal_path_replaced')
            current_bytes = os.pread(self.fd, self.size + 1, 0)
            if digest(current_bytes) != self.content_sha256:
                raise ValueError('journal_content_drift')
            row = {'sequence': self.sequence, 'binding': self.binding,
                   'previous': self.previous, 'event': event}
            line = canonical(row)
            if self.size + len(line) + 1 > MAX_BYTES:
                raise ValueError('journal_limit')
            write_all(self.fd, line + b'\n')
            os.fsync(self.fd)
            self.content_sha256 = digest(current_bytes + line + b'\n')
            self.size += len(line) + 1
            self.sequence += 1
            self.previous = digest(line)
        except BaseException:
            self.failed = True
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
