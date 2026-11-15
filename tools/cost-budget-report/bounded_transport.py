"""Bounded existing gh GET transport; no retry or credential acquisition."""
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
from time import monotonic

WRAPPER = '/Users/milanmerglevsky/.codex/bin/codex-guarded-command'
BUDGET_URL = re.compile(r'https://api\.github\.com/(?:enterprises|organizations)/[A-Za-z0-9_.-]+/settings/billing/budgets(?:/[A-Za-z0-9%_.~-]+|\?page=[0-9]+&per_page=100)?')
MAX_OUTPUT = 1024 * 1024


def read_json_command(argv, *, timeout, cwd):
    """Bound stdout and total elapsed time; silence potentially sensitive stderr.

    The outer invocation must still use capped-run for physical-memory and escaped
    process-tree containment. This function provides no substitute for that cap.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise ValueError('invalid transport timeout')
    deadline = monotonic() + timeout
    environment = {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/bin:/bin',
                   'LANG': 'en_US.UTF-8', 'GH_PROMPT_DISABLED': '1', 'GIT_TERMINAL_PROMPT': '0'}
    process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    data = bytearray()
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline-monotonic()
            if remaining <= 0:
                raise TimeoutError('GET transport deadline; no retry')
            events = selector.select(remaining)
            if not events:
                raise TimeoutError('GET transport deadline; no retry')
            block = os.read(process.stdout.fileno(), min(65536, MAX_OUTPUT+1-len(data)))
            if not block:
                break
            data.extend(block)
            if len(data) > MAX_OUTPUT:
                raise ValueError('GET response exceeds bound')
        remaining = deadline-monotonic()
        if remaining <= 0:
            raise TimeoutError('GET transport deadline; no retry')
        try:
            rc = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise TimeoutError('GET transport deadline; no retry') from None
        if rc:
            raise ValueError('GET transport failed; no retry')
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            raise ValueError('invalid GET JSON') from None
        if not isinstance(value, dict):
            raise ValueError('GET response object required')
        return value
    finally:
        selector.close()
        # Terminate the invocation's process group even if its leader already
        # exited, so children retaining a pipe cannot survive this transport.
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()
        process.stdout.close()


def gh_get(url, *, timeout):
    if url != 'https://api.github.com/rate_limit' and not (
            isinstance(url, str) and BUDGET_URL.fullmatch(url)):
        raise ValueError('unsupported GET endpoint')
    command = ['/bin/bash', WRAPPER, 'gh', 'api', '--hostname', 'github.com',
               '--method', 'GET', url.removeprefix('https://api.github.com/'),
               '-H', 'Accept: application/vnd.github+json',
               '-H', 'X-GitHub-Api-Version: 2026-03-10']
    return read_json_command(command, timeout=timeout, cwd=Path(__file__).resolve().parent)
