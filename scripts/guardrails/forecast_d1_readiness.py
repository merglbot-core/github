#!/usr/bin/env python3
"""
Forecast D-1 readiness guardrail.

Purpose:
- Verify that D-1 actuals are present in target final tables (inventory-driven).
- Designed to run from GitHub Actions with WIF/OIDC and Cloud SDK (bq) available.

Business policy (Europe/Prague):
- Scheduled hourly at 09:15–16:15.
- Required scope = all (all countries required) for every scheduled run.

DST-safe scheduling:
- Workflow schedules a UTC superset (07:15–15:15 UTC).
- Script uses GITHUB_EVENT_SCHEDULE + local UTC offset to NOOP outside the local execution window,
  ensuring only one run per local slot/day.

Never logs secret values.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
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
REQUIRED_COLUMNS_14_COMMON = ("date", "sessions", "revenue_db", "transactions_db")
REQUIRED_COLUMNS_14_DOMAIN = ("domain",)
REQUIRED_COLUMNS_14_COUNTRY = ("country",)
OPTIONAL_COLUMN_COST = "cost"

DOMAIN_OVERRIDES: dict[tuple[str, str], str] = {}

_BQ_FQ_TABLE_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+){2}$")
_DOMAIN_RE = re.compile(r"^[a-z0-9._\-]+$")


@dataclass(frozen=True)
class PipelineSpec:
    project_id: str
    tenant: str
    country: str
    bq_table_13: str
    bq_table_14: str


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
    status_13: str
    reason_13: str
    table_fq_14: str
    domain: str
    status_14: str
    reason_14: str
    row_count_14: int
    sessions_sum_14: float
    revenue_db_sum_14: float
    transactions_db_sum_14: float
    actuals_sum_14: float
    cost_sum_14: float
    cost_present_14: str
    error_snippet_14: str


@dataclass(frozen=True)
class ChannelResultRow:
    tenant: str
    country: str
    channel: str
    row_count: int
    revenue_db_sum: float
    cost_sum: float
    cost_present: str
    status: str
    reason: str


def _norm_key(value: str) -> str:
    return (value or "").strip().casefold()


def _wanted_channels(tenant: str, country: str) -> list[str]:
    t = (tenant or "").strip().lower()
    c = (country or "").strip().lower()
    if not t or not c:
        return []

    if t in ("proteinaco", "denatura", "cerano", "livero"):
        return ["Google Ads", "Facebook"]
    if t == "autodoplnky" and c == "cz":
        return ["Google Ads", "Facebook"]
    if t == "ruzovyslon":
        return ["Google ads pmax"]
    return []


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


def _normalize_table_fq(table_fq: str, *, allow_empty: bool = False) -> str:
    t = (table_fq or "").strip()
    if not t:
        if allow_empty:
            return ""
        raise ValueError("invalid_bq_table:empty")
    if "`" in t or "\n" in t or "\r" in t or not _BQ_FQ_TABLE_RE.match(t):
        raise ValueError(f"invalid_bq_table:{t}")
    return t


def _split_table_fq(table_fq: str) -> tuple[str, str, str]:
    table_fq = _normalize_table_fq(table_fq)
    parts = table_fq.split(".", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid_bq_table:{table_fq}")
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

    lines = [ln for ln in csv_text.splitlines() if ln.strip()]

    # `bq` can emit auth-related warnings to stdout in some environments (notably external_account),
    # which would break CSV parsing (header not on the first line).
    noise_prefixes = ("WARNING:", "INFO:", "NOTE:")
    lines = [ln for ln in lines if not ln.lstrip().startswith(noise_prefixes)]
    if not lines:
        return []

    # Drop UTF-8 BOM if present.
    lines[0] = lines[0].lstrip("\ufeff")

    reader = csv.DictReader(lines)
    return [dict(r) for r in reader]


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


def _fmt6(value: object) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return "—"


def _infer_slot_auto(now_local: datetime, *, gh_schedule: str = "") -> str:
    """
    Infer slot for scheduled runs.

    When `gh_schedule` is provided (from `github.event.schedule`), prefer it over
    wall-clock gating to stay robust to scheduling delays and DST.
    """

    gh_schedule = (gh_schedule or "").strip()
    if gh_schedule:
        parts = gh_schedule.split()
        if len(parts) < 2:
            return "noop"
        try:
            utc_minute = int(parts[0])
            utc_hour = int(parts[1])
        except ValueError:
            return "noop"

        if utc_minute != 15:
            return "noop"

        offset = now_local.utcoffset() or timedelta(0)
        offset_hours = int(offset.total_seconds() // 3600)
        local_hour = utc_hour + offset_hours
        if 9 <= local_hour <= 16:
            return f"{local_hour:02d}"
        return "noop"

    # Fallback: allow any time within the local hour (handles common delays).
    if 9 <= now_local.hour <= 16:
        return f"{now_local.hour:02d}"
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
                    bq_table_14=row.get("bq_table_14", ""),
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
                "status_13",
                "reason_13",
                "table_fq_14",
                "domain",
                "status_14",
                "reason_14",
                "row_count_14",
                "sessions_sum_14",
                "revenue_db_sum_14",
                "transactions_db_sum_14",
                "actuals_sum_14",
                "cost_sum_14",
                "cost_present_14",
                "error_snippet_14",
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
                    r.status_13,
                    r.reason_13,
                    r.table_fq_14,
                    r.domain,
                    r.status_14,
                    r.reason_14,
                    str(r.row_count_14),
                    f"{r.sessions_sum_14:.6f}",
                    f"{r.revenue_db_sum_14:.6f}",
                    f"{r.transactions_db_sum_14:.6f}",
                    f"{r.actuals_sum_14:.6f}",
                    f"{r.cost_sum_14:.6f}",
                    r.cost_present_14,
                    r.error_snippet_14,
                ]
                )


def _write_channels_csv(path: Path, rows: list[ChannelResultRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "tenant",
                "country",
                "channel",
                "row_count",
                "revenue_db_sum",
                "cost_sum",
                "cost_present",
                "status",
                "reason",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.tenant,
                    r.country,
                    r.channel,
                    str(r.row_count),
                    f"{r.revenue_db_sum:.6f}",
                    f"{r.cost_sum:.6f}",
                    r.cost_present,
                    r.status,
                    r.reason,
                ]
            )


def _domain_for(tenant: str, country: str) -> str:
    """Build domain string from tenant+country with validation."""
    key = ((tenant or "").strip().lower(), (country or "").strip().lower())
    dom = DOMAIN_OVERRIDES.get(key, f"{key[0]}.{key[1]}")
    dom = (dom or "").strip().lower()
    if dom and not _DOMAIN_RE.match(dom):
        raise ValueError(f"invalid_domain:{dom}")
    return dom


def _md_row_data(r) -> tuple:
    """Extract row data for MD table, handling 14_* overrides."""
    table_fq = r.table_fq
    reason = r.reason
    row_count = r.row_count
    actuals_sum = r.actuals_sum
    cost_sum = r.cost_sum
    if (r.reason or "").startswith("14_"):
        table_fq = r.table_fq_14 or r.table_fq
        dom = (r.domain or "").strip() or "?"
        reason = f"{r.reason} (domain={dom})"
        row_count = r.row_count_14
        actuals_sum = r.actuals_sum_14
        cost_sum = r.cost_sum_14
    return table_fq, reason, row_count, actuals_sum, cost_sum


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
    channel_rows: list[ChannelResultRow],
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
            table_fq, reason, row_count, actuals_sum, cost_sum = _md_row_data(r)
            lines.append(
                f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | `{table_fq}` | `{reason}` | {row_count} | {_fmt6(actuals_sum)} | {_fmt6(cost_sum)} |"
            )
        lines.append("")

    if optional_fail:
        lines.append("## Optional failures (first 20)")
        lines.append("")
        lines.append("| Project | Tenant | Country | Table | Reason | row_count | actuals_sum | cost_sum |")
        lines.append("|---|---|---|---|---|---:|---:|---:|")
        for r in optional_fail[:20]:
            table_fq, reason, row_count, actuals_sum, cost_sum = _md_row_data(r)
            lines.append(
                f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | `{table_fq}` | `{reason}` | {row_count} | {_fmt6(actuals_sum)} | {_fmt6(cost_sum)} |"
            )
        lines.append("")

    lines.append("## Channel checks (selected)")
    lines.append("")
    lines.append("Full detail in artifact `forecast_d1_readiness_channels_report.csv`.")
    lines.append("")

    if channel_rows:
        # Map main guardrail status per tenant/country to drive icon semantics.
        status13_map: dict[tuple[str, str], str] = {}
        for r in rows:
            key = ((r.tenant or "").strip().lower(), (r.country or "").strip().lower())
            status13_map[key] = (r.status_13 or "").strip()

        lines.append("| Tenant | Country | Channel | rev | cost | status | reason |")
        lines.append("|---|---|---|---|---|---|---|")

        MAX_CHANNEL_MD_ROWS = 200
        for cr in channel_rows[:MAX_CHANNEL_MD_ROWS]:
            key = ((cr.tenant or "").strip().lower(), (cr.country or "").strip().lower())
            st13 = (status13_map.get(key, "") or "").strip()

            rev_icon = "❌"
            cost_icon = "❌"
            if st13 == "PASS":
                if cr.status in ("SKIP", "ERROR"):
                    rev_icon = "⚠️"
                    cost_icon = "⚠️"
                else:
                    rev_icon = "✅" if abs(cr.revenue_db_sum) > EPS else "⚠️"
                    if (cr.cost_present or "").strip().lower() == "yes":
                        cost_icon = "✅" if abs(cr.cost_sum) > EPS else "⚠️"
                    else:
                        cost_icon = "⚠️"

            reason = cr.reason if (cr.reason or "").strip() else "—"
            lines.append(
                f"| `{cr.tenant}` | `{cr.country}` | `{cr.channel}` | {rev_icon} | {cost_icon} | `{cr.status}` | `{reason}` |"
            )

        if len(channel_rows) > MAX_CHANNEL_MD_ROWS:
            lines.append("")
            lines.append("_... truncated; see CSV._")
            lines.append("")
    else:
        lines.append("_No channel checks configured._")
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
        required_policy = "all"
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
    channel_rows: list[ChannelResultRow] = []
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

        status_13 = "FAIL"
        reason_13 = "bq_error"
        row_count = 0
        sessions_sum = 0.0
        revenue_sum = 0.0
        transactions_sum = 0.0
        actuals_sum = 0.0
        cost_sum = 0.0
        cost_present = "no"
        error_snippet = ""
        cols: set[str] = set()  # table_13 column names (lowercased); populated when bq show succeeds
        table13_exception_kind = ""
        table_fq_13 = ""

        try:
            table_fq_13 = _normalize_table_fq(spec.bq_table_13)
            meta = _bq_show_table_json(job_project_id=spec.project_id, table_fq=table_fq_13)
            fields = meta.get("schema", {}).get("fields", []) or []
            cols = {str(f.get("name", "") or "").strip().lower() for f in fields if isinstance(f, dict)}
            missing = [c for c in REQUIRED_COLUMNS if c not in cols]
            cost_present = "yes" if OPTIONAL_COLUMN_COST in cols else "no"

            if missing:
                status_13 = "FAIL"
                reason_13 = "missing_columns:" + ",".join(missing)
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
                    + f" FROM `{table_fq_13}`"
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
                    # Refunds/returns can make revenue negative; use abs() to avoid false "actuals_zero".
                    actuals_sum = abs(sessions_sum) + abs(revenue_sum) + abs(transactions_sum)
                    if cost_present == "yes":
                        cost_sum = _as_float(r.get("cost_sum"))

                if row_count <= 0:
                    status_13 = "FAIL"
                    reason_13 = "no_rows_for_date"
                elif actuals_sum <= EPS:
                    status_13 = "FAIL"
                    reason_13 = "actuals_zero"
                else:
                    status_13 = "PASS"
                    reason_13 = ""
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            kind = "bq_error"
            if msg.startswith("invalid_bq_table:"):
                kind = "invalid_bq_table"
            elif msg.startswith("not_found:"):
                kind = "not_found"
            elif msg.startswith("forbidden:"):
                kind = "forbidden"
            elif msg.startswith("bq_error:"):
                kind = "bq_error"

            reason_13 = kind
            table13_exception_kind = kind
            if "stderr(first" in msg:
                error_snippet = msg.split("stderr(first", 1)[-1]
                error_snippet = error_snippet[-800:]
            else:
                error_snippet = msg[:800]

        wanted = _wanted_channels(spec.tenant, spec.country)
        if wanted:
            default_cost_present = cost_present if cost_present in ("yes", "no") else "no"
            if table13_exception_kind:
                for ch_name in wanted:
                    channel_rows.append(
                        ChannelResultRow(
                            tenant=spec.tenant,
                            country=spec.country,
                            channel=ch_name,
                            row_count=0,
                            revenue_db_sum=0.0,
                            cost_sum=0.0,
                            cost_present=default_cost_present,
                            status="ERROR",
                            reason=table13_exception_kind,
                        )
                    )
            elif status_13 != "PASS":
                for ch_name in wanted:
                    channel_rows.append(
                        ChannelResultRow(
                            tenant=spec.tenant,
                            country=spec.country,
                            channel=ch_name,
                            row_count=0,
                            revenue_db_sum=0.0,
                            cost_sum=0.0,
                            cost_present=default_cost_present,
                            status="SKIP",
                            reason=f"guardrail_not_pass:{reason_13 or 'FAIL'}",
                        )
                    )
            elif "channel" not in cols:
                for ch_name in wanted:
                    channel_rows.append(
                        ChannelResultRow(
                            tenant=spec.tenant,
                            country=spec.country,
                            channel=ch_name,
                            row_count=0,
                            revenue_db_sum=0.0,
                            cost_sum=0.0,
                            cost_present=default_cost_present,
                            status="SKIP",
                            reason="missing_channel_column",
                        )
                    )
            else:
                # 1 query per tenant/country (domain) to get per-channel revenue/cost.
                agg: dict[str, tuple[int, float, float]] = {}
                ch_query_kind = ""
                try:
                    # NOTE: Table FQ name is validated by `_normalize_table_fq` (no backticks/newlines; strict `project.dataset.table`).
                    table_fq_13_safe = _normalize_table_fq(table_fq_13)
                    select_parts_ch = [
                        "CAST(channel AS STRING) AS channel",
                        "COUNT(1) AS row_count",
                        "IFNULL(SUM(revenue_db), 0) AS revenue_db_sum",
                    ]
                    if default_cost_present == "yes":
                        select_parts_ch.append("IFNULL(SUM(cost), 0) AS cost_sum")

                    sql_ch = (
                        "SELECT "
                        + ", ".join(select_parts_ch)
                        + f" FROM `{table_fq_13_safe}`"
                        + " WHERE CAST(date AS STRING)=@d"
                        + " GROUP BY channel"
                    )
                    out_ch = _bq_query_csv(
                        job_project_id=spec.project_id,
                        sql=sql_ch,
                        parameters=[f"d:STRING:{patch_date}"],
                    )
                    qrows_ch = _parse_csv_rows(out_ch)
                    for row_ch in qrows_ch:
                        ch_val = str(row_ch.get("channel") or "").strip()
                        k = _norm_key(ch_val)
                        if not k:
                            continue
                        rc = _as_int(row_ch.get("row_count"))
                        rev = _as_float(row_ch.get("revenue_db_sum"))
                        cost = _as_float(row_ch.get("cost_sum")) if default_cost_present == "yes" else 0.0
                        prev = agg.get(k)
                        if prev is None:
                            agg[k] = (rc, rev, cost)
                        else:
                            agg[k] = (prev[0] + rc, prev[1] + rev, prev[2] + cost)
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    kind = "bq_error"
                    if msg.startswith("invalid_bq_table:"):
                        kind = "invalid_bq_table"
                    elif msg.startswith("not_found:"):
                        kind = "not_found"
                    elif msg.startswith("forbidden:"):
                        kind = "forbidden"
                    elif msg.startswith("bq_error:"):
                        kind = "bq_error"
                    ch_query_kind = kind

                for ch_name in wanted:
                    if ch_query_kind:
                        channel_rows.append(
                            ChannelResultRow(
                                tenant=spec.tenant,
                                country=spec.country,
                                channel=ch_name,
                                row_count=0,
                                revenue_db_sum=0.0,
                                cost_sum=0.0,
                                cost_present=default_cost_present,
                                status="ERROR",
                                reason=ch_query_kind,
                            )
                        )
                    else:
                        rc, rev, cost = agg.get(_norm_key(ch_name), (0, 0.0, 0.0))
                        channel_rows.append(
                            ChannelResultRow(
                                tenant=spec.tenant,
                                country=spec.country,
                                channel=ch_name,
                                row_count=rc,
                                revenue_db_sum=rev,
                                cost_sum=cost,
                                cost_present=default_cost_present,
                                status="OK",
                                reason="",
                            )
                        )

        table_fq_14 = spec.bq_table_14
        has_14 = bool((table_fq_14 or "").strip())
        dom = ""
        status_14 = ""
        reason_14 = ""
        row_count_14 = 0
        sessions_sum_14 = 0.0
        revenue_sum_14 = 0.0
        transactions_sum_14 = 0.0
        actuals_sum_14 = 0.0
        cost_sum_14 = 0.0
        cost_present_14 = "no"
        error_snippet_14 = ""

        dom = ""
        if has_14:
            status_14 = "FAIL"
            reason_14 = "14_bq_error"
            try:
                dom = _domain_for(spec.tenant, spec.country)
                table_fq_14_norm = _normalize_table_fq(table_fq_14)
                meta14 = _bq_show_table_json(job_project_id=spec.project_id, table_fq=table_fq_14_norm)
                fields14 = meta14.get("schema", {}).get("fields", []) or []
                cols14 = {str(f.get("name", "") or "").strip().lower() for f in fields14 if isinstance(f, dict)}
                cost_present_14 = "yes" if OPTIONAL_COLUMN_COST in cols14 else "no"

                required14: tuple[str, ...] = REQUIRED_COLUMNS_14_COMMON + REQUIRED_COLUMNS_14_DOMAIN + REQUIRED_COLUMNS_14_COUNTRY
                where_14 = ""
                params14 = [f"d:STRING:{patch_date}"]
                dom_norm = (dom or "").strip()
                country_param = (spec.country or "").strip().lower()
                invalid_filter_reason = ""

                # Prefer domain filtering when available; fallback to country for all-countries 14_* tables.
                if "domain" in cols14 and dom_norm:
                    required14 = REQUIRED_COLUMNS_14_COMMON + REQUIRED_COLUMNS_14_DOMAIN
                    where_14 = "CAST(date AS STRING)=@d AND domain=@dom"
                    params14.append(f"dom:STRING:{dom_norm}")
                elif "country" in cols14 and country_param:
                    required14 = REQUIRED_COLUMNS_14_COMMON + REQUIRED_COLUMNS_14_COUNTRY
                    where_14 = "CAST(date AS STRING)=@d AND LOWER(CAST(country AS STRING))=@c"
                    params14.append(f"c:STRING:{country_param}")
                elif "domain" in cols14:
                    required14 = REQUIRED_COLUMNS_14_COMMON + REQUIRED_COLUMNS_14_DOMAIN
                    invalid_filter_reason = "14_invalid_filter_value:domain"
                elif "country" in cols14:
                    required14 = REQUIRED_COLUMNS_14_COMMON + REQUIRED_COLUMNS_14_COUNTRY
                    invalid_filter_reason = "14_invalid_filter_value:country"

                missing14 = [col for col in required14 if col not in cols14]
                if missing14:
                    status_14 = "FAIL"
                    reason_14 = "14_missing_columns:" + ",".join(missing14)
                elif invalid_filter_reason:
                    status_14 = "FAIL"
                    reason_14 = invalid_filter_reason
                elif not where_14:
                    status_14 = "FAIL"
                    reason_14 = "14_internal_error_empty_where"
                else:
                    select_parts_14 = [
                        "COUNT(1) AS row_count_14",
                        "IFNULL(SUM(sessions), 0) AS sessions_sum_14",
                        "IFNULL(SUM(revenue_db), 0) AS revenue_db_sum_14",
                        "IFNULL(SUM(transactions_db), 0) AS transactions_db_sum_14",
                    ]
                    if cost_present_14 == "yes":
                        select_parts_14.append("IFNULL(SUM(cost), 0) AS cost_sum_14")

                    sql14 = (
                        "SELECT "
                        + ", ".join(select_parts_14)
                        + f" FROM `{table_fq_14_norm}`"
                        + " WHERE "
                        + where_14
                    )
                    out14 = _bq_query_csv(
                        job_project_id=spec.project_id,
                        sql=sql14,
                        parameters=params14,
                    )
                    qrows14 = _parse_csv_rows(out14)
                    if qrows14:
                        r14 = qrows14[0]
                        row_count_14 = _as_int(r14.get("row_count_14"))
                        sessions_sum_14 = _as_float(r14.get("sessions_sum_14"))
                        revenue_sum_14 = _as_float(r14.get("revenue_db_sum_14"))
                        transactions_sum_14 = _as_float(r14.get("transactions_db_sum_14"))
                        actuals_sum_14 = abs(sessions_sum_14) + abs(revenue_sum_14) + abs(transactions_sum_14)
                        if cost_present_14 == "yes":
                            cost_sum_14 = _as_float(r14.get("cost_sum_14"))

                    if row_count_14 <= 0:
                        status_14 = "FAIL"
                        reason_14 = "14_no_rows_for_date"
                    elif actuals_sum_14 <= EPS:
                        status_14 = "FAIL"
                        reason_14 = "14_actuals_zero"
                    else:
                        status_14 = "PASS"
                        reason_14 = ""
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                kind = "bq_error"
                if msg.startswith("invalid_domain:"):
                    kind = "invalid_domain"
                elif msg.startswith("invalid_bq_table:"):
                    kind = "invalid_bq_table"
                elif msg.startswith("not_found:"):
                    kind = "not_found"
                elif msg.startswith("forbidden:"):
                    kind = "forbidden"
                elif msg.startswith("bq_error:"):
                    kind = "bq_error"

                status_14 = "FAIL"
                reason_14 = f"14_{kind}"
                if "stderr(first" in msg:
                    error_snippet_14 = msg.split("stderr(first", 1)[-1]
                    error_snippet_14 = error_snippet_14[-800:]
                else:
                    error_snippet_14 = msg[:800]

        status = "FAIL"
        reason = ""
        if status_13 != "PASS":
            status = "FAIL"
            reason = reason_13
        elif has_14 and status_14 != "PASS":
            status = "FAIL"
            reason = reason_14
        else:
            status = "PASS"
            reason = ""

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
                status_13=status_13,
                reason_13=reason_13,
                table_fq_14=table_fq_14,
                domain=dom,
                status_14=status_14,
                reason_14=reason_14,
                row_count_14=row_count_14,
                sessions_sum_14=sessions_sum_14,
                revenue_db_sum_14=revenue_sum_14,
                transactions_db_sum_14=transactions_sum_14,
                actuals_sum_14=actuals_sum_14,
                cost_sum_14=cost_sum_14,
                cost_present_14=cost_present_14,
                error_snippet_14=error_snippet_14,
            )
        )

    status = "FAIL" if required_failed > 0 else "PASS"

    report_csv = outdir / "forecast_d1_readiness_report.csv"
    channels_csv = outdir / "forecast_d1_readiness_channels_report.csv"
    report_md = outdir / "forecast_d1_readiness_report.md"
    summary_json = outdir / "forecast_d1_readiness_summary.json"

    _write_csv(report_csv, rows)
    _write_channels_csv(channels_csv, channel_rows)
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
        channel_rows=channel_rows,
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
