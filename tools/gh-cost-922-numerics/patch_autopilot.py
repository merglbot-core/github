"""Patch the billing reader without changing windows, state or DoD."""
import ast
from pathlib import Path

HERE = Path(__file__).parent


def patch(source):
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    helper = (HERE / "runtime.py").read_text()
    replacement = ('items = billing_usage_items(data)\n'
                   '            if items is None:\n'
                   '                log("billing numeric provenance unavailable; acceptance blocked")\n'
                   '                return False\n'
                   '            for item in items:')
    if helper.rstrip() + "\n" in source and source.count(replacement) == 1:
        return source
    if any(node.name == "billing_usage_items" for node in functions):
        raise ValueError("billing numeric helper drift")
    node = next(node for node in functions if node.name == "post_billing")
    lines = source.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    for old, new in [('for item in data.get("usageItems", []):', replacement),
                     ('item.get("grossAmount") or 0', 'item["grossAmount"]'),
                     ('item.get("quantity") or 0', 'item["quantity"]')]:
        if body.count(old) != 1:
            raise ValueError("billing evaluator source drift")
        body = body.replace(old, new)
    lines[node.lineno - 1:node.end_lineno] = [helper.rstrip() + "\n\n" + body]
    result = "".join(lines)
    compile(result, "autopilot.py", "exec")
    return result
