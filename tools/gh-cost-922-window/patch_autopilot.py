"""Patch only the existing #922 billing handler and measured repository set."""
import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent


def patch(source):
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    replacement = (HERE / "runtime.py").read_text()
    new_repos = ('repos = sorted({key.rsplit("#", 1)[0] for _, key in billing_inputs(state)}\n'
                 '                   | set(billing.get("extra_repos", [])))')
    # Future follow-ups can be registered with the exact same reviewed patch.
    # Accept only byte-identical installed code, never a partial/stale patch.
    if replacement.rstrip() + "\n" in source and source.count(new_repos) == 1:
        return source
    if any(name in functions for name in ("billing_inputs", "billing_window")):
        raise ValueError("installed billing helper drift")
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == "BILLING_REQUIRED_FOLLOWUPS" for target in node.targets):
            raise ValueError("billing constant collision")
    node = functions["billing_acceptance"]
    lines = source.splitlines(keepends=True)
    old = "".join(lines[node.lineno - 1:node.end_lineno])
    if 'if not billing.get("due_at"):' not in old or 'return post_billing(state, billing)' not in old:
        raise ValueError("billing handler drift or patch already installed")
    lines[node.lineno - 1:node.end_lineno] = [replacement.rstrip() + "\n"]
    updated = "".join(lines)
    original_repos = 'repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")}\n                   | set(billing.get("extra_repos", [])))'
    if updated.count(original_repos) != 1:
        raise ValueError("billing repository selector drift")
    updated = updated.replace(original_repos, new_repos)
    compile(updated, "autopilot.py", "exec")
    return updated
