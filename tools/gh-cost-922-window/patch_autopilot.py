"""Patch only the existing #922 billing handler and measured repository set."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent


def patch(source):
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    node = functions["billing_acceptance"]
    lines = source.splitlines(keepends=True)
    old = "".join(lines[node.lineno - 1:node.end_lineno])
    if 'if not billing.get("due_at"):' not in old or 'return post_billing(state, billing)' not in old:
        raise ValueError("billing handler drift or patch already installed")
    replacement = (HERE / "runtime.py").read_text()
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n"]
    updated = "".join(lines)
    original_repos = 'repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")}\n                   | set(billing.get("extra_repos", [])))'
    if updated.count(original_repos) != 1:
        raise ValueError("billing repository selector drift")
    updated = updated.replace(original_repos,
        'repos = sorted({key.rsplit("#", 1)[0] for _, key in billing_inputs(state)}\n'
        '                   | set(billing.get("extra_repos", [])))')
    compile(updated, "autopilot.py", "exec")
    return updated
