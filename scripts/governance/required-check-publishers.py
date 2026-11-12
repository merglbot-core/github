"""Build and verify explicit required-check publisher bindings offline."""
import json
import sys

V6 = "Merglbot PR Assistant v6"
V6_APP = 3518182


def names(required):
    contexts = required.get("contexts", [])
    checks = required.get("checks", [])
    if not isinstance(contexts, list) or not isinstance(checks, list):
        raise TypeError("required checks must be arrays")
    values = contexts + [check["context"] for check in checks]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("invalid check context")
    return list(dict.fromkeys(values))


def explicit_bindings(required, contexts):
    bindings = []
    for context in contexts:
        matches = [check for check in required.get("checks", [])
                   if check["context"] == context]
        if not matches:
            raise ValueError("publisher unavailable; refusing automatic App selection")
        for check in matches:
            app = check.get("app_id")
            # -1 is GitHub's explicit any-publisher binding, preserved only
            # when it already exists. Null/omitted App IDs are not evidence.
            if type(app) is not int or (app != -1 and app <= 0):
                raise ValueError("publisher unavailable; refusing automatic App selection")
            binding = {"context": context, "app_id": app}
            if binding not in bindings:
                bindings.append(binding)
    return bindings


def normalize(protection, desired):
    required = protection.get("required_status_checks")
    if required is not None and not isinstance(required, dict):
        raise TypeError("invalid required status checks object")
    if required is None and not desired:
        raise ValueError("required status checks absent")
    required = required or {}
    if type(required.get("strict", True)) is not bool:
        raise TypeError("invalid strict flag")
    existing = names(required)
    if not isinstance(desired, list) or any(
            not isinstance(context, str) or not context.strip() for context in desired):
        raise ValueError("invalid desired contexts")
    contexts = list(dict.fromkeys(desired or existing))
    if not contexts:
        raise ValueError("refusing empty required status checks")
    checks = []
    for context in contexts:
        if context == V6:
            checks.append({"context": V6, "app_id": V6_APP})
        else:
            checks.extend(explicit_bindings(required, [context]))
    # Retain the setter's existing strict=true baseline behavior.
    return {"strict": required.get("strict") or True, "checks": checks}


def verify(expected, protection):
    observed = protection.get("required_status_checks")
    if not isinstance(observed, dict):
        raise TypeError("readback required checks absent")
    # Do not normalize V6 here: a wrong publisher in the response must fail.
    bindings = explicit_bindings(observed, names(observed))
    identity = lambda checks: {(c["context"], c["app_id"]) for c in checks}
    if (identity(expected["checks"]) != identity(bindings)
            or observed.get("strict") is not expected["strict"]):
        raise ValueError("required-check publisher/strict readback mismatch")


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in ("normalize", "verify"):
        raise ValueError("usage: required-check-publishers.py normalize BEFORE CONTEXTS_JSON | verify PAYLOAD AFTER")
    with open(sys.argv[2], encoding="utf-8") as source:
        value = json.load(source)
    if sys.argv[1] == "normalize":
        print(json.dumps(normalize(value, json.loads(sys.argv[3]))))
    else:
        with open(sys.argv[3], encoding="utf-8") as source:
            verify(value["required_status_checks"], json.load(source))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, KeyError, OSError) as error:
        # Fixed reasons only; never dump branch-protection response bodies.
        print(f"required-check-publishers: {error}", file=sys.stderr)
        sys.exit(1)
