#!/usr/bin/env python3
"""
Forecast self-heal guardrail

Purpose:
- Verify that forecast "final" outputs for the target date are not a zero snapshot.
- If needed, patch the GCS forecast CSVs from final_prep_* tables and trigger DTS runs.

Key business rules (Europe/Prague):
- CZ (country=cz): D-1 must be ready in the morning window.
- non-CZ: morning D-2 is acceptable; D-1 must be ready only from the afternoon window.

This script is designed to run from GitHub Actions with WIF/OIDC and Cloud SDK (bq/gsutil) available.
Never logs secret values.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo


EPS = 1e-9


@dataclass(frozen=True)
class PipelineSpec:
    project_id: str
    tenant: str
    country: str
    location: str
    gcs_uri: str
    final_prep_txns_table: str
    final_prep_cost_table: str
    bq_table_13: str
    dts_config_13: str
    bq_table_14: str
    dts_config_14: str


@dataclass(frozen=True)
class ResultRow:
    project_id: str
    tenant: str
    country: str
    run_mode: str
    patch_date_local: str
    status: str
    reason: str
    gcs_uri: str
    bq_table_13: str
    bq_table_14: str
    final_prep_sessions_sum: float
    final_prep_revenue_db_sum: float
    final_prep_transactions_db_sum: float
    bq13_sessions_sum: float
    bq13_revenue_db_sum: float
    bq13_transactions_db_sum: float
    bq13_cost_sum: float
    patched_metrics: str
    triggered_13_run: str
    triggered_14_run: str


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


def _infer_run_mode_auto(now_local: datetime) -> str:
    # Allow a small window to avoid clock jitter.
    if now_local.hour == 6 and 0 <= now_local.minute <= 15:
        return "cz_morning"
    if now_local.hour == 14 and 0 <= now_local.minute <= 15:
        return "noncz_afternoon"
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
                    location=row["location"].strip(),
                    gcs_uri=row["gcs_uri"].strip(),
                    # Preserve spaces in BQ table names - some legacy tables have leading spaces
                    # (e.g. "final_prep_si. 8_join_*"). Stripping would break BQ queries.
                    final_prep_txns_table=row["final_prep_txns_table"],
                    final_prep_cost_table=row["final_prep_cost_table"],
                    bq_table_13=row["bq_table_13"],
                    dts_config_13=row["dts_config_13"].strip(),
                    bq_table_14=row["bq_table_14"],
                    dts_config_14=row["dts_config_14"].strip(),
                )
            )
    if not specs:
        raise ValueError(f"No specs found in {csv_path}")
    return specs


def _bq_query_csv(*, project_id: str, sql: str, parameters: Iterable[str]) -> str:
    cmd = [
        "bq",
        "query",
        "--project_id",
        project_id,
        "--use_legacy_sql=false",
        "--format=csv",
    ]
    for p in parameters:
        cmd.append(f"--parameter={p}")
    cmd.append(sql)
    cp = _run_with_retries(cmd, check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"bq query failed (project={project_id}). stderr(first 800)={cp.stderr[:800]!r}")
    return cp.stdout


def _parse_csv_rows(text: str) -> list[dict[str, str]]:
    text = (text or "").strip()
    if not text:
        return []
    reader = csv.DictReader(text.splitlines())
    return [dict(r) for r in reader]


def _as_float(v: Any) -> float:
    try:
        if v is None:
            return 0.0
        s = str(v).strip()
        if not s:
            return 0.0
        return float(s)
    except Exception:
        return 0.0


def _final_prep_by_channel(
    *,
    project_id: str,
    patch_date: str,
    txns_table: str,
    cost_table: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    # Use CAST(date AS STRING) filter so the query works for both DATE and STRING typed date columns.
    sql_txns = (
        "SELECT channel,"
        " SUM(sessions) AS sessions,"
        " SUM(quantity_db) AS quantity_db,"
        " SUM(revenue_db) AS revenue_db,"
        " SUM(revenue_with_vat_db) AS revenue_with_vat_db,"
        " SUM(buyprice_db) AS buyprice_db,"
        " SUM(margin_db) AS margin_db,"
        " SUM(transactions_db) AS transactions_db"
        f" FROM `{txns_table}`"
        " WHERE CAST(date AS STRING)=@d"
        " GROUP BY channel"
    )
    sql_cost = (
        "SELECT channel,"
        " SUM(clicks) AS clicks,"
        " SUM(cost) AS cost,"
        " SUM(impressions) AS impressions"
        f" FROM `{cost_table}`"
        " WHERE CAST(date AS STRING)=@d"
        " GROUP BY channel"
    )
    txns_rows = _parse_csv_rows(
        _bq_query_csv(project_id=project_id, sql=sql_txns, parameters=[f"d:STRING:{patch_date}"])
    )
    cost_rows = _parse_csv_rows(
        _bq_query_csv(project_id=project_id, sql=sql_cost, parameters=[f"d:STRING:{patch_date}"])
    )

    txns: dict[str, dict[str, float]] = {}
    for r in txns_rows:
        ch = (r.get("channel") or "").strip()
        if not ch:
            continue
        txns[ch] = {
            "sessions": _as_float(r.get("sessions")),
            "quantity_db": _as_float(r.get("quantity_db")),
            "revenue_db": _as_float(r.get("revenue_db")),
            "revenue_with_vat_db": _as_float(r.get("revenue_with_vat_db")),
            "buyprice_db": _as_float(r.get("buyprice_db")),
            "margin_db": _as_float(r.get("margin_db")),
            "transactions_db": _as_float(r.get("transactions_db")),
        }

    cost: dict[str, dict[str, float]] = {}
    for r in cost_rows:
        ch = (r.get("channel") or "").strip()
        if not ch:
            continue
        cost[ch] = {
            "clicks": _as_float(r.get("clicks")),
            "cost": _as_float(r.get("cost")),
            "impressions": _as_float(r.get("impressions")),
        }

    return txns, cost


def _bq_table_totals(
    *,
    project_id: str,
    patch_date: str,
    table_fq: str,
) -> dict[str, float]:
    sql = (
        "SELECT"
        " SUM(sessions) AS sessions_sum,"
        " SUM(revenue_db) AS revenue_db_sum,"
        " SUM(transactions_db) AS transactions_db_sum,"
        " SUM(cost) AS cost_sum"
        f" FROM `{table_fq}`"
        " WHERE CAST(date AS STRING)=@d"
    )
    rows = _parse_csv_rows(_bq_query_csv(project_id=project_id, sql=sql, parameters=[f"d:STRING:{patch_date}"]))
    if not rows:
        return {"sessions_sum": 0.0, "revenue_db_sum": 0.0, "transactions_db_sum": 0.0, "cost_sum": 0.0}
    r = rows[0]
    return {
        "sessions_sum": _as_float(r.get("sessions_sum")),
        "revenue_db_sum": _as_float(r.get("revenue_db_sum")),
        "transactions_db_sum": _as_float(r.get("transactions_db_sum")),
        "cost_sum": _as_float(r.get("cost_sum")),
    }


def _gsutil_cp(src: str, dst: str) -> None:
    cp = _run_with_retries(["gsutil", "cp", src, dst], check=False)
    if cp.returncode != 0:
        raise RuntimeError(f"gsutil cp failed: {src} -> {dst}. stderr(first 800)={cp.stderr[:800]!r}")


def _trigger_transfer_run(*, project_id: str, config_resource_name: str, run_time_utc: str) -> None:
    cp = _run_with_retries(
        [
            "bq",
            "mk",
            "--project_id",
            project_id,
            "--transfer_run",
            f"--run_time={run_time_utc}",
            config_resource_name,
        ],
        check=False,
    )
    if cp.returncode != 0:
        raise RuntimeError(f"bq mk --transfer_run failed. stderr(first 800)={cp.stderr[:800]!r}")


def _largest_remainder_int_allocation(total: int, weights: list[float]) -> list[int]:
    if total <= 0:
        return [0 for _ in weights]
    if not weights:
        return []
    s = sum(w for w in weights if w > 0)
    if s <= 0:
        # uniform
        base = total // len(weights)
        rem = total - base * len(weights)
        out = [base for _ in weights]
        for i in range(rem):
            out[i] += 1
        return out
    wnorm = [max(0.0, w) / s for w in weights]
    raw = [total * w for w in wnorm]
    floors = [int(x) for x in raw]
    rem = total - sum(floors)
    fracs = sorted([(raw[i] - floors[i], i) for i in range(len(weights))], reverse=True)
    out = floors[:]
    for j in range(rem):
        out[fracs[j % len(fracs)][1]] += 1
    return out


def _build_actuals_mapping(
    *,
    channels_target: list[str],
    txns_by_channel: dict[str, dict[str, float]],
    cost_by_channel: dict[str, dict[str, float]],
) -> dict[str, dict[str, str]]:
    # Base mapping from final_prep. Missing channel -> 0.
    mapping: dict[str, dict[str, float]] = {}
    for ch in channels_target:
        tx = txns_by_channel.get(ch, {})
        co = cost_by_channel.get(ch, {})
        mapping[ch] = {
            "sessions": float(tx.get("sessions", 0.0)),
            "quantity_db": float(tx.get("quantity_db", 0.0)),
            "revenue_db": float(tx.get("revenue_db", 0.0)),
            "revenue_with_vat_db": float(tx.get("revenue_with_vat_db", 0.0)),
            "buyprice_db": float(tx.get("buyprice_db", 0.0)),
            "margin_db": float(tx.get("margin_db", 0.0)),
            "transactions_db": float(tx.get("transactions_db", 0.0)),
            "clicks": float(co.get("clicks", 0.0)),
            "cost": float(co.get("cost", 0.0)),
            "impressions": float(co.get("impressions", 0.0)),
        }

    # If final_prep has not_in_ga4 but CSV doesn't, distribute its txn metrics to existing channels.
    if "not_in_ga4" in txns_by_channel and "not_in_ga4" not in set(channels_target):
        not_row = txns_by_channel.get("not_in_ga4", {})
        not_revenue = float(not_row.get("revenue_db", 0.0))
        not_sessions = float(not_row.get("sessions", 0.0))
        not_quantity = int(round(float(not_row.get("quantity_db", 0.0))))
        not_transactions = int(round(float(not_row.get("transactions_db", 0.0))))
        not_rev_with_vat = float(not_row.get("revenue_with_vat_db", 0.0))
        not_buyprice = float(not_row.get("buyprice_db", 0.0))
        not_margin = float(not_row.get("margin_db", 0.0))

        # weights: revenue_db positive share, else sessions positive share, else uniform
        weights: list[float] = []
        for ch in channels_target:
            weights.append(max(0.0, mapping.get(ch, {}).get("revenue_db", 0.0)))
        if sum(weights) <= 0:
            weights = [max(0.0, mapping.get(ch, {}).get("sessions", 0.0)) for ch in channels_target]
        if sum(weights) <= 0:
            weights = [1.0 for _ in channels_target]

        # Distribute float metrics
        s = sum(weights) if sum(weights) > 0 else 1.0
        wnorm = [w / s for w in weights]
        for i, ch in enumerate(channels_target):
            mapping[ch]["revenue_db"] += wnorm[i] * not_revenue
            mapping[ch]["revenue_with_vat_db"] += wnorm[i] * not_rev_with_vat
            mapping[ch]["buyprice_db"] += wnorm[i] * not_buyprice
            mapping[ch]["margin_db"] += wnorm[i] * not_margin
            mapping[ch]["sessions"] += wnorm[i] * not_sessions

        # Distribute integer-like metrics with largest remainder
        qty_alloc = _largest_remainder_int_allocation(not_quantity, weights)
        trx_alloc = _largest_remainder_int_allocation(not_transactions, weights)
        for i, ch in enumerate(channels_target):
            mapping[ch]["quantity_db"] += float(qty_alloc[i])
            mapping[ch]["transactions_db"] += float(trx_alloc[i])

    # Convert to string mapping with correct integer formatting for integer columns.
    int_cols = {"sessions", "quantity_db", "transactions_db", "clicks", "impressions"}
    out: dict[str, dict[str, str]] = {}
    for ch, vals in mapping.items():
        row: dict[str, str] = {}
        for k, v in vals.items():
            if k in int_cols:
                row[k] = str(int(round(v)))
            else:
                # keep compact float formatting
                row[k] = ("0" if abs(v) < EPS else repr(float(v)))
        out[ch] = row
    return out


def _patch_csv_file(
    *,
    input_path: Path,
    output_path: Path,
    patch_date: str,
    actuals_by_channel: dict[str, dict[str, str]],
) -> tuple[int, list[str]]:
    patched_rows = 0
    patched_cols: set[str] = set()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8", newline="") as f_in:
        reader = csv.DictReader(f_in)
        fieldnames = list(reader.fieldnames or [])
        if not fieldnames or "date" not in fieldnames or "channel" not in fieldnames:
            raise ValueError("CSV missing required columns: date/channel")

        with output_path.open("w", encoding="utf-8", newline="") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()

            for row in reader:
                if (row.get("date") or "").strip() == patch_date:
                    ch = (row.get("channel") or "").strip()
                    actual = actuals_by_channel.get(ch)
                    # Patch only if the columns exist in this CSV
                    if actual:
                        for col, v in actual.items():
                            if col in fieldnames:
                                row[col] = v
                                patched_cols.add(col)
                        patched_rows += 1
                    else:
                        # Unknown channel -> zero out known actual columns (if present)
                        for col in [
                            "sessions",
                            "quantity_db",
                            "revenue_db",
                            "revenue_with_vat_db",
                            "buyprice_db",
                            "margin_db",
                            "transactions_db",
                            "clicks",
                            "cost",
                            "impressions",
                        ]:
                            if col in fieldnames:
                                row[col] = "0"
                                patched_cols.add(col)
                        patched_rows += 1

                writer.writerow(row)

    return patched_rows, sorted(patched_cols)


def run(
    *,
    config_csv: Path,
    outdir: Path,
    tz_name: str,
    patch_date_local_str: str,
    run_mode: str,
    dry_run: bool,
) -> int:
    _ensure_cmd_available("bq")
    _ensure_cmd_available("gsutil")

    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz=tz)

    if run_mode == "auto":
        run_mode = _infer_run_mode_auto(now_local)
        if run_mode == "noop":
            # No-op run (for DST-safe dual cron schedule).
            outdir.mkdir(parents=True, exist_ok=True)
            summary = {
                "status": "NOOP",
                "reason": "Outside execution window for auto mode",
                "now_local": now_local.isoformat(),
                "timezone": tz_name,
            }
            (outdir / "forecast_self_heal_summary.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            return 0

    patch_date_local = _parse_date_local(patch_date_local_str, tz)
    patch_date = patch_date_local.isoformat()

    specs_all = _load_specs(config_csv)
    if run_mode == "cz_morning":
        specs = [s for s in specs_all if s.country == "cz"]
    elif run_mode == "noncz_afternoon":
        specs = [s for s in specs_all if s.country != "cz"]
    elif run_mode == "all":
        specs = list(specs_all)
    else:
        raise ValueError(f"Invalid run_mode: {run_mode}")

    outdir.mkdir(parents=True, exist_ok=True)
    checked_at_utc = datetime.now(tz=UTC)

    results: list[ResultRow] = []
    fixed = 0
    fails = 0
    skipped = 0

    # De-duplicate 14_* triggers (per run).
    trigger_14_configs: set[tuple[str, str]] = set()

    for spec in specs:
        print(f"[check] {spec.project_id} {spec.tenant}/{spec.country} ({run_mode})", file=sys.stderr)
        try:
            txns_by_ch, cost_by_ch = _final_prep_by_channel(
                project_id=spec.project_id,
                patch_date=patch_date,
                txns_table=spec.final_prep_txns_table,
                cost_table=spec.final_prep_cost_table,
            )

            fp_sessions = sum(float(v.get("sessions", 0.0)) for v in txns_by_ch.values())
            fp_rev = sum(float(v.get("revenue_db", 0.0)) for v in txns_by_ch.values())
            fp_trx = sum(float(v.get("transactions_db", 0.0)) for v in txns_by_ch.values())
            fp_cost = sum(float(v.get("cost", 0.0)) for v in cost_by_ch.values())

            bq13 = _bq_table_totals(project_id=spec.project_id, patch_date=patch_date, table_fq=spec.bq_table_13)
            bq13_sessions = float(bq13["sessions_sum"])
            bq13_rev = float(bq13["revenue_db_sum"])
            bq13_trx = float(bq13["transactions_db_sum"])
            bq13_cost = float(bq13["cost_sum"])

            # Determine missing snapshot (only when final_prep has non-zero but 13 is ~0).
            missing_metrics: list[str] = []
            if abs(fp_sessions) > EPS and abs(bq13_sessions) <= EPS:
                missing_metrics.append("sessions")
            if abs(fp_rev) > EPS and abs(bq13_rev) <= EPS:
                missing_metrics.append("revenue_db")
            if abs(fp_trx) > EPS and abs(bq13_trx) <= EPS:
                missing_metrics.append("transactions_db")
            if abs(fp_cost) > EPS and abs(bq13_cost) <= EPS:
                missing_metrics.append("cost")

            status = "PASS"
            reason = ""
            triggered_13 = "no"
            triggered_14 = "no"

            if abs(fp_sessions) <= EPS and abs(fp_rev) <= EPS and abs(fp_trx) <= EPS and abs(fp_cost) <= EPS:
                status = "SKIP"
                reason = "final_prep appears empty for patch_date"
                skipped += 1
            elif not missing_metrics:
                status = "PASS"
            else:
                # Patch needed
                if dry_run:
                    status = "FAIL"
                    reason = "dry_run: would patch"
                    fails += 1
                else:
                    base = f"{spec.project_id}__{spec.tenant}_{spec.country}__{patch_date.replace('-', '')}"
                    local_orig = outdir / f"{base}__orig.csv"
                    local_patched = outdir / f"{base}__patched.csv"

                    _gsutil_cp(spec.gcs_uri, str(local_orig))

                    # Determine channels present in CSV for patch_date
                    channels_target: list[str] = []
                    with local_orig.open("r", encoding="utf-8", newline="") as f_in:
                        r = csv.DictReader(f_in)
                        for row in r:
                            if (row.get("date") or "").strip() == patch_date:
                                ch = (row.get("channel") or "").strip()
                                if ch and ch not in channels_target:
                                    channels_target.append(ch)

                    if not channels_target:
                        status = "FAIL"
                        reason = "CSV has no rows for patch_date"
                        fails += 1
                    else:
                        actuals_map = _build_actuals_mapping(
                            channels_target=channels_target,
                            txns_by_channel=txns_by_ch,
                            cost_by_channel=cost_by_ch,
                        )
                        patched_rows, patched_cols = _patch_csv_file(
                            input_path=local_orig,
                            output_path=local_patched,
                            patch_date=patch_date,
                            actuals_by_channel=actuals_map,
                        )
                        if patched_rows <= 0:
                            status = "FAIL"
                            reason = "No rows patched"
                            fails += 1
                        elif not patched_cols:
                            status = "FAIL"
                            reason = "Rows found but no columns modified"
                            fails += 1
                        else:
                            # Upload + trigger DTS run for 13
                            _gsutil_cp(str(local_patched), spec.gcs_uri)
                            run_time_utc = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                            _trigger_transfer_run(
                                project_id=spec.project_id, config_resource_name=spec.dts_config_13, run_time_utc=run_time_utc
                            )
                            triggered_13 = "yes"

                            # Some unions (notably cerano-main) have 4h cadence; trigger 14 manually there.
                            if spec.project_id == "cerano-main" and spec.dts_config_14:
                                trigger_14_configs.add((spec.project_id, spec.dts_config_14))

                            status = "FIXED"
                            reason = f"patched_cols={';'.join(patched_cols)}"
                            fixed += 1

            results.append(
                ResultRow(
                    project_id=spec.project_id,
                    tenant=spec.tenant,
                    country=spec.country,
                    run_mode=run_mode,
                    patch_date_local=patch_date,
                    status=status,
                    reason=reason,
                    gcs_uri=spec.gcs_uri,
                    bq_table_13=spec.bq_table_13,
                    bq_table_14=spec.bq_table_14,
                    final_prep_sessions_sum=float(fp_sessions),
                    final_prep_revenue_db_sum=float(fp_rev),
                    final_prep_transactions_db_sum=float(fp_trx),
                    bq13_sessions_sum=float(bq13_sessions),
                    bq13_revenue_db_sum=float(bq13_rev),
                    bq13_transactions_db_sum=float(bq13_trx),
                    bq13_cost_sum=float(bq13_cost),
                    patched_metrics=";".join(missing_metrics),
                    triggered_13_run=triggered_13,
                    triggered_14_run=triggered_14,
                )
            )
        except Exception as exc:  # noqa: BLE001
            fails += 1
            results.append(
                ResultRow(
                    project_id=spec.project_id,
                    tenant=spec.tenant,
                    country=spec.country,
                    run_mode=run_mode,
                    patch_date_local=patch_date,
                    status="FAIL",
                    reason=f"exception: {exc}",
                    gcs_uri=spec.gcs_uri,
                    bq_table_13=spec.bq_table_13,
                    bq_table_14=spec.bq_table_14,
                    final_prep_sessions_sum=0.0,
                    final_prep_revenue_db_sum=0.0,
                    final_prep_transactions_db_sum=0.0,
                    bq13_sessions_sum=0.0,
                    bq13_revenue_db_sum=0.0,
                    bq13_transactions_db_sum=0.0,
                    bq13_cost_sum=0.0,
                    patched_metrics="",
                    triggered_13_run="no",
                    triggered_14_run="no",
                )
            )

    # Trigger 14 configs (if needed) after giving 13 runs a short head start.
    if trigger_14_configs and not dry_run:
        time.sleep(90)
        run_time_utc = datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        for project_id, cfg14 in sorted(trigger_14_configs):
            try:
                _trigger_transfer_run(project_id=project_id, config_resource_name=cfg14, run_time_utc=run_time_utc)
                # Mark rows as triggered for visibility (only if they have bq_table_14)
                for i, r in enumerate(results):
                    if r.project_id == project_id and r.status in {"PASS", "FIXED"} and r.bq_table_14:
                        results[i] = replace(r, triggered_14_run="yes")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                results.append(
                    ResultRow(
                        project_id=project_id,
                        tenant="",
                        country="",
                        run_mode=run_mode,
                        patch_date_local=patch_date,
                        status="FAIL",
                        reason=f"DTS 14 trigger failed: {exc}",
                        gcs_uri="",
                        bq_table_13="",
                        bq_table_14=cfg14,
                        final_prep_sessions_sum=0.0,
                        final_prep_revenue_db_sum=0.0,
                        final_prep_transactions_db_sum=0.0,
                        bq13_sessions_sum=0.0,
                        bq13_revenue_db_sum=0.0,
                        bq13_transactions_db_sum=0.0,
                        bq13_cost_sum=0.0,
                        patched_metrics="",
                        triggered_13_run="no",
                        triggered_14_run="no",
                    )
                )

    report_csv = outdir / "forecast_self_heal_report.csv"
    report_md = outdir / "forecast_self_heal_report.md"
    summary_json = outdir / "forecast_self_heal_summary.json"

    # Write CSV
    with report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "project_id",
                "tenant",
                "country",
                "run_mode",
                "patch_date_local",
                "status",
                "reason",
                "gcs_uri",
                "bq_table_13",
                "bq_table_14",
                "final_prep_sessions_sum",
                "final_prep_revenue_db_sum",
                "final_prep_transactions_db_sum",
                "bq13_sessions_sum",
                "bq13_revenue_db_sum",
                "bq13_transactions_db_sum",
                "bq13_cost_sum",
                "patched_metrics",
                "triggered_13_run",
                "triggered_14_run",
            ]
        )
        for r in results:
            w.writerow(
                [
                    r.project_id,
                    r.tenant,
                    r.country,
                    r.run_mode,
                    r.patch_date_local,
                    r.status,
                    r.reason,
                    r.gcs_uri,
                    r.bq_table_13,
                    r.bq_table_14,
                    f"{r.final_prep_sessions_sum:.6f}",
                    f"{r.final_prep_revenue_db_sum:.6f}",
                    f"{r.final_prep_transactions_db_sum:.6f}",
                    f"{r.bq13_sessions_sum:.6f}",
                    f"{r.bq13_revenue_db_sum:.6f}",
                    f"{r.bq13_transactions_db_sum:.6f}",
                    f"{r.bq13_cost_sum:.6f}",
                    r.patched_metrics,
                    r.triggered_13_run,
                    r.triggered_14_run,
                ]
            )

    # Write MD
    status = "✅ PASS" if fails == 0 else "🚨 FAIL"
    lines: list[str] = []
    lines.append("# Forecast Self-Heal Report")
    lines.append("")
    lines.append(f"- Run mode: `{run_mode}`")
    lines.append(f"- Patch date (local): `{patch_date}` (`{tz_name}`)")
    lines.append(f"- Checked at (UTC): `{checked_at_utc.replace(microsecond=0).isoformat().replace('+00:00','Z')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Status: **{status}**")
    lines.append(f"- Pipelines: {len(results)} total / {fixed} fixed / {fails} fail / {skipped} skip")
    lines.append("")
    if fails:
        lines.append("## Failures (first 20)")
        lines.append("")
        lines.append("| Project | Tenant | Country | Reason |")
        lines.append("|---|---|---|---|")
        for r in [x for x in results if x.status == 'FAIL'][:20]:
            lines.append(f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | {r.reason} |")
        lines.append("")
    if fixed:
        lines.append("## Fixes (first 20)")
        lines.append("")
        lines.append("| Project | Tenant | Country | Patched | DTS 13 | DTS 14 |")
        lines.append("|---|---|---|---|---:|---:|")
        for r in [x for x in results if x.status == 'FIXED'][:20]:
            lines.append(
                f"| `{r.project_id}` | `{r.tenant}` | `{r.country}` | `{r.patched_metrics}` | `{r.triggered_13_run}` | `{r.triggered_14_run}` |"
            )
        lines.append("")
    report_md.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    summary = {
        "status": "PASS" if fails == 0 else "FAIL",
        "run_mode": run_mode,
        "timezone": tz_name,
        "patch_date_local": patch_date,
        "pipelines_total": len(results),
        "pipelines_fixed": fixed,
        "pipelines_failed": fails,
        "pipelines_skipped": skipped,
    }
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Exit code: fail only if we still have failures after attempting fixes.
    return 0 if fails == 0 else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config-csv",
        default="scripts/guardrails/forecast_pipelines.csv",
        help="CSV with forecast pipeline inventory (default: scripts/guardrails/forecast_pipelines.csv).",
    )
    ap.add_argument("--outdir", default="forecast-self-heal-out")
    ap.add_argument("--timezone", default="Europe/Prague")
    ap.add_argument(
        "--patch-date-local",
        default="",
        help="Date to patch/check (YYYY-MM-DD, evaluated in --timezone). Default: yesterday in --timezone.",
    )
    ap.add_argument(
        "--run-mode",
        default="auto",
        choices=["auto", "cz_morning", "noncz_afternoon", "all"],
        help="Which set of pipelines to evaluate/fix.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Do not patch or trigger DTS runs.")
    args = ap.parse_args()

    tz = ZoneInfo(args.timezone)
    if args.patch_date_local.strip():
        patch_date_local_str = args.patch_date_local.strip()
    else:
        patch_date_local_str = (datetime.now(tz=tz).date() - timedelta(days=1)).isoformat()

    return run(
        config_csv=Path(args.config_csv),
        outdir=Path(args.outdir),
        tz_name=args.timezone,
        patch_date_local_str=patch_date_local_str,
        run_mode=args.run_mode,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())

