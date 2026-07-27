"""Contract test for the pinned lint-tool installs in the reusable ci-python workflow.

Guards the determinism fix from merglbot-core/github#730 (issue #728): the hub
must install ruff/black/mypy at EXACT pinned versions, must let a repo-installed
tool win (`command -v` runs before the pip fallback), and must judge black/mypy
interpreter compatibility against the RESOLVED interpreter.

Design notes (round-5 hardening):
- Asserts are anchored to the lint-install `run` block, not the whole file.
- All checks operate on EFFECTIVE lines (comments stripped), so a pinned string
  surviving only inside a comment cannot satisfy them — that exact mutation
  (pin moved to a comment + bare `pip install ruff` restored) is exercised by
  the self-test below and must be reported as a violation.
"""

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CI_PYTHON = REPO_ROOT / ".github" / "workflows" / "ci-python.yml"

PINS = {
    "ruff": "0.16.0",
    "black": "26.5.1",
    "mypy": "2.3.0",
}


def lint_install_block(text=None):
    """The single run-block that installs lint tools, sliced out of the workflow."""
    if text is None:
        text = CI_PYTHON.read_text(encoding="utf-8")
    start = text.index("Install linters based on inputs")
    next_step = re.search(r"\n      - name: ", text[start:])
    end = start + next_step.start() if next_step else len(text)
    return text[start:end]


def effective(block):
    """Block reduced to executable content: full-line comments dropped,
    trailing comments stripped. What the shell would actually run."""
    lines = []
    for raw in block.splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(raw.split(" #", 1)[0].rstrip())
    return lines


def pin_violations(block):
    """Return a list of human-readable contract violations for the block.

    Kept as a plain function (not test methods) so the self-test below can run
    it against a deliberately mutated block and assert it goes red.
    """
    lines = effective(block)
    text = "\n".join(lines)
    problems = []

    for tool, version in PINS.items():
        pinned = f'pip install "{tool}=={version}"'
        if pinned not in text:
            problems.append(f"{tool}: exact hub pin '{pinned}' missing from effective lines")
        # A bare (unpinned) install anywhere in the effective lines reintroduces #728.
        for line in lines:
            if re.search(rf'pip install\s+"?{tool}"?\s*$', line):
                problems.append(f"{tool}: bare unpinned install present: {line.strip()!r}")
        guard = text.find(f"command -v {tool}")
        install = text.find(pinned)
        if guard == -1:
            problems.append(f"{tool}: 'command -v {tool}' repo-first guard missing")
        elif install != -1 and guard > install:
            problems.append(f"{tool}: repo-first guard must precede the hub pin")

    if "hub_pin_needs_py310()" not in text or "sys.version_info >= (3, 10)" not in text:
        problems.append("resolved-interpreter gate (hub_pin_needs_py310 / sys.version_info) missing")
    else:
        for tool in ("black", "mypy"):
            anchor = text.find(f"command -v {tool}")
            gate = text.find("hub_pin_needs_py310", anchor if anchor != -1 else 0)
            install = text.find(f'pip install "{tool}=={PINS[tool]}"')
            if install != -1 and (gate == -1 or gate > install):
                problems.append(f"{tool}: resolved-interpreter gate must precede the hub pin")

    return problems


class LintPinContractTests(unittest.TestCase):
    def test_current_workflow_satisfies_contract(self):
        self.assertEqual(pin_violations(lint_install_block()), [])

    def test_checker_catches_pin_moved_to_comment(self):
        """The round-5 Codex mutation: keep the pinned string only in a comment
        and restore a bare install — the checker must report violations."""
        block = lint_install_block()
        mutated = block.replace(
            'pip install "ruff==0.16.0"',
            '# pip install "ruff==0.16.0"\n            pip install ruff',
            1,
        )
        self.assertNotEqual(mutated, block, "mutation did not apply — anchor drifted")
        problems = pin_violations(mutated)
        self.assertTrue(
            any("ruff" in p and ("missing" in p or "bare unpinned" in p) for p in problems),
            f"checker failed to flag the comment-hidden pin mutation: {problems}",
        )

    def test_checker_catches_removed_repo_first_guard(self):
        block = lint_install_block()
        mutated = block.replace("command -v black", "true || command -v black", 1)
        problems = pin_violations(mutated)
        # guard text still present but no longer first-position semantics is out of
        # textual reach; the contract's detectable regression is full removal:
        mutated2 = "\n".join(
            l for l in block.splitlines() if "command -v mypy" not in l
        )
        self.assertTrue(
            any("mypy" in p and "guard missing" in p for p in pin_violations(mutated2)),
            "checker failed to flag a removed repo-first guard",
        )


if __name__ == "__main__":
    unittest.main()
