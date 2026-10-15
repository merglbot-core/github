"""Bounded, read-only npm evidence for the two existing package consumers.

This does not establish the caller's effective token identity or retire a PAT.
The entrypoint requires a trusted Actions caller; repository context is a guard,
not authentication. npm retains responsibility for the caller's existing auth.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


REGISTRY = "https://npm.pkg.github.com"
SCOPES = {
    "core": ("merglbot-core/merglbot-admin", (
        "@merglbot-core/auth-common", "@merglbot-core/project-management-app")),
    "proteinaco": ("merglbot-proteinaco/proteinaco-web", (
        "@merglbot-proteinaco/btf-viz",)),
}
VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                     r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z")
TAG = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}\Z")
JSON_LIMIT = 4 * 1024 * 1024
DISK_LIMIT = 256 * 1024 * 1024
MAX_VERSIONS = 1000


class InventoryGap(ValueError):
    """Only fixed, safe reason codes belong in this exception."""


def require(condition, code):
    if not condition:
        raise InventoryGap(code)


def valid_version(value):
    return type(value) is str and len(value) <= 128 and VERSION.fullmatch(value)


def sri(value):
    require(type(value) is str and value.startswith("sha512-"), "sha512_required")
    try:
        digest = base64.b64decode(value[7:], validate=True)
    except (ValueError, TypeError):
        raise InventoryGap("invalid_integrity") from None
    require(len(digest) == 64, "invalid_integrity")
    return "sha512-" + base64.b64encode(digest).decode("ascii")


def registry_url(value):
    try:
        parsed = urlsplit(value) if type(value) is str else None
        valid = (parsed is not None and parsed.scheme == "https"
                 and parsed.netloc == "npm.pkg.github.com"
                 and parsed.path.startswith("/") and not parsed.query
                 and not parsed.fragment)
    except ValueError:
        valid = False
    require(valid, "unexpected_archive_origin")


def locked_versions(lock, allowed):
    require(type(lock) is dict and lock.get("lockfileVersion") in (2, 3)
            and type(lock.get("packages")) is dict, "unsupported_lockfile")
    pins = {name: {} for name in allowed}
    for path, row in lock["packages"].items():
        require(type(path) is str and type(row) is dict, "invalid_lock_entry")
        if "node_modules/" not in path:
            continue
        name = path.rsplit("node_modules/", 1)[1]
        resolved = row.get("resolved", "")
        private = (name.startswith("@merglbot-") or name in allowed
                   or (type(resolved) is str and "npm.pkg.github.com" in resolved))
        if not private:
            continue
        require(name in allowed, "unmapped_private_package")
        require(row.get("name", name) == name and not row.get("link"),
                "unsupported_private_alias_or_link")
        registry_url(resolved)
        version = row.get("version")
        require(valid_version(version), "invalid_locked_version")
        digest = sri(row.get("integrity"))
        require(version not in pins[name] or pins[name][version] == digest,
                "conflicting_lock_integrity")
        pins[name][version] = digest
    require(all(pins.values()), "required_package_missing")
    return pins


def read_lock(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(JSON_LIMIT + 1)
        require(len(raw) <= JSON_LIMIT, "lockfile_too_large")
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, RecursionError) as error:
        if isinstance(error, InventoryGap):
            raise
        raise InventoryGap("lockfile_unreadable") from None


class NpmReader:
    """No shell, login, publish, auth inspection, or provider error passthrough."""

    def __init__(self, root, *, project_context=None, deadline_seconds=300):
        self.root = Path(root)
        self.project_context = Path(project_context) if project_context is not None else self.root
        self.deadline = time.monotonic() + deadline_seconds
        self.env = dict(os.environ)
        self.env.update({"npm_config_cache": str(self.root / "cache"),
                         "npm_config_logs_max": "0", "npm_config_loglevel": "silent",
                         "npm_config_update_notifier": "false"})

    def disk_guard(self):
        # A bounded sample of our private workspace, not a filesystem quota.
        count = total = 0
        for parent, _, files in os.walk(self.root):
            for name in files:
                count += 1
                require(count <= 4096, "npm_workspace_limit")
                try:
                    total += (Path(parent) / name).stat().st_size
                except FileNotFoundError:
                    continue  # npm may atomically rename its cache entries.
                require(total <= DISK_LIMIT, "npm_workspace_limit")

    def __call__(self, args, cwd):
        require(args[0] in ("view", "pack"), "unsupported_npm_operation")
        flags = ["--json", "--ignore-scripts", "--registry=" + REGISTRY,
                 "--fetch-retries=0", "--fetch-timeout=20000"]
        # An explicit scope registry beats the caller's existing scope mapping.
        for scope in ("@merglbot-core", "@merglbot-proteinaco"):
            flags.append("--" + scope + ":registry=" + REGISTRY)
        try:
            # Preserve npm's trusted project-local configuration. Cache and pack
            # output remain explicitly directed to the private workspace.
            process = subprocess.Popen(["npm", *args, *flags], cwd=self.project_context,
                                       env=self.env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
        except OSError:
            raise InventoryGap("npm_unavailable") from None
        output = bytearray()
        size = 0
        end = min(self.deadline, time.monotonic() + 45)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, True)
                selector.register(process.stderr, selectors.EVENT_READ, False)
                while selector.get_map() or process.poll() is None:
                    require(time.monotonic() < end, "npm_deadline")
                    self.disk_guard()
                    for key, _ in selector.select(0.05):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        require(size <= JSON_LIMIT, "npm_output_limit")
                        if key.data:
                            output.extend(chunk)
            require(process.wait() == 0, "npm_read_failed")
            self.disk_guard()
            try:
                return json.loads(output)
            except (ValueError, RecursionError):
                raise InventoryGap("npm_invalid_json") from None
        finally:
            # Include descendants holding inherited pipes, even if npm exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def catalogue(reader, name, cwd):
    summary = reader(["view", name, "versions", "dist-tags"], cwd)
    require(type(summary) is dict, "invalid_catalogue")
    versions, tags = summary.get("versions"), summary.get("dist-tags")
    if type(versions) is str:
        versions = [versions]
    require(type(versions) is list and 0 < len(versions) <= MAX_VERSIONS
            and all(valid_version(v) for v in versions)
            and len(set(versions)) == len(versions), "invalid_version_catalogue")
    require(type(tags) is dict and len(tags) <= MAX_VERSIONS
            and all(type(k) is str and TAG.fullmatch(k) and v in versions
                    for k, v in tags.items()), "invalid_dist_tags")
    fields = ["version", "dist.integrity", "dist.tarball", "dist.unpackedSize",
              "dist.fileCount", "deprecated"]
    # An all-prerelease package has no wildcard match at all.
    raw = (reader(["view", name + "@*", *fields], cwd)
           if any("-" not in v.split("+", 1)[0] for v in versions) else [])
    require(type(raw) in (dict, list), "invalid_version_metadata")
    rows = raw if type(raw) is list else [raw]
    indexed = {}
    for row in rows:
        require(type(row) is dict and row.get("version") in versions,
                "unexpected_version_metadata")
        require(row["version"] not in indexed, "duplicate_version_metadata")
        indexed[row["version"]] = row
    # npm's wildcard range excludes prereleases; explicitly retrieve every gap.
    for version in versions:
        if version not in indexed:
            row = reader(["view", name + "@" + version, *fields], cwd)
            require(type(row) is dict and row.get("version") == version,
                    "missing_version_metadata")
            indexed[version] = row
    result = {}
    for version in versions:
        row = indexed[version]
        registry_url(row.get("dist.tarball"))
        entry = {"integrity": sri(row.get("dist.integrity")),
                 "deprecated": bool(row.get("deprecated", False))}
        for source, target in (("dist.unpackedSize", "unpacked_bytes"),
                               ("dist.fileCount", "file_count")):
            value = row.get(source)
            require(value is None or (type(value) is int and value >= 0),
                    "invalid_size_metadata")
            entry[target] = value
        result[version] = entry
    return {"versions": result, "dist_tags": tags}


def verify_archive(reader, name, version, integrity, destination):
    raw = reader(["pack", name + "@" + version, "--pack-destination=" + str(destination)],
                 destination)
    require(type(raw) is list and len(raw) == 1 and type(raw[0]) is dict,
            "invalid_pack_result")
    row = raw[0]
    require(row.get("name") == name and row.get("version") == version,
            "archive_identity_mismatch")
    filename = row.get("filename")
    require(type(filename) is str and re.fullmatch(r"[A-Za-z0-9_.-]+\.tgz", filename),
            "invalid_archive_filename")
    path = destination / filename
    require(not path.is_symlink() and path.is_file()
            and path.resolve().parent == destination.resolve(), "invalid_archive_file")
    actual = path.stat().st_size
    require(0 < actual <= DISK_LIMIT and type(row.get("size")) is int
            and row["size"] == actual, "archive_size_mismatch")
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    measured = "sha512-" + base64.b64encode(digest.digest()).decode("ascii")
    require(sri(row.get("integrity")) == integrity == measured,
            "archive_integrity_mismatch")
    path.unlink()
    return {"compressed_bytes": actual, "integrity": measured}


def collect(scope, lock_path, *, env=None, reader_factory=NpmReader):
    env = os.environ if env is None else env
    require(scope in SCOPES, "unknown_scope")
    repository, allowed = SCOPES[scope]
    require(env.get("GITHUB_ACTIONS") == "true"
            and env.get("GITHUB_REPOSITORY") == repository, "untrusted_caller_context")
    lock, lock_sha = read_lock(lock_path)
    pins = locked_versions(lock, allowed)
    report = {"schema_version": 1, "repository": repository,
              "lockfile_sha256": lock_sha, "catalogue_complete": True,
              "locked_archives_complete": True, "all_catalogue_archive_bytes_complete": False,
              "effective_auth_identity_verified": False, "packages": {}}
    with tempfile.TemporaryDirectory(prefix="npm-inventory-") as directory:
        root = Path(directory)
        reader = reader_factory(root, project_context=Path(lock_path).resolve().parent)
        for name in allowed:
            item = catalogue(reader, name, root)
            item["locked_archives"] = {}
            for version, expected in pins[name].items():
                require(version in item["versions"], "locked_version_not_in_catalogue")
                require(item["versions"][version]["integrity"] == expected,
                        "lock_registry_integrity_mismatch")
                with tempfile.TemporaryDirectory(dir=root, prefix="pack-") as target:
                    item["locked_archives"][version] = verify_archive(
                        reader, name, version, expected, Path(target))
            # Repeat the catalogue after downloads: a concurrent publication/tag
            # update makes this capture incomplete rather than silently stale.
            require(catalogue(reader, name, root) == {
                k: v for k, v in item.items() if k != "locked_archives"},
                "catalogue_changed_during_capture")
            report["packages"][name] = item
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--lockfile", required=True)
    args = parser.parse_args(argv)
    try:
        report = collect(args.scope, args.lockfile)
    except (InventoryGap, OSError) as error:
        reason = str(error) if isinstance(error, InventoryGap) else "local_io_failed"
        print(json.dumps({"status": "DATA_GAP", "reason": reason,
                          "catalogue_complete": False, "locked_archives_complete": False}))
        return 1
    print(json.dumps({"status": "PASS", **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
