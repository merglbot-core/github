"""Validate Actions compute usage before the existing gross billing evaluator."""


def billing_usage_items(data):
    import datetime
    import math
    import re
    if not isinstance(data, dict) or not isinstance(data.get("usageItems"), list):
        return None
    for item in data["usageItems"]:
        if not isinstance(item, dict) or not isinstance(item.get("product"), str) or not item["product"].strip():
            return None
        if item["product"].lower() != "actions":
            continue
        sku = item.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            return None
        if "storage" in sku.lower():
            continue
        for field in ("grossAmount", "quantity"):
            value = item.get(field)
            # bool is a subclass of int, but is not a billing measurement.
            if type(value) not in (int, float):
                return None
            try:
                if not math.isfinite(value):
                    return None
            except OverflowError:
                return None
        for field in ("organizationName", "repositoryName", "date"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                return None
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item["date"][:10]):
                return None
            datetime.date.fromisoformat(item["date"][:10])
        except ValueError:
            return None
    return data["usageItems"]
