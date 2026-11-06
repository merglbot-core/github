"""Proposed installation transaction; owner-only adapter/review still required.

This module has no command-line entry point and does not select host targets.
Source admission, exact owner authorization and supported owner-script emission
must precede use on protected files. Tests use isolated temporary regular files.
"""

import ctypes
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


class Refused(RuntimeError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reject_acl(path, *, directory=False):
    """Admit only empty native ACLs; mode bits alone cannot pin access."""
    if sys.platform != "darwin":
        raise Refused("Native ACL admission requires macOS")
    library = ctypes.CDLL(None, use_errno=True)
    library.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    library.acl_get_fd_np.restype = ctypes.c_void_p
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                 | (os.O_DIRECTORY if directory else 0))
    acl = None
    try:
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            raise Refused("ACL target type changed")
        ctypes.set_errno(0)
        acl = library.acl_get_fd_np(fd, 0x100)  # ACL_TYPE_EXTENDED
        # Darwin's filesec_get_property returns ENOENT for an absent ACL.
        # The object is addressed through an open, validated descriptor.
        # Even explicitly stored empty ACLs fail closed rather than assuming
        # ACL flags or future inheritance cannot affect admission.
        if acl or ctypes.get_errno() != errno.ENOENT:
            raise Refused("ACL-bearing target or unreadable ACL refused")
    finally:
        if acl:
            library.acl_free(acl)
        os.close(fd)


def sync_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Refused("Persistence target is not a regular file")
        os.fsync(fd)
        # Darwin F_FULLFSYNC asks the drive to flush data to durable media.
        fcntl.fcntl(fd, 51)
    finally:
        os.close(fd)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
        fcntl.fcntl(fd, 51)
    finally:
        os.close(fd)


DEFAULT_MAX_BYTES = 1024 * 1024
PROFILE_MAX_BYTES = 16 * DEFAULT_MAX_BYTES
TRANSACTION_MAX_BYTES = 64 * DEFAULT_MAX_BYTES


def limits(maximum, total=TRANSACTION_MAX_BYTES):
    if (type(maximum) is not int or not 1 <= maximum <= PROFILE_MAX_BYTES
            or type(total) is not int or not maximum <= total <= TRANSACTION_MAX_BYTES):
        raise Refused("Invalid bounded transaction limits")


def snapshot(path, *, maximum=DEFAULT_MAX_BYTES):
    limits(maximum)
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise Refused("Target is not a regular non-symlink file")
    reject_acl(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise Refused("Target identity changed before read")
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise Refused("Unexpectedly large installation target")
    attrs = {}
    names = subprocess.check_output(["/usr/bin/xattr", str(path)], text=True)
    for name in names.splitlines():
        value = subprocess.check_output(["/usr/bin/xattr", "-px", name, str(path)])
        attrs[name] = digest(bytes.fromhex(value.decode().strip()))
    reject_acl(path)
    final = path.lstat()
    if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns,
        final.st_ctime_ns, final.st_uid, final.st_gid, final.st_mode, getattr(final, "st_flags", 0)) != (
        info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns, info.st_uid, info.st_gid, info.st_mode, getattr(info, "st_flags", 0)
    ):
        raise Refused("Target changed during metadata read")
    return {
        "sha256": digest(data),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "flags": getattr(info, "st_flags", 0),
        "xattrs": attrs,
    }


def expect(path, expected, *, maximum=DEFAULT_MAX_BYTES):
    if snapshot(path, maximum=maximum) != expected:
        raise Refused("File bytes or required metadata changed")


def copy_preserving(source, target):
    # Native macOS cp preserves xattrs and required ownership/mode/flags.
    subprocess.run(["/bin/cp", "-p", str(source), str(target)], check=True)


def install(entries, backup_parent, invariant_check=lambda: None, journal=lambda receipt: None,
            generated_attributes=(), *, target_max_bytes=DEFAULT_MAX_BYTES,
            total_max_bytes=TRANSACTION_MAX_BYTES):
    """Stage all files and verify all backups before the first replacement.

    No automatic rollback: failure returns a partial receipt and leaves verified
    backups for a distinct admitted owner rollback after live readback.
    The adapter must admit target paths, source bytes, pins and authorization.
    """
    from .copy_admission import admit_copy_snapshot
    limits(target_max_bytes, total_max_bytes)
    entries = list(entries)
    # Validate even an empty transaction's policy before any filesystem write.
    for row in entries:
        admit_copy_snapshot(row["previous"], row["previous"], generated_attributes)
    paths = [Path(row["target"]).absolute() for row in entries]
    if not paths or len(paths) != len(set(paths)):
        raise Refused("Empty or duplicate target list")
    total = 0
    for row in entries:
        if type(row["source_bytes"]) is not bytes or len(row["source_bytes"]) > target_max_bytes:
            raise Refused("Candidate exceeds transaction limit")
        expect(row["target"], row["previous"], maximum=target_max_bytes)
        total += max(len(row["source_bytes"]), Path(row["target"]).stat().st_size)
        if total > total_max_bytes:
            raise Refused("Physical transaction payloads exceed total limit")
        if digest(row["source_bytes"]) != row["source_sha256"]:
            raise Refused("Source hash mismatch")
    invariant_check()
    parent = Path(backup_parent)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or parent.is_symlink():
        raise Refused("Backup parent is not a real directory")
    backup_dir = Path(tempfile.mkdtemp(prefix="session-context-", dir=parent))
    os.chmod(backup_dir, 0o700)
    sync_directory(parent)
    receipt = {"state": "prepared", "backup_dir": str(backup_dir), "rows": [],
               "copy_generated_attributes": list(generated_attributes)}
    if target_max_bytes != DEFAULT_MAX_BYTES:
        receipt["target_max_bytes"] = target_max_bytes
    staged = []
    try:
        for index, row in enumerate(entries):
            target = Path(row["target"])
            backup = backup_dir / str(index)
            copy_preserving(target, backup)
            backup_snapshot = admit_copy_snapshot(row["previous"], snapshot(backup, maximum=target_max_bytes), generated_attributes)
            sync_file(backup)
            sync_directory(backup_dir)
            fd, temporary = tempfile.mkstemp(prefix=".session-context-", dir=target.parent)
            os.close(fd)
            temporary = Path(temporary)
            staged.append(temporary)
            copy_preserving(target, temporary)
            staged_snapshot = admit_copy_snapshot(row["previous"], snapshot(temporary, maximum=target_max_bytes), generated_attributes)
            fd = os.open(temporary, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(fd, "wb") as stream:
                stream.write(row["source_bytes"])
                stream.flush()
                os.fsync(stream.fileno())
            candidate = dict(staged_snapshot, sha256=row["source_sha256"])
            expect(temporary, candidate, maximum=target_max_bytes)
            sync_file(temporary)
            sync_directory(target.parent)
            receipt["rows"].append({
                "target": str(target), "backup": str(backup),
                "previous": row["previous"], "candidate": candidate,
                "backup_snapshot": backup_snapshot,
                "replacement_attempted": False, "replacement_verified": False,
            })
        journal(receipt)
        invariant_check()
        for row in entries:
            expect(row["target"], row["previous"], maximum=target_max_bytes)
        for temporary, row in zip(staged, receipt["rows"]):
            expect(row["target"], row["previous"], maximum=target_max_bytes)
            row["replacement_attempted"] = True
            journal(receipt)
            invariant_check()
            expect(row["target"], row["previous"], maximum=target_max_bytes)
            os.replace(temporary, row["target"])
            sync_directory(Path(row["target"]).parent)
            expect(row["target"], row["candidate"], maximum=target_max_bytes)
            row["replacement_verified"] = True
            journal(receipt)
        invariant_check()
        for row in receipt["rows"]:
            expect(row["target"], row["candidate"], maximum=target_max_bytes)
        receipt["state"] = "installed_verified"
    except Exception as error:
        receipt["state"] = "partial_requires_live_readback" if any(
            row["replacement_attempted"] for row in receipt["rows"]
        ) else "refused_before_replacement"
        receipt["error_class"] = type(error).__name__
    finally:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
    journal(receipt)
    return receipt


def _rollback_inputs(receipt):
    """Shared read-only refusal checks; never reserve a journal or stage data."""
    from .copy_admission import admit_copy_snapshot
    maximum = receipt.get("target_max_bytes", DEFAULT_MAX_BYTES)
    limits(maximum)
    raw_policy = receipt.get("copy_generated_attributes", [])
    if type(raw_policy) is not list:
        raise Refused("Invalid receipt native-copy metadata policy")
    policy = tuple(raw_policy)
    rows = receipt["rows"]
    actions = []
    for row in rows:
        backup_snapshot = row.get("backup_snapshot", row["previous"])
        admit_copy_snapshot(row["previous"], backup_snapshot, policy)
        expect(row["backup"], backup_snapshot, maximum=maximum)
        current = snapshot(row["target"], maximum=maximum)
        if current == row["previous"]:
            continue
        if current != row["candidate"]:
            raise Refused("Rollback target differs from admitted candidate")
        actions.append(row)
    return maximum, policy, rows, actions


def rollback_preflight(receipt):
    """Validate every backup/target without mutation; not a recovery authority."""
    _, _, _, actions = _rollback_inputs(receipt)
    return {"state": "rollback_preflight_passed", "restorable_files": len(actions)}


def rollback(receipt, invariant_check=lambda: None, journal=lambda receipt: None):
    """Distinct owner action: repeat checks and never overwrite a later edit."""
    from .copy_admission import admit_copy_snapshot
    maximum, policy, rows, actions = _rollback_inputs(receipt)
    invariant_check()
    result = {"state": "rollback_prepared", "restored_files": 0,
              "attempted_targets": [], "restored_targets": [], "restored_snapshots": {}}
    journal(result)
    try:
        for row in actions:
            expect(row["target"], row["candidate"], maximum=maximum)
            fd, temporary = tempfile.mkstemp(prefix=".session-context-rollback-", dir=Path(row["target"]).parent)
            os.close(fd)
            temporary = Path(temporary)
            try:
                copy_preserving(row["backup"], temporary)
                restored = admit_copy_snapshot(row.get("backup_snapshot", row["previous"]),
                                               snapshot(temporary, maximum=maximum), policy)
                sync_file(temporary)
                sync_directory(temporary.parent)
                expect(row["target"], row["candidate"], maximum=maximum)
                result["attempted_targets"].append(row["target"])
                journal(result)
                invariant_check()
                expect(row["target"], row["candidate"], maximum=maximum)
                expect(row["backup"], row.get("backup_snapshot", row["previous"]), maximum=maximum)
                os.replace(temporary, row["target"])
                sync_directory(Path(row["target"]).parent)
                expect(row["target"], restored, maximum=maximum)
                result["restored_snapshots"][row["target"]] = restored
                result["restored_targets"].append(row["target"])
                result["restored_files"] += 1
                journal(result)
            finally:
                temporary.unlink(missing_ok=True)
        invariant_check()
        for row in rows:
            expect(row["target"], result["restored_snapshots"].get(row["target"], row["previous"]), maximum=maximum)
        result["state"] = "rollback_verified"
    except Exception as error:
        result["state"] = "rollback_incomplete_requires_live_readback"
        result["error_class"] = type(error).__name__
        journal(result)
        raise
    journal(result)
    return result
