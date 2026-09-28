"""Validate workflow-run identity before an observed cost denominator is counted."""


def snapshot_identity_error(snapshot: object, expected_repo: str) -> str | None:
    """Return a DATA_GAP category, without deduplicating or changing the input.

    A workflow rerun retains its run ID. Counting two attempts as two distinct
    created workflow runs would make cost per successful run artificially low.
    Reject inconsistent evidence rather than choosing which attempt to keep.
    """
    if not isinstance(snapshot, dict):
        return "RUN_RECORDS_INVALID"
    if snapshot.get("repo") != expected_repo:
        return "RUN_REPOSITORY_IDENTITY_MISMATCH"
    runs = snapshot.get("runs")
    if not isinstance(runs, list):
        return "RUN_RECORDS_INVALID"
    run_ids = [run.get("id") if isinstance(run, dict) else None for run in runs]
    if any(isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0
           for run_id in run_ids):
        return "RUN_ID_INVALID"
    if len(run_ids) != len(set(run_ids)):
        return "RUN_ID_DUPLICATE"
    return None
