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
    node = next(node for node in functions if node.name == "post_billing")
    lines = source.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    artifact = ('    BILLING_DIR.mkdir(parents=True, exist_ok=True)\n'
                '    (BILLING_DIR / "acceptance.json").write_text(json.dumps({"usd": usage, "sku_minutes": skus}, indent=1))\n')
    accumulated_guard = ('    if not billing_numbers_finite(value for group in (usage, skus) for row in group.values() for value in row.values()):\n'
                         '        log("billing accumulation overflow; acceptance blocked")\n'
                         '        return False\n')
    row_guard = ('        if not billing_numbers_finite((before, after, before - after, total_before, total_after)):\n'
                 '            log("billing daily totals overflow; acceptance blocked")\n'
                 '            return False\n')
    derived_guard = ('    if not billing_numbers_finite((saved_usd, model, 0.8 * model)):\n'
                     '        log("billing derived amount unavailable; acceptance blocked")\n'
                     '        return False\n' + artifact)
    transformations = [('for item in data.get("usageItems", []):', replacement),
                     ('item.get("grossAmount") or 0', 'item["grossAmount"]'),
                     ('item.get("quantity") or 0', 'item["quantity"]'),
                     (artifact, accumulated_guard),
                     ('        rows.append(f"| {repo}', row_guard + '        rows.append(f"| {repo}'),
                     ('    verdict = "FAKT"', derived_guard + '    verdict = "FAKT"')]
    if any(node.name in {"billing_usage_items", "billing_numbers_finite"} for node in functions):
        # Every transformation must be complete inside the evaluator, including
        # all finite-value guards. A matching helper/loop alone is insufficient.
        if (source.count(helper.rstrip() + "\n") == 1
                and all(body.count(new) == 1 for _, new in transformations)
                and not any(old in body for old, _ in transformations[:3])):
            return source
        raise ValueError("incomplete billing numeric patch or helper drift")
    for old, new in transformations:
        if body.count(old) != 1:
            raise ValueError("billing evaluator source drift")
        body = body.replace(old, new)
    lines[node.lineno - 1:node.end_lineno] = [helper.rstrip() + "\n\n" + body]
    result = "".join(lines)
    compile(result, "autopilot.py", "exec")
    return result
