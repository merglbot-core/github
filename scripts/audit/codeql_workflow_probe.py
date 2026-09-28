"""Project only executable workflow structure from YAML; never run the YAML.

PyYAML BaseLoader keeps GitHub's `on` key as a string (SafeLoader's YAML 1.1
booleans do not). Errors expose no workflow body or credential-bearing input.
"""

import json
import sys

import yaml


def probe(body):
    docs = list(yaml.load_all(body, Loader=yaml.BaseLoader))
    if len(docs) != 1 or not isinstance(docs[0], dict):
        raise ValueError("invalid_workflow")
    workflow = docs[0]
    on = workflow.get("on")
    if isinstance(on, str):
        triggers = [on]
    elif isinstance(on, list):
        triggers = on
    elif isinstance(on, dict):
        triggers = list(on)
    else:
        raise ValueError("invalid_workflow")
    if not triggers or any(not isinstance(x, str) or not x for x in triggers):
        raise ValueError("invalid_workflow")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("invalid_workflow")
    step_uses = []
    job_uses = []
    for job in jobs.values():
        if not isinstance(job, dict):
            raise ValueError("invalid_workflow")
        if "uses" in job:
            if not isinstance(job["uses"], str):
                raise ValueError("invalid_workflow")
            job_uses.append(job["uses"])
        steps = job.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("invalid_workflow")
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("invalid_workflow")
            if "uses" in step:
                if not isinstance(step["uses"], str):
                    raise ValueError("invalid_workflow")
                step_uses.append(step["uses"])
    return {"triggers": triggers, "step_uses": step_uses, "job_uses": job_uses}


if __name__ == "__main__":
    try:
        body = sys.stdin.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("workflow_too_large")
        print(json.dumps(probe(body)))
    except Exception:
        print('{"error":"workflow_unparseable"}')
        sys.exit(1)
