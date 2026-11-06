"""Proposed exact native-copy comparison; not an installation authorization.

The default remains strict. A separately reviewed owner plan may name only the
specific provenance attribute whose native copy changes were actually measured.
Callers must bind real consent, full snapshots, durable receipts and rollback;
this helper alone neither admits a host action nor makes recovery safe.
"""

from .transaction import Refused


PROVENANCE = "com.apple.provenance"
FIELDS = {"sha256", "uid", "gid", "mode", "flags", "xattrs"}


def admit_copy_snapshot(expected, observed, generated_attributes=()):
    """Return the full observed pin without dropping security metadata.

Only an explicitly named provenance VALUE may differ or be added by a native
copy. It cannot disappear. All other names, values and file fields must match.
Never use this comparison for a live source or post-replacement readback: those
must match their exact pinned snapshots, including the observed provenance hash.
"""
    if type(generated_attributes) is not tuple or generated_attributes not in (
        (), (PROVENANCE,),
    ):
        raise Refused("Unsupported native-copy metadata policy")
    if type(expected) is not dict or type(observed) is not dict:
        raise Refused("Invalid native-copy snapshot")
    if set(expected) != FIELDS or set(observed) != FIELDS:
        raise Refused("Incomplete native-copy snapshot")
    if any(expected[key] != observed[key] for key in FIELDS - {"xattrs"}):
        raise Refused("Native copy changed bytes or security metadata")
    before, after = expected["xattrs"], observed["xattrs"]
    if type(before) is not dict or type(after) is not dict:
        raise Refused("Invalid native-copy attribute snapshot")
    before_names, after_names = set(before), set(after)
    if generated_attributes:
        same_names = (before_names - {PROVENANCE} == after_names - {PROVENANCE}
                      and not (PROVENANCE in before_names and PROVENANCE not in after_names))
    else:
        same_names = before_names == after_names
    if not same_names:
        raise Refused("Native copy changed attribute names")
    if any(before[name] != after[name] for name in before
           if name not in generated_attributes):
        raise Refused("Native copy changed an unadmitted attribute")
    # Keep every actual attribute hash: subsequent readback remains exact.
    return dict(observed, xattrs=dict(after))
