"""Existing observer flock only; file transactions use the pinned native library."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat


def no_symlink(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('absolute_path_required')
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('symlink_refused')


@contextmanager
def observer_lock(path):
    """Use the existing inode; never replace/unlink/create the observer lock."""
    no_symlink(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('unsafe_lock_inode')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        live = os.stat(path, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (live.st_dev, live.st_ino):
            raise ValueError('lock_inode_changed')
        yield
    finally:
        os.close(fd)
