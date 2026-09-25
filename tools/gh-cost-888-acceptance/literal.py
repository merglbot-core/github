"""Exact natural-run evidence for #892; do not substitute PR Gate runs."""

from datetime import datetime


def terraform_paths_filtered(source):
    """Require direct PR trigger paths, not a comment or unrelated job text."""
    lines = source.splitlines()
    on = next((i for i, line in enumerate(lines) if line == "on:"), None)
    if on is None or sum(line == "on:" for line in lines) != 1:
        return False
    end = next((i for i in range(on + 1, len(lines))
                if lines[i] and not lines[i].startswith((" ", "#"))), len(lines))
    on_lines = lines[on + 1:end]
    pr = next((i for i, line in enumerate(on_lines) if line == "  pull_request:"), None)
    if pr is None or sum(line == "  pull_request:" for line in on_lines) != 1:
        return False
    pr_end = next((i for i in range(pr + 1, len(on_lines))
                   if on_lines[i].startswith("  ") and not on_lines[i].startswith("    ")
                   and not on_lines[i].lstrip().startswith("#")), len(on_lines))
    body = on_lines[pr + 1:pr_end]
    paths = next((i for i, line in enumerate(body) if line == "    paths:"), None)
    if paths is None or sum(line == "    paths:" for line in body) != 1:
        return False
    paths_end = next((i for i in range(paths + 1, len(body))
                      if body[i].strip() and not body[i].lstrip().startswith("#")
                      and len(body[i]) - len(body[i].lstrip(" ")) <= 4), len(body))
    path_rows = [line.strip()[2:].strip().strip("'\"") for line in body[paths + 1:paths_end]
                 if line.startswith("      - ")]
    return "terraform/**" in path_rows


def one_short_successful_job(payload, max_seconds):
    """Require complete direct Jobs API evidence, with one successful bounded job."""
    if not isinstance(payload, dict) or payload.get("total_count") != 1:
        return False
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or len(jobs) != 1:
        return False
    job = jobs[0]
    if job.get("conclusion") != "success":
        return False
    try:
        start = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
        seconds = (end - start).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    return 0 <= seconds <= max_seconds


def qualifying_terraform_runs(runs, merged_at):
    """Return a count only for a complete, unique workflow run census."""
    if not isinstance(runs, dict) or not isinstance(runs.get("total_count"), int):
        return None
    rows = runs.get("workflow_runs")
    if not isinstance(rows, list) or len(rows) != runs["total_count"]:
        return None
    ids = [row.get("id") for row in rows]
    if any(value is None for value in ids) or len(set(ids)) != len(ids):
        return None
    good = [row for row in rows if row.get("event") == "pull_request"
            and row.get("created_at", "") > merged_at
            and row.get("run_attempt") == 1
            and row.get("conclusion") == "success"
            and row.get("job_verified") is True]
    return len(good)


def qualifying_pr_count(pulls, merged_at):
    """The caller must provide all PR list pages through the merge timestamp."""
    if not isinstance(pulls, list):
        return None
    ids = [row.get("number") for row in pulls]
    if any(value is None for value in ids) or len(ids) != len(set(ids)):
        return None
    return sum(row.get("created_at", "") > merged_at for row in pulls)
