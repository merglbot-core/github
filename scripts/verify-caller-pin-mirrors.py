#!/usr/bin/env python3
"""A caller workflow may name only the merglbot-core/github SHA it actually runs (github#773).

Dependabot bumps the `uses:` line of a thin caller, but not the comment that mirrors the
same pin (`# ... pinned to merglbot-core/github@<sha>`). The file then documents a pin it
no longer executes, and a V6 review flags it on the next bump. This check reads every
`.github/workflows/*.y*ml` under the given roots and reports each file whose comments
name a merglbot-core/github SHA that none of its `uses:` lines run.

Usage: verify-caller-pin-mirrors.py <repo-root> [<repo-root> ...]
Exit 0 when consistent, 1 with one line per stale mirror. Comments that quote history
on purpose should drop the SHA and point at the `uses:` line instead (#773, remedy 2).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

USES = re.compile(r"^\s*-?\s*uses:\s*merglbot-core/github/[^@\s]+@([0-9a-f]{40})\b", re.M)
MIRROR = re.compile(r"merglbot-core/github(?:/[^@\s]*)?@([0-9a-f]{7,40})\b")
USES_LINE = re.compile(r"^\s*-?\s*uses:\s")


def stale_mirrors(text: str) -> list[tuple[int, str]]:
    running = set(USES.findall(text))
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # A mirror can hide anywhere outside the uses: line itself: a comment, a
        # description block scalar, an echo in run: (review of #1043).
        if USES_LINE.match(line):
            # The pin itself is the source of truth; only a trailing comment on it
            # can mirror (and drift from) another SHA.
            line = line.split("#", 1)[1] if "#" in line else ""
        for sha in MIRROR.findall(line):
            if not any(r.startswith(sha) for r in running):
                out.append((lineno, sha))
    return out


def main(roots: list[str]) -> int:
    bad = 0
    for root in roots:
        wf = Path(root) / ".github" / "workflows"
        for path in sorted(list(wf.glob("*.yml")) + list(wf.glob("*.yaml"))):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "merglbot-core/github" not in text:
                continue
            for lineno, sha in stale_mirrors(text):
                print(f"{path}:{lineno}: comment names merglbot-core/github@{sha}, which no uses: line in this file runs")
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
