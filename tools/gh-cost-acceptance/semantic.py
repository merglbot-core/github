"""Fail-closed acceptance predicates for the local GitHub cost autopilot.

These predicates do not contact GitHub or mutate the autopilot state. The caller
must provide complete, current, SHA-bound GitHub responses.
"""

import re


SLIM_RUNNER = "${{ inputs.runs-on == 'ubuntu-slim' && 'ubuntu-slim' || 'ubuntu-24.04' }}"
SLIM_TIMEOUT = "${{ inputs.runs-on == 'ubuntu-slim' && 15 || 30 }}"


def _mapping_block(lines, key, indent):
    """Return the body of one unambiguous literal YAML mapping key."""
    marker = " " * indent + key + ":"
    positions = [i for i, line in enumerate(lines)
                 if line.rstrip() == marker]
    if len(positions) != 1:
        return None
    start = positions[0] + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            depth = len(line) - len(line.lstrip(" "))
            if depth <= indent:
                break
        end += 1
    return lines[start:end]


def hub_slim_configured(source):
    """Recognize the reviewed pr-gate.yml shape; ambiguity is a non-pass.

    This intentionally accepts only the deployed scalar expression. If the hub
    changes shape, extend this predicate with a fixture from that exact revision.
    """
    lines = source.splitlines()
    # Never accept a match that appears only in a comment or a document string.
    if any("\t" in line[:len(line) - len(line.lstrip())] for line in lines):
        return False
    on = _mapping_block(lines, "on", 0)
    jobs = _mapping_block(lines, "jobs", 0)
    if on is None or jobs is None:
        return False
    call = _mapping_block(on, "workflow_call", 2)
    job = _mapping_block(jobs, "pr-gate", 2)
    if call is None or job is None:
        return False
    inputs = _mapping_block(call, "inputs", 4)
    if inputs is None:
        return False
    runner = _mapping_block(inputs, "runs-on", 6)
    if runner is None:
        return False
    defaults = [line.strip() for line in runner if line.startswith("        default:")]
    defaults = [value for value in defaults
                if re.fullmatch(r"default:\s*['\"]?ubuntu-slim['\"]?\s*", value)]
    if len(defaults) != 1:
        return False
    declarations = [line.strip() for line in job
                    if re.match(r"^    (runs-on|timeout-minutes):", line)]
    return (declarations.count("runs-on: " + SLIM_RUNNER) == 1
            and declarations.count("timeout-minutes: " + SLIM_TIMEOUT) == 1
            and len(declarations) == 2)


def push_run_counts(runs, since, known_premerge_shas):
    """Count genuine post-merge main push runs; retain unknown SHAs as blockers.

    The caller proves every excluded SHA is a parent/ancestor of the merged
    configuration. A delayed run on such a SHA does not execute the new YAML.
    """
    if not isinstance(runs, dict) or not isinstance(runs.get("workflow_runs"), list):
        return None
    rows = runs["workflow_runs"]
    if runs.get("total_count") is None or runs["total_count"] != len(rows):
        return None
    ids = [row.get("id") for row in rows]
    if any(value is None for value in ids) or len(ids) != len(set(ids)):
        return None
    if any(not isinstance(row.get("created_at"), str) or not row["created_at"]
           or not isinstance(row.get("event"), str) or not row["event"] for row in rows):
        return None
    after = [run for run in rows if run.get("created_at", "") > since]
    pushes = [run for run in after if run.get("event") == "push"]
    real = [run for run in pushes if run.get("head_sha") not in known_premerge_shas]
    weekly = [run for run in after if run.get("event") == "schedule"]
    return {"post_merge_pushes": len(real),
            "excluded_premerge_run_ids": [run.get("id") for run in pushes
                                          if run.get("head_sha") in known_premerge_shas],
            "schedule_successes": sum(run.get("conclusion") == "success" for run in weekly)}


def main_trigger_contract(source, require_schedule=False):
    """Recognize a literal main workflow with no push trigger."""
    lines = source.splitlines()
    on = _mapping_block(lines, "on", 0)
    if on is None:
        return False
    declarations = [line for line in on
                    if line.startswith("  ") and not line.startswith("    ")
                    and line.strip() and not line.lstrip().startswith("#")]
    if any(not re.fullmatch(r"  [A-Za-z_]+:\s*(?:#.*)?", line) for line in declarations):
        return False
    trigger_keys = [line.strip().split(":", 1)[0] for line in declarations]
    if len(trigger_keys) != len(set(trigger_keys)):
        return False
    return (("pull_request" in trigger_keys or (require_schedule and "schedule" in trigger_keys))
            and "push" not in trigger_keys
            and (not require_schedule or "schedule" in trigger_keys))
