"""Prepare and revalidate an offline installation packet; never install or run consumers.

The owner installation transport must consume this packet after its own admission.
A packet is not an authorization, a runtime receipt, or a transaction executor.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

HERE = Path(__file__).resolve().parent
HOME = Path.home()
TARGETS = {
    'digest': HOME / '.claude/merglbot-preauth/autonomy-digest.sh',
    'monthly': HOME / '.merglbot/gh-cost-wave3/evidence/financial-closed-month-20261102/render_report.py',
    'renderer': HOME / '.merglbot/tools/cost-budget-report/budget_report.py',
}
MAX_BYTES = 2_000_000


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def stable_stat(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def xattrs(path):
    if hasattr(os, 'listxattr'):
        return {name: digest(os.getxattr(path, name)) for name in sorted(os.listxattr(path))}
    # macOS Python builds may omit the xattr API. Use the native read-only tool.
    names = subprocess.check_output(['/usr/bin/xattr', str(path)], timeout=10).decode().splitlines()
    return {name: digest(bytes.fromhex(subprocess.check_output(
        ['/usr/bin/xattr', '-px', name, str(path)], timeout=10).decode())) for name in sorted(names)}


def snapshot(path, missing=False):
    """Reject symlink ancestors and nonregular or oversized files before reading."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute normalized path required')
    for ancestor in (*reversed(path.parents), path):
        if ancestor.is_symlink():
            raise ValueError('symlink path refused')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if missing:
            return {'exists': False}, None
        raise
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError('nonregular or oversized input')
        raw = os.read(fd, MAX_BYTES + 1)
        after = os.fstat(fd)
        if stable_stat(before) != stable_stat(after) or len(raw) != before.st_size or stable_stat(path.stat()) != stable_stat(after):
            raise ValueError('input changed during read')
        # Metadata is evidence, not a license to change it. The installer must
        # preserve it or refuse; this adapter does not restore or strip xattrs.
        attributes = xattrs(path)
        if stable_stat(path.stat()) != stable_stat(after) or xattrs(path) != attributes:
            raise ValueError('metadata changed during read')
        return {'exists': True, 'sha256': digest(raw), 'size': len(raw),
                'mode': stat.S_IMODE(after.st_mode), 'uid': after.st_uid,
                'gid': after.st_gid, 'mtime_ns': after.st_mtime_ns,
                'device': after.st_dev, 'inode': after.st_ino,
                'xattr_sha256': attributes}, raw
    finally:
        os.close(fd)


def patch_exact(raw, patch, contract):
    if digest(raw) != contract['baseline_sha256'] or digest(patch) != contract['patch_sha256']:
        raise ValueError('consumer or patch differs from reviewed contract')
    lines = raw.decode().splitlines(True)
    delta = patch.decode().splitlines(True)
    if len(delta) < 3 or not delta[0].startswith('--- ') or not delta[1].startswith('+++ '):
        raise ValueError('invalid patch header')
    output, cursor, i = [], 0, 2
    while i < len(delta):
        match = re.fullmatch(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@\n', delta[i])
        if not match:
            raise ValueError('invalid patch hunk')
        start, count, new_start, new_count = map(int, match.groups())
        offset = start - 1 if count else start
        if offset < cursor or offset > len(lines):
            raise ValueError('invalid hunk offset')
        output.extend(lines[cursor:offset]); cursor = offset
        if (new_start - 1 if new_count else new_start) != len(output):
            raise ValueError('invalid output offset')
        old_seen = new_seen = 0
        i += 1
        while i < len(delta) and not delta[i].startswith('@@ '):
            line = delta[i]; i += 1
            if not line or line[0] not in ' +-':
                raise ValueError('invalid patch line')
            if line[0] in ' -':
                if cursor >= len(lines) or lines[cursor] != line[1:]:
                    raise ValueError('patch context mismatch')
                cursor += 1; old_seen += 1
            if line[0] in ' +':
                output.append(line[1:]); new_seen += 1
        if (old_seen, new_seen) != (count, new_count):
            raise ValueError('hunk length mismatch')
    output.extend(lines[cursor:])
    result = ''.join(output).encode()
    if digest(result) != contract['candidate_sha256']:
        raise ValueError('candidate differs from reviewed contract')
    return result


def prepare(source=HERE, targets=None):
    targets = TARGETS if targets is None else targets
    if set(targets) != set(TARGETS):
        raise ValueError('exactly two consumers and renderer required')
    contract_raw = (source / 'integration/manifest.json').read_bytes()
    contract = json.loads(contract_raw)
    records, blobs = {}, {}
    for key in ('digest', 'monthly', 'renderer'):
        before, raw = snapshot(targets[key], missing=key == 'renderer')
        if key == 'renderer':
            candidate = (source / 'budget_report.py').read_bytes()
        else:
            candidate = patch_exact(raw, (source / f'integration/{key}.patch').read_bytes(),
                                    contract['sources'][key])
        records[key] = {'target': str(targets[key]), 'preimage': before,
                        'postimage_sha256': digest(candidate),
                        'candidate': f'{key}.candidate',
                        'rollback': f'{key}.backup' if raw is not None else None,
                        'rollback_action': 'restore_exact_backup' if raw is not None else 'remove_only_matching_postimage'}
        blobs[f'{key}.candidate'] = candidate
        if raw is not None:
            blobs[f'{key}.backup'] = raw
    manifest = {'schema_version': 1, 'scope': 'github#982', 'source_only': True,
                'installation_authorized': False, 'runtime_function_proven': False,
                'contract_sha256': digest(contract_raw), 'records': records,
                'requirements': ['owner admission and fresh preimages before apply',
                    'preserve metadata and xattrs', 'durable backup before first replacement',
                    'conditional rollback only for matching installed postimages',
                    'no consumer execution, scheduler, timer, API or settings mutation']}
    manifest['binding_sha256'] = digest(json.dumps(manifest, sort_keys=True).encode())
    return manifest, blobs


def verify_packet(directory, targets=None, source=HERE):
    targets = TARGETS if targets is None else targets
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    expected, _ = prepare(source, targets)
    if manifest != expected:
        raise ValueError('packet differs from current source or preimages')
    binding = manifest.pop('binding_sha256')
    if digest(json.dumps(manifest, sort_keys=True).encode()) != binding:
        raise ValueError('packet binding mismatch')
    if manifest['installation_authorized'] is not False or set(manifest['records']) != set(TARGETS):
        raise ValueError('packet scope mismatch')
    for key, record in manifest['records'].items():
        if record['target'] != str(targets[key]):
            raise ValueError('target mismatch')
        before, _ = snapshot(targets[key], missing=key == 'renderer')
        if before != record['preimage']:
            raise ValueError('preimage drift')
        if record['candidate'] != f'{key}.candidate' or record['rollback'] != (f'{key}.backup' if before['exists'] else None):
            raise ValueError('packet filename mismatch')
        if digest((directory / record['candidate']).read_bytes()) != record['postimage_sha256']:
            raise ValueError('candidate drift')
        if before['exists'] and digest((directory / record['rollback']).read_bytes()) != before['sha256']:
            raise ValueError('backup drift')
    return {'binding_sha256': binding, 'preimages_match': True, 'installation_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        result = verify_packet(args.packet)
    else:
        manifest, blobs = prepare()
        # Exclusive packet creation preserves all previous plans and backups.
        args.packet.mkdir(mode=0o700)
        for name, raw in blobs.items():
            with (args.packet / name).open('xb') as stream:
                os.chmod(stream.name, 0o600); stream.write(raw)
        with (args.packet / 'manifest.json').open('x') as stream:
            os.chmod(stream.name, 0o600); json.dump(manifest, stream, indent=2)
        result = verify_packet(args.packet)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
