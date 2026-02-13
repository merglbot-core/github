#!/usr/bin/env python3
"""
Forecast D-1 readiness guardrail.

Purpose:
- Verify that D-1 actuals are present in target final tables (inventory-driven).
- Designed to run from GitHub Actions with WIF/OIDC and Cloud SDK (bq) available.

Business policy (Europe/Prague):
- 08:00 + 10:00: required scope = country=cz; non-CZ is informational only.
- 15:00: required scope = all.

DST-safe scheduling:
- Workflow schedules both CET/CEST cron equivalents.
- Script uses GITHUB_EVENT_SCHEDULE + local UTC offset to NOOP the "wrong" cron,
  ensuring only one Slack notification per slot/day.

Never logs secret values.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


EPS = 1e-9

REQUIRED_COLUMNS = ("date", "sessions", "revenue_db", "transactions_db")
OPTIONAL_COLUMN_COST = "cost"


@dataclass(frozen=True)
class PipelineSpec:
    project_id: str
    tenant: str
    country: str
    bq_table_13: str


@dataclass(frozen=True)
class ResultRow:
    project_id: str
    tenant: str
    country: str
    slot: str
    required_policy: str
    patch_date_local: str
    is_required: str
    table_fq: str
    status: str
    reason: str
    row_count: int
    sessions_sum: float
    revenue_db_sum: float
    transactions_db_sum: float
    actuals_sum: float
    cost_sum: float
    cost_present: str
    error_snippet: str


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def _run_with_retries(
    args: list[str],
    *,
    max_attempts: int = 5,
    initial_sleep_s: float = 2.0,
    check: bool = True,
    retry_stderr_substrings: tuple[str, ...] = (
        "ServerNotFoundError('Unable to find the server at bigquery.googleapis.com')",
        "ServerNotFoundError(\"Unable to find the server at bigquery.googleapis.com\")",
        "Could not connect with BigQuery server",
        "Retrying request, attempt",
    ),
) -> subprocess.CompletedProcess[str]:
    sleep_s = initial_sleep_s
    last: Optional[subprocess.CompletedProcess[str]] = None
    for attempt in range(1, max_attempts + 1):
        cp = _run(args, check=False)
        last = cp
        if cp.returncode == 0:
            return cp
        if any(s in (cp.stderr or "") for s in retry_stderr_substrings) and attempt < max_attempts:
            time.sleep(sleep_s)
            sleep_s = min(30.0, sleep_s * 2.0)
            continue
        if check:
            raise subprocess.CalledProcessError(cp.returncode, args, output=cp.stdout, stderr=cp.stderr)
        return cp
    if last is None:  # pragma: no cover
        raise RuntimeError("Unexpected retry loop state")
    return last


def _ensure_cmd_available(cmd: str) -> None:
    cp = _run(["/bin/bash", "-lc", f"command -v {cmd} >/dev/null 2>&1 && echo OK || echo NO"], check=True)
    if cp.stdout.strip() != "OK":
        raise RuntimeError(f"Missing required command: {cmd}")


def _parse_date_local(value: str, tz: ZoneInfo) -> date:
    value = (value or "").strip()
    if not value:
        return datetime.now(tz=tz).date()
    return date.fromisoformat(value)


def _split_table_fq(table_fq: str) -> tuple[str, str, str]:
    parts = (table_fq or "").split(".", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid table FQ name: {table_fq!r} (expected project.dataset.table)")
    return parts[0], parts[1], parts[2]


def _classify_bq_error(stderr: str) -> str:
    s = (stderr or "").lower()
    if "not found" in s:
        return "not_found"
    if "access denied" in s or "permission denied" in s or "forbidden" in s or "403" in s:
        return "forbidden"
    return "bq_error"


def _bq_show_table_json(*, job_project_id: str, table_fq: str) -> dict:
    project, dataset, table = _split_table_fq(table_fq)
    table_ref = f"{project}:{dataset}.{table}"
    cp = _run_with_retries(
        ["bq", "show", "--project_id", job_project_id, "--format=prettyjson", table_ref],
        check=False,
    )
    if cp.returncode != 0:
        kind = _classify_bq_error(cp.stderr)
        raise RuntimeError(f"{kind}: bq show failed for {table_ref}. stderr(first 800)={cp.stderr[:800]!r}")
    raw = (cp.stdout or "").strip()
    if not raw:
        raise RuntimeError(f"bq show returned empty stdout for {table_ref}")

    # Best-effort parsing: sometimes bq may emit non-JSON warnings before the JSON object.
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise RuntimeError(f"bq show returned invalid JSON for {table_ref}. stdout(first 400)={raw[:400]!r}")


def _bq_query_csv(*, job_project_id: str, sql: str, parameters: list[str]) -> str:
    cmd = [
        "bq",
        "query",
        "--project_id",
        job_project_id,
        "--use_legacy_sql=false",
        "--format=csv",
    ]
    for p in parameters:
        cmd.append(f"--parameter={p}")
    cmd.append(sql)
    cp = _run_with_retries(cmd, check=False)
    if cp.returncode != 0:
        kind = _classify_bq_error(cp.stderr)
        raise RuntimeError(f"{kind}: bq query failed. stderr(first 800)={cp.stderr[:800]!r}")
    return cp.stdout


def _parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    csv_text = (csv_text or "").strip()
    if not csv_text:
        return []
    return list(csv.DictReader(csv_text.splitlines()))


def _as_int(value: object) -> int:
    try:
        return int(float(str(value or "0").strip() or "0"))
    except Exception:  # noqa: BLE001
        return 0


def _as_float(value: object) -> float:
    try:
        return float(str(value or "0").strip() or "0")
    except Exception:  # noqa: BLE001
        return 0.0


def _infer_slot_auto(now_local: datetime, *, gh_schedule: str = "") -> str:
    """
    Infer slot for scheduled runs.

    When `gh_schedule` is provided (from `github.event.schedule`), prefer it over
    wall-clock gating to stay robust to scheduling delays and DST.
    """

    gh_schedule = (gh_schedule or "").strip()
    if gh_schedule:
        offset = now_local.utcoffset() or timedelta(0)
        is_cet = offset == timedelta(hours=1)
        is_cest = offset == timedelta(hours=2)

        # Workflow crons (UTC) in `.github/workflows/forecast-d1-readiness.yml`
        cet_08 = "0 7 * * *"   # 08:00 Europe/Prague (UTC+1)
        cet_10 = "0 9 * * *"   # 10:00 Europe/Prague (UTC+1)
        cet_15 = "0 14 * * *"  # 15:00 Europe/Prague (UTC+1)

        cest_08 = "0 6 * * *"   # 08:00 Europe/Prague (UTC+2)
        cest_10 = "0 8 * * *"   # 10:00 Europe/Prague (UTC+2)
        cest_15 = "0 13 * * *"  # 15:00 Europe/Prague (UTC+2)

        if is_cet:
            if gh_schedule == cet_08:
                return "08"
            if gh_schedule == cet_10:
                return "10"
            if gh_schedule == cet_15:
                return "15"
            return "noop"

        if is_cest:
            if gh_schedule == cest_08:
                return "08"
            if gh_schedule == cest_10:
                return "10"
            if gh_schedule == cest_15:
                return "15"
            return "noop"

        return "noop"

    # Fallback: allow any time within the local hour (handles common delays).
    if now_local.hour == 8:
        return "08"
    if now_local.hour == 10:
        return "10"
    if now_local.hour == 15:
        return "15"
    return "noop"


def _load_specs(csv_path: Path) -> list[PipelineSpec]:
    required = {
        "project_id",
        "tenant",
        "country",
        "location",
        "gcs_uri",
        "final_prep_txns_table",
        "final_prep_cost_table",
        "bq_table_13",
        "dts_config_13",
        "bq_table_14",
        "dts_config_14",
    }
    specs: list[PipelineSpec] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(row for row in f if row.strip() and not row.lstrip().startswith("#"))
        got = set(reader.fieldnames or [])
        if got != required:
            raise ValueError(f"Invalid CSV header in {csv_path}. Expected: {sorted(required)}; got: {reader.fieldnames}")
        for row in reader:
            specs.append(
                PipelineSpec(
                    project_id=row["project_id"].strip(),
                    tenant=row["tenant"].strip(),
                    country=row["country"].strip(),
                    # Preserve spaces in table names (legacy can have leading spaces in other columns).
                    bq_table_13=row["bq_table_13"],
                )
            )
    if not specs:
        raise ValueError(f"No specs found in {csv_path}")
    return specs


def _write_csv(path: Path, rows: list[ResultRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "project_id",
                "tenant",
                "country",
                "slot",
                "required_policy",
                "patch_date_local",
                "is_required",
                "table_fq",
                "status",
                "reason",
                "row_count",
                "sessions_sum",
                "revenue_db_sum",
                "transactions_db_sum",
                "actuals_sum",
                "cost_sum",
                "cost_present",
                "error_snippet",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.project_id,
                    r.tenant,
                    r.country,
                    r.slot,
                    r.required_policy,
                    r.patch_date_local,
                    r.is_required,
                    r.table_fq,
                    r.status,
                    r.reason,
                    str(r.row_count),
                    f"{r.sessions_sum:.6f}",
                    f"{r.revenue_db_sum:.6f}",
                    f"{r.transactions_db_sum:.6f}",
                    f"{r.actuals_sum:.6f}",
                    f"{r.cost_sum:.6f}",
                    r.cost_present,
                    r.error_snippet,
                ]
            )


def _write_md(
    path: Path,
    *,
    tz_name: str,
    checked_at_utc: datetime,
    patch_date_local: str,
    slot: str,
    required_policy: str,
    status: str,
    required_total: int,
    required_failed: int,
    optional_total: int,
    optional_failed: int,
    rows: list[ResultRow],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Forecast D-1 Readiness Report")
    lines.append("")
    lines.append(f"- Slot: `{slot}`")
    lines.append(f"- Required policy: `{required_policy}`")
    lines.append(f"- Patch date (local): `{patch_date_local}` (`{tz_name}`)")
    lines.append(f"- Checked at (UTC): `{checked_at_utc.replace(microsecond=0).isoformat().replace('+00:00','Z')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Status: **{status}**")
    lines.append(f"- Required: {required_total - required_failed} PASS / {required_failed} FAIL (total: {required_total})")
    lines.append(f"- Optional: {optional_total - optional_failed} PASS / {optional_failed} FAIL (total: {optional_total})")
    lines.append("")

    required_fail = [r for r in rows if r.status == "FAIL" and r.is_required == "yes"]
    optional_fail = [r for r in rows if r.status == "FAIL" and r.is_required != "yes"]

    if required_fail:
        lines.append("## Required failures (first 20)")
        lines.append("")
        lines.append("| Project | Tenant | Country | Table | Reason | row_count | actuals_sum | cost_sum |")
        lines.append("|---|---|---|---|---|---:|---:|---:|")
        for r in required_fail[:20]:
            lines.append(
                f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | `{r.table_fq}` | `{r.reason}` | {r.row_count} | {r.actuals_sum:.6f} | {r.cost_sum:.6f} |"
            )
        lines.append("")

    if optional_fail:
        lines.append("## Optional failures (first 20)")
        lines.append("")
        lines.append("| Project | Tenant | Country | Table | Reason | row_count | actuals_sum | cost_sum |")
        lines.append("|---|---|---|---|---|---:|---:|---:|")
        for r in optional_fail[:20]:
            lines.append(
                f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | `{r.table_fq}` | `{r.reason}` | {r.row_count} | {r.actuals_sum:.6f} | {r.cost_sum:.6f} |"
            )
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_json_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(*, config_csv: Path, outdir: Path, tz_name: str, patch_date_local_str: str, mode: str) -> int:
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz=tz)

    gh_schedule = (os.environ.get("GITHUB_EVENT_SCHEDULE", "") or "").strip()
    if mode == "manual":
        slot = "15"
        required_policy = "all"
    elif mode == "auto":
        slot = _infer_slot_auto(now_local, gh_schedule=gh_schedule)
        if slot == "noop":
            outdir.mkdir(parents=True, exist_ok=True)
            _write_json_summary(
                outdir / "forecast_d1_readiness_summary.json",
                {
                    "status": "NOOP",
                    "slot": "noop",
                    "required_policy": "",
                    "reason": "Outside execution window for auto mode",
                    "github_event_schedule": gh_schedule,
                    "now_local": now_local.isoformat(),
                    "timezone": tz_name,
                    "mode": mode,
                },
            )
            return 0
        required_policy = "cz_only" if slot in ("08", "10") else "all"
    else:
        raise ValueError(f"Invalid mode: {mode}")

    _ensure_cmd_available("bq")

    if patch_date_local_str.strip():
        patch_date_local = _parse_date_local(patch_date_local_str, tz)
    else:
        patch_date_local = (datetime.now(tz=tz).date() - timedelta(days=1))
    patch_date = patch_date_local.isoformat()

    outdir.mkdir(parents=True, exist_ok=True)
    checked_at_utc = datetime.now(tz=UTC)

    specs = _load_specs(config_csv)

    def is_required(country: str) -> bool:
        if required_policy == "all":
            return True
        return (country or "").strip().lower() == "cz"

    rows: list[ResultRow] = []
    required_total = 0
    required_failed = 0
    optional_total = 0
    optional_failed = 0

    for spec in specs:
        req = is_required(spec.country)
        if req:
            required_total += 1
        else:
            optional_total += 1

        print(f"[check] {spec.project_id} {spec.tenant}/{spec.country} (required={'yes' if req else 'no'})", file=sys.stderr)

        status = "FAIL"
        reason = "bq_error"
        row_count = 0
        sessions_sum = 0.0
        revenue_sum = 0.0
        transactions_sum = 0.0
        actuals_sum = 0.0
        cost_sum = 0.0
        cost_present = "no"
        error_snippet = ""

        try:
            meta = _bq_show_table_json(job_project_id=spec.project_id, table_fq=spec.bq_table_13)
            fields = meta.get("schema", {}).get("fields", []) or []
            cols = {str(f.get("name", "") or "").strip().lower() for f in fields if isinstance(f, dict)}
            missing = [c for c in REQUIRED_COLUMNS if c not in cols]
            cost_present = "yes" if OPTIONAL_COLUMN_COST in cols else "no"

            if missing:
                status = "FAIL"
                reason = "missing_columns:" + ",".join(missing)
            else:
                select_parts = [
                    "COUNT(1) AS row_count",
                    "IFNULL(SUM(sessions), 0) AS sessions_sum",
                    "IFNULL(SUM(revenue_db), 0) AS revenue_db_sum",
                    "IFNULL(SUM(transactions_db), 0) AS transactions_db_sum",
                ]
                if cost_present == "yes":
                    select_parts.append("IFNULL(SUM(cost), 0) AS cost_sum")

                sql = (
                    "SELECT "
                    + ", ".join(select_parts)
                    + f" FROM `{spec.bq_table_13}`"
                    + " WHERE CAST(date AS STRING)=@d"
                )

                out = _bq_query_csv(job_project_id=spec.project_id, sql=sql, parameters=[f"d:STRING:{patch_date}"])
                qrows = _parse_csv_rows(out)
                if qrows:
                    r = qrows[0]
                    row_count = _as_int(r.get("row_count"))
                    sessions_sum = _as_float(r.get("sessions_sum"))
                    revenue_sum = _as_float(r.get("revenue_db_sum"))
                    transactions_sum = _as_float(r.get("transactions_db_sum"))
                    actuals_sum = sessions_sum + revenue_sum + transactions_sum
                    if cost_present == "yes":
                        cost_sum = _as_float(r.get("cost_sum"))

                if row_count <= 0:
                    status = "FAIL"
                    reason = "no_rows_for_date"
                elif actuals_sum <= EPS:
                    status = "FAIL"
                    reason = "actuals_zero"
                else:
                    status = "PASS"
                    reason = ""
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            kind = "bq_error"
            if msg.startswith("not_found:"):
                kind = "not_found"
            elif msg.startswith("forbidden:"):
                kind = "forbidden"
            elif msg.startswith("bq_error:"):
                kind = "bq_error"

            reason = kind
            if "stderr(first" in msg:
                error_snippet = msg.split("stderr(first", 1)[-1]
                error_snippet = error_snippet[-800:]
            else:
                error_snippet = msg[:800]

        if status != "PASS":
            if req:
                required_failed += 1
            else:
                optional_failed += 1

        rows.append(
            ResultRow(
                project_id=spec.project_id,
                tenant=spec.tenant,
                country=spec.country,
                slot=slot,
                required_policy=required_policy,
                patch_date_local=patch_date,
                is_required="yes" if req else "no",
                table_fq=spec.bq_table_13,
                status=status,
                reason=reason,
                row_count=row_count,
                sessions_sum=sessions_sum,
                revenue_db_sum=revenue_sum,
                transactions_db_sum=transactions_sum,
                actuals_sum=actuals_sum,
                cost_sum=cost_sum,
                cost_present=cost_present,
                error_snippet=error_snippet,
            )
        )

    status = "FAIL" if required_failed > 0 else "PASS"

    report_csv = outdir / "forecast_d1_readiness_report.csv"
    report_md = outdir / "forecast_d1_readiness_report.md"
    summary_json = outdir / "forecast_d1_readiness_summary.json"

    _write_csv(report_csv, rows)
    _write_md(
        report_md,
        tz_name=tz_name,
        checked_at_utc=checked_at_utc,
        patch_date_local=patch_date,
        slot=slot,
        required_policy=required_policy,
        status="🚨 FAIL" if status == "FAIL" else "✅ PASS",
        required_total=required_total,
        required_failed=required_failed,
        optional_total=optional_total,
        optional_failed=optional_failed,
        rows=rows,
    )
    _write_json_summary(
        summary_json,
        {
            "status": status,
            "slot": slot,
            "required_policy": required_policy,
            "timezone": tz_name,
            "patch_date_local": patch_date,
            "github_event_schedule": gh_schedule,
            "now_local": now_local.isoformat(),
            "checked_at_utc": checked_at_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "pipelines_total": len(specs),
            "required_total": required_total,
            "required_failed": required_failed,
            "optional_total": optional_total,
            "optional_failed": optional_failed,
            "mode": mode,
        },
    )

    return 0 if required_failed == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser(description="Forecast D-1 readiness guardrail (inventory-driven, BigQuery).")
    ap.add_argument(
        "--config-csv",
        default="scripts/guardrails/forecast_pipelines.csv",
        help="CSV with forecast pipeline inventory (default: scripts/guardrails/forecast_pipelines.csv).",
    )
    ap.add_argument("--timezone", default="Europe/Prague")
    ap.add_argument(
        "--patch-date-local",
        default="",
        help="Date to check (YYYY-MM-DD, evaluated in --timezone). Default: yesterday in --timezone.",
    )
    ap.add_argument("--mode", default="auto", choices=["auto", "manual"])
    ap.add_argument("--outdir", default="forecast-d1-readiness-out")
    args = ap.parse_args()

    try:
        return run(
            config_csv=Path(args.config_csv),
            outdir=Path(args.outdir),
            tz_name=args.timezone,
            patch_date_local_str=args.patch_date_local.strip(),
            mode=args.mode.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        # Best-effort summary so CI can Slack even on unexpected errors.
        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        _write_json_summary(
            outdir / "forecast_d1_readiness_summary.json",
            {
                "status": "FAIL",
                "slot": "",
                "required_policy": "",
                "timezone": args.timezone,
                "patch_date_local": args.patch_date_local.strip(),
                "reason": "unexpected_error",
                "error": str(exc)[:800],
                "mode": args.mode,
            },
        )
        print(f"[error] Unexpected failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
