"""Native atomic exchange primitive; transaction/admission must surround its use.

macOS SDK sys/stdio.h defines RENAME_SWAP=2. There is deliberately no fallback to
sequential renames. Both generations must remain immutable and retained after
exchange for descriptor-pinned readers. No CLI or automatic invocation exists.
"""
import ctypes
import os
from pathlib import Path
import stat
import sys


def exchange_existing(left, right):
    left, right = Path(left), Path(right)
    if (sys.platform != 'darwin' or not left.is_absolute() or left == right
            or left.parent != right.parent or left.parent.resolve(strict=True) != left.parent):
        raise ValueError('native sibling directory exchange required')
    parent = os.open(left.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        ps = os.fstat(parent)
        if ps.st_uid != os.getuid() or ps.st_mode & 0o022:
            raise ValueError('unsafe parent')
        before = []
        for path in (left, right):
            s = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o022:
                raise ValueError('unsafe generation directory')
            before.append((s.st_dev, s.st_ino))
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameatx_np
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, os.fsencode(left.name), parent, os.fsencode(right.name), 2) != 0:
            raise OSError(ctypes.get_errno(), 'atomic exchange refused')
        os.fsync(parent)
        after = [os.stat(p.name, dir_fd=parent, follow_symlinks=False) for p in (left, right)]
        if [(s.st_dev, s.st_ino) for s in after] != list(reversed(before)):
            raise ValueError('exchange identity mismatch; preserve both generations, no retry')
    finally:
        os.close(parent)
