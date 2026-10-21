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


class UnparsableWorkflow(Exception):
    """The workflow does not parse; its running pins cannot be determined."""


def running_pins(text: str) -> set[str]:
    """SHAs that `jobs.<id>.uses` and `jobs.<id>.steps[].uses` actually execute.

    Read from the parsed workflow, so a `uses:` string inside a description or
    run block cannot pose as a running pin, and quoted values still count. A
    workflow that does not parse is an error, never a guess (fail closed).
    """
    import yaml  # PyYAML; the hub CI and pre-push hook already use it

    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise UnparsableWorkflow(str(exc).splitlines()[0]) from None
    pins: set[str] = set()
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    for job in (jobs or {}).values() if isinstance(jobs, dict) else []:
        if not isinstance(job, dict):
            continue
        values = [job.get("uses")] + [st.get("uses") for st in job.get("steps") or [] if isinstance(st, dict)]
        for value in values:
            if isinstance(value, str):
                m = re.match(r"\s*merglbot-core/github/[^@\s]+@([0-9a-f]{40})\s*$", value)
                if m:
                    pins.add(m.group(1))
    return pins


def stale_mirrors(text: str) -> list[tuple[int, str]]:
    running = running_pins(text)
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # A mirror can hide anywhere outside the uses: line itself: a comment, a
        # description block scalar, an echo in run: (review of #1043).
        pin = USES.match(line + "\n")
        if USES_LINE.match(line) and pin and pin.group(1) in running:
            # A running pin is the source of truth; only a trailing comment on it
            # can mirror (and drift from) another SHA. A `uses:` text that runs
            # nothing (e.g. inside a block scalar) is checked like any other line.
            line = line.split("#", 1)[1] if "#" in line else ""
        for sha in MIRROR.findall(line):
            if not any(r.startswith(sha) for r in running):
                out.append((lineno, sha))
    return out


def main(roots: list[str]) -> int:
    try:
        import yaml  # noqa: F401
    except ImportError:
        # Without the parser the running pins are unknown; never guess (fail closed).
        print("PyYAML is required: install it (python3 -m pip install pyyaml) and run again")
        return 2
    bad = 0
    for root in roots:
        wf = Path(root) / ".github" / "workflows"
        for path in sorted(list(wf.glob("*.yml")) + list(wf.glob("*.yaml"))):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "merglbot-core/github" not in text:
                continue
            try:
                stale = stale_mirrors(text)
            except UnparsableWorkflow as exc:
                print(f"{path}: workflow does not parse ({exc}); pin mirrors cannot be checked")
                bad += 1
                continue
            for lineno, sha in stale:
                print(f"{path}:{lineno}: comment names merglbot-core/github@{sha}, which no uses: line in this file runs")
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["."]))
