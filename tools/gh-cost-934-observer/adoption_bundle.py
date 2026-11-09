"""Load only reviewed native dependency bytes, without a mutable import path.

The byte pins prove source identity, not installation authority. The separately
reviewed owner entrypoint must still verify its own admission and owner manifest.
"""
import hashlib
from pathlib import Path
import sys
import types

SOURCE_COMMIT = '9ac0ba9ca03bfbd87103e22b619b8f5fd2004b6d'
PINS = {
    'transaction': 'ce32e07dd82a33600812c95449c25e2c239e932c9ebd6a6af9bfbb76dd692d12',
    'copy_admission': 'b90cff66d33ab99a1593f4ff07dc2bfb72036a30337ebee576a9eb0e98e6d750',
}
NAMESPACE = '_cost934_native_9ac0ba9c'
MAX_BYTES = 1024 * 1024


def verified_sources(root):
    """Verify the entire dependency set before executing any of it."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('absolute_bundle_required')
    result = {}
    for name, expected in PINS.items():
        path = root / 'tools/session_context_install' / (name + '.py')
        with path.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('native_dependency_pin_mismatch:' + name)
        result[name] = data
    return result


def load(root):
    if not sys.flags.isolated:
        raise ValueError('isolated_python_required')
    sources = verified_sources(root)
    names = [NAMESPACE, *(NAMESPACE + '.' + key for key in PINS)]
    if any(name in sys.modules for name in names):
        raise ValueError('native_namespace_already_loaded')
    package = types.ModuleType(NAMESPACE)
    package.__path__ = []  # Never search the bundle for unpinned dependencies.
    sys.modules[NAMESPACE] = package
    try:
        for name in PINS:
            module = types.ModuleType(NAMESPACE + '.' + name)
            module.__package__ = NAMESPACE
            module.__file__ = str(Path(root) / 'tools/session_context_install' / (name + '.py'))
            sys.modules[module.__name__] = module
            setattr(package, name, module)
            # Execute the exact verified buffer, never reopen the mutable file.
            exec(compile(sources[name], module.__file__, 'exec'), module.__dict__)
        return package.transaction
    except BaseException:
        for name in names:
            sys.modules.pop(name, None)
        raise
