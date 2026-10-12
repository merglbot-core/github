"""Execute the reusable CodeQL workflow's config step offline (no GitHub, no CodeQL)."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-codeql-analysis.yml"


def run_step(paths_ignore):
    source = WORKFLOW.read_text()
    step = source.split("    - name: Build CodeQL config\n", 1)[1]
    script = textwrap.dedent(step.split("      run: |\n", 1)[1].split("\n\n    - name:", 1)[0])
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "output"
        env = {**os.environ, "GITHUB_OUTPUT": str(output)}
        if paths_ignore is None:
            env.pop("PATHS_IGNORE", None)
        else:
            env["PATHS_IGNORE"] = paths_ignore
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
        return result.returncode, output.read_text() if output.exists() else "", result.stderr


class PathsIgnoreConfigTests(unittest.TestCase):
    def test_default_and_unset_emit_an_empty_config(self):
        for value in ("[]", "", None, "  "):
            code, output, _ = run_step(value)
            self.assertEqual(code, 0, value)
            self.assertEqual(output, "config=\n", value)

    def test_globs_become_a_quoted_yaml_paths_ignore_list(self):
        code, output, _ = run_step('["**/*.test.cjs", "**/__tests__/**"]')
        self.assertEqual(code, 0)
        self.assertEqual(
            output,
            'config<<CODEQL_CONFIG_EOF\npaths-ignore:\n  - "**/*.test.cjs"\n  - "**/__tests__/**"\nCODEQL_CONFIG_EOF\n',
        )

    def test_a_hostile_entry_cannot_break_out_of_the_scalar_or_the_output_block(self):
        # A YAML-active string stays one JSON-quoted scalar; nothing becomes a new key.
        code, output, _ = run_step('["a\\": x\\nqueries: evil", "b # c"]')
        self.assertNotEqual(code, 0, "embedded newline must be rejected")
        self.assertEqual(output, "")
        code, output, _ = run_step('["x\\": y", "CODEQL_CONFIG_EOF"]')
        self.assertEqual(code, 0)
        lines = output.splitlines()
        self.assertEqual(lines[2], '  - "x\\": y"')
        self.assertEqual(lines[3], '  - "CODEQL_CONFIG_EOF"', "the delimiter only ever appears indented and quoted")
        self.assertEqual(lines.count("CODEQL_CONFIG_EOF"), 1, "exactly one bare closing delimiter")

    def test_invalid_shapes_fail_closed_without_writing_output(self):
        for value in ('"**/x"', '{"a": 1}', "[1]", '[""]', "not json", '["ok", null]'):
            code, output, stderr = run_step(value)
            self.assertNotEqual(code, 0, value)
            self.assertEqual(output, "", value)
            self.assertIn("paths-ignore must be", stderr, value)

    def test_the_init_step_consumes_the_generated_config(self):
        source = WORKFLOW.read_text()
        init = source.split("    - name: Initialize CodeQL\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("config: ${{ steps.codeql-config.outputs.config }}", init)
        self.assertIn("PATHS_IGNORE: ${{ inputs.paths-ignore }}", source)
        self.assertNotIn("${{ inputs.paths-ignore }}", source.split("      run: |\n", 1)[1].split("    - name: Initialize", 1)[0])


if __name__ == "__main__":
    unittest.main()
