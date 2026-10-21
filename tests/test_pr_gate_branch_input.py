"""Keep the reusable gate's free-form branch prefixes outside generated code."""

import json
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def gate_source():
    # Extract this fixed step without adding a dependency before CI's pip install.
    # Changed or ambiguous formatting fails closed instead of skipping the check.
    text = (ROOT / ".github/workflows/pr-gate.yml").read_text()
    steps = re.findall(
        r"^      - name: Check branch naming\n(.*?)(?=^      - name:|\Z)",
        text, re.M | re.S,
    )
    if len(steps) != 1:
        raise AssertionError("Expected exactly one branch naming step")
    step = steps[0]
    scripts = re.findall(
        r"^          script: \|\n((?:^            .*\n|^\s*\n)+)", step, re.M,
    )
    if len(scripts) != 1:
        raise AssertionError("Expected exactly one literal branch-check script")
    script = "\n".join(line[12:] for line in scripts[0].splitlines())
    inputs = re.findall(
        r"^      allowed-branch-prefixes:\n(.*?)(?=^      \S|\Z)",
        text, re.M | re.S,
    )
    if len(inputs) != 1:
        raise AssertionError("Expected exactly one branch-prefix input")
    defaults = re.findall(r"^        default: '([^']*)'$", inputs[0], re.M)
    if len(defaults) != 1:
        raise AssertionError("Expected one literal branch-prefix default")
    return step, script, defaults[0]


class BranchPrefixDataContract(unittest.TestCase):
    def test_free_string_input_is_data_not_script_source(self):
        step, script, _ = gate_source()
        self.assertRegex(
            step,
            r"(?m)^        env:\n(?:          .+\n)*"
            r"          ALLOWED_BRANCH_PREFIXES: \$\{\{ inputs.allowed-branch-prefixes \}\}$",
        )
        self.assertNotIn("${{", script)

    def test_actual_script_preserves_prefix_behaviour_for_literal_values(self):
        _, script, default = gate_source()
        cases = [
            (default, "feat/example", 0),
            (" fix/, docs/ ", "docs/example", 0),
            ("feat/,fix/", "unrelated/example", 1),
            ("", "same-empty-prefix-behaviour", 0),
            ("feat'/", "feat'/example", 0),
            ("literal`$()/", "literal`$()/example", 0),
            ("line\nbreak/", "line\nbreak/example", 0),
        ]
        driver = """
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const warnings = [];
const logs = [];
vm.runInNewContext(input.script, {
  process: { env: input.env },
  core: { warning: value => warnings.push(value) },
  console: { log: value => logs.push(value) },
}, { timeout: 1000 });
process.stdout.write(JSON.stringify({ warnings: warnings.length, logs: logs.length }));
"""
        for prefixes, branch, warnings in cases:
            with self.subTest(prefixes=prefixes):
                result = subprocess.run(
                    ["node", "-e", driver],
                    input=json.dumps({
                        "script": script,
                        "env": {"PR_BRANCH": branch, "ALLOWED_BRANCH_PREFIXES": prefixes},
                    }),
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                self.assertEqual(
                    json.loads(result.stdout), {"warnings": warnings, "logs": 1 - warnings}
                )


if __name__ == "__main__":
    unittest.main()
