"""Preserve #888's minute model; reject missing and nonfinite measurements."""
import ast
from pathlib import Path

HERE = Path(__file__).parent


def patch(source):
    tree = ast.parse(source)
    helper = (HERE.parent / "gh-cost-922-numerics/runtime.py").read_text()
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "post_billing")
    lines = source.splitlines(keepends=True)
    body = "".join(lines[node.lineno - 1:node.end_lineno])
    loop = ('items = billing_usage_items(data)\n'
            '            if items is None:\n'
            '                log("billing measurement provenance unavailable; acceptance blocked")\n'
            '                return False\n'
            '            for item in items:')
    month = ('                if day[:7] != f"{year:04d}-{month:02d}":\n'
             '                    log("billing response month mismatch; acceptance blocked")\n'
             '                    return False\n')
    artifact = ('    BILLING_DIR.mkdir(parents=True, exist_ok=True)\n'
                '    (BILLING_DIR / "acceptance.json").write_text(json.dumps(usage, indent=1))\n')
    aggregate = ('    if not billing_numbers_finite(value for row in usage.values() for value in row.values()):\n'
                 '        log("billing accumulation overflow; acceptance blocked")\n'
                 '        return False\n')
    daily = ('        if not billing_numbers_finite((before, after, before - after, total_before, total_after)):\n'
             '            log("billing daily totals overflow; acceptance blocked")\n'
             '            return False\n')
    derived = ('    if not billing_numbers_finite((saved_usd, model, 0.8 * model)):\n'
               '        log("billing derived amount unavailable; acceptance blocked")\n'
               '        return False\n' + artifact)
    steps = [('for item in data.get("usageItems", []):', loop),
             ('item.get("quantity") or 0', 'item["quantity"]'),
             ('"linux" not in (item.get("sku") or "").lower()',
              '("linux" not in (item.get("sku") or "").lower() or "storage" in item["sku"].lower())'),
             ('                usage.setdefault(repo,', month + '                usage.setdefault(repo,'),
             (artifact, aggregate),
             ('        rows.append(f"| {repo}', daily + '        rows.append(f"| {repo}'),
             ('    verdict = "FAKT"', derived + '    verdict = "FAKT"')]
    if any(isinstance(n, ast.FunctionDef) and n.name in {"billing_usage_items", "billing_numbers_finite"}
           for n in tree.body):
        if (source.count(helper.rstrip() + "\n") == 1 and all(body.count(new) == 1 for _, new in steps)
                and not any(old in body for old, _ in steps[:2])):
            return source
        raise ValueError("incomplete #888 numeric patch or helper drift")
    for old, new in steps:
        if body.count(old) != 1:
            raise ValueError("#888 billing source drift")
        body = body.replace(old, new)
    lines[node.lineno - 1:node.end_lineno] = [helper.rstrip() + "\n\n" + body]
    result = "".join(lines)
    compile(result, "autopilot.py", "exec")
    return result
