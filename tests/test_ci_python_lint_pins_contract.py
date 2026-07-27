"""Contract test for the pinned lint-tool installs in the reusable ci-python workflow.

Guards the determinism fix from merglbot-core/github#730 (issue #728): the hub
must install ruff/black/mypy at EXACT pinned versions, must let a repo-installed
tool win (`command -v` runs before the pip fallback), and must judge black/mypy
interpreter compatibility against the RESOLVED interpreter.

The asserts are anchored to the lint-install `run` block (not the whole file),
so an unrelated occurrence elsewhere cannot satisfy them vacuously.
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


def lint_install_block() -> str:
    """The single run-block that installs lint tools, sliced out of the workflow."""
    text = CI_PYTHON.read_text(encoding="utf-8")
    start = text.index("Install linters based on inputs")
    # The block ends at the next step of the same job.
    next_step = re.search(r"\n      - name: ", text[start:])
    end = start + next_step.start() if next_step else len(text)
    return text[start:end]


class LintPinContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.block = lint_install_block()

    def test_exact_pins_present(self):
        for tool, version in PINS.items():
            with self.subTest(tool=tool):
                self.assertIn(
                    f'pip install "{tool}=={version}"',
                    self.block,
                    f"{tool} must be installed at the exact hub pin {version}; "
                    "an unpinned install reintroduces github#728",
                )

    def test_no_unpinned_installs_remain(self):
        for tool in PINS:
            with self.subTest(tool=tool):
                self.assertNotRegex(
                    self.block,
                    rf"pip install {tool}\s*$",
                    f"bare 'pip install {tool}' (run-time latest) must not exist",
                )

    def test_repo_installed_tool_wins(self):
        """`command -v <tool>` must gate (precede) the pinned pip fallback."""
        for tool, version in PINS.items():
            with self.subTest(tool=tool):
                guard = self.block.index(f"command -v {tool}")
                install = self.block.index(f'pip install "{tool}=={version}"')
                self.assertLess(
                    guard,
                    install,
                    f"{tool}: the command -v check must run before the hub pin "
                    "so a repo-installed tool always wins",
                )

    def test_interpreter_gate_uses_resolved_interpreter(self):
        """black/mypy compat is judged via sys.version_info, not selector parsing."""
        self.assertIn("hub_pin_needs_py310()", self.block)
        self.assertIn("sys.version_info >= (3, 10)", self.block)
        for tool in ("black", "mypy"):
            with self.subTest(tool=tool):
                gate = self.block.index("hub_pin_needs_py310", self.block.index(f"command -v {tool}"))
                install = self.block.index(f'pip install "{tool}=={PINS[tool]}"')
                self.assertLess(
                    gate,
                    install,
                    f"{tool}: the resolved-interpreter gate must run before the hub pin",
                )


if __name__ == "__main__":
    unittest.main()
