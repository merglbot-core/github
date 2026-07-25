#!/usr/bin/env python3
import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Forbidden, NotFound
from google.cloud import bigquery


@dataclass(frozen=True)
class ProducerSpec:
    project_id: str
    dataset_id: str
    table_pattern: str
    sla_local_time: time


@dataclass(frozen=True)
class TableCheckResult:
    project_id: str
    dataset_id: str
    table_pattern: str
    table_id: str
    table_type: str
    last_modified_utc: str
    last_modified_local: str
    sla_local: str
    status: str
    reason: str


def _parse_sla_local_time(value: str) -> time:
    value = value.strip()
    try:
        hour_str, minute_str = value.split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid sla_local_time: {value!r} (expected HH:MM)") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid sla_local_time: {value!r} (expected HH:MM 00:00..23:59)")
    return time(hour=hour, minute=minute)


def _parse_date_local(value: str, tz: ZoneInfo) -> date:
    value = value.strip()
    if not value:
        return datetime.now(tz=tz).date()
    try:
        return date.fromisoformat(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid date_local: {value!r} (expected YYYY-MM-DD)") from exc


def _like_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    regex = []
    for ch in pattern:
        if ch == "%":
            regex.append(".*")
        else:
            regex.append(re.escape(ch))
    return re.compile("^" + "".join(regex) + "$")


def _compile_ignore_regexes(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        pattern = pattern.strip()
        if not pattern:
            continue
        compiled.append(re.compile(pattern))
    return compiled


def _is_ignored(table_id: str, ignore_res: list[re.Pattern[str]]) -> bool:
    return any(r.search(table_id) for r in ignore_res)


def _load_specs(csv_path: Path) -> list[ProducerSpec]:
    specs: list[ProducerSpec] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(row for row in f if row.strip() and not row.lstrip().startswith("#"))
        required = {"project_id", "dataset_id", "table_pattern", "sla_local_time"}
        if set(reader.fieldnames or []) != required:
            raise ValueError(
                f"Invalid CSV header in {csv_path}. Expected exactly: {sorted(required)}; got: {reader.fieldnames}"
            )
        for row in reader:
            specs.append(
                ProducerSpec(
                    project_id=row["project_id"].strip(),
                    dataset_id=row["dataset_id"].strip(),
                    table_pattern=row["table_pattern"].strip(),
                    sla_local_time=_parse_sla_local_time(row["sla_local_time"]),
                )
            )
    if not specs:
        raise ValueError(f"No specs found in {csv_path}")
    return specs


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_csv(path: Path, rows: list[TableCheckResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "project_id",
                "dataset_id",
                "table_pattern",
                "table_id",
                "table_type",
                "last_modified_utc",
                "last_modified_local",
                "sla_local",
                "status",
                "reason",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.project_id,
                    r.dataset_id,
                    r.table_pattern,
                    r.table_id,
                    r.table_type,
                    r.last_modified_utc,
                    r.last_modified_local,
                    r.sla_local,
                    r.status,
                    r.reason,
                ]
            )


def _write_md(
    path: Path,
    *,
    date_local: date,
    tz_name: str,
    checked_at_utc: datetime,
    specs: list[ProducerSpec],
    rows: list[TableCheckResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    fails = [r for r in rows if r.status == "FAIL"]
    pass_count = total - len(fails)

    by_spec: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for s in specs:
        key = (s.project_id, s.dataset_id, s.table_pattern, s.sla_local_time.strftime("%H:%M"))
        by_spec[key] = {"total": 0, "fail": 0}

    for r in rows:
        key = (r.project_id, r.dataset_id, r.table_pattern, r.sla_local)
        by_spec.setdefault(key, {"total": 0, "fail": 0})
        by_spec[key]["total"] += 1
        if r.status == "FAIL":
            by_spec[key]["fail"] += 1

    lines: list[str] = []
    lines.append("# Readiness Guardrail Report")
    lines.append("")
    lines.append(f"- Date (local): `{date_local.isoformat()}` (`{tz_name}`)")
    lines.append(f"- Checked at (UTC): `{_iso(checked_at_utc)}`")
    lines.append("")
    lines.append("## Summary")
    status = "✅ PASS" if not fails else "🚨 FAIL"
    lines.append(f"- Status: **{status}**")
    lines.append(f"- Tables: {pass_count} PASS / {len(fails)} FAIL (total: {total})")
    lines.append("")
    lines.append("## Specs")
    lines.append("")
    lines.append("| Project | Dataset | Pattern | SLA (local) | Tables | Failures |")
    lines.append("|---|---|---|---:|---:|---:|")
    for (project_id, dataset_id, pattern, sla_local), stats in sorted(by_spec.items()):
        lines.append(
            f"| `{project_id}` | `{dataset_id}` | `{pattern}` | `{sla_local}` | {stats['total']} | {stats['fail']} |"
        )
    lines.append("")

    if fails:
        lines.append("## Failures (first 50)")
        lines.append("")
        lines.append("| Project | Dataset | Table | Last Modified (local) | SLA | Reason |")
        lines.append("|---|---|---|---:|---:|---|")
        for r in fails[:50]:
            lines.append(
                f"| `{r.project_id}` | `{r.dataset_id}` | `{r.table_id}` | `{r.last_modified_local}` | `{r.sla_local}` | `{r.reason}` |"
            )
        lines.append("")

    lines.append("## IAM (minimum)")
    lines.append("")
    lines.append("- GitHub Actions WIF service account must have `roles/bigquery.metadataViewer` on each target dataset.")
    lines.append("- This guardrail avoids BigQuery jobs (no `roles/bigquery.jobUser` expected).")
    lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_json_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(*, csv_path: Path, outdir: Path, tz_name: str, date_local_str: str, ignore_table_regexes: list[str]) -> int:
    tz = ZoneInfo(tz_name)
    date_local = _parse_date_local(date_local_str, tz)
    checked_at_utc = datetime.now(tz=UTC)
    sla_local_dt_cache: dict[ProducerSpec, datetime] = {}

    specs = _load_specs(csv_path)
    ignore_res = _compile_ignore_regexes(ignore_table_regexes)

    clients: dict[str, bigquery.Client] = {}
    rows: list[TableCheckResult] = []

    for spec in specs:
        if spec.project_id not in clients:
            clients[spec.project_id] = bigquery.Client(project=spec.project_id)
        client = clients[spec.project_id]

        if spec not in sla_local_dt_cache:
            sla_local_dt_cache[spec] = datetime.combine(date_local, spec.sla_local_time, tzinfo=tz)
        sla_local_dt = sla_local_dt_cache[spec]

        dataset_ref = bigquery.DatasetReference(spec.project_id, spec.dataset_id)
        pattern_re = _like_pattern_to_regex(spec.table_pattern)

        try:
            table_list = list(client.list_tables(dataset_ref))
        except (Forbidden, NotFound) as exc:
            rows.append(
                TableCheckResult(
                    project_id=spec.project_id,
                    dataset_id=spec.dataset_id,
                    table_pattern=spec.table_pattern,
                    table_id="",
                    table_type="",
                    last_modified_utc="",
                    last_modified_local="",
                    sla_local=spec.sla_local_time.strftime("%H:%M"),
                    status="FAIL",
                    reason=f"{exc.__class__.__name__}: {exc.message}",
                )
            )
            continue

        matches = [
            t
            for t in table_list
            if pattern_re.match(t.table_id or "") and not _is_ignored(t.table_id or "", ignore_res)
        ]

        if not matches:
            rows.append(
                TableCheckResult(
                    project_id=spec.project_id,
                    dataset_id=spec.dataset_id,
                    table_pattern=spec.table_pattern,
                    table_id="",
                    table_type="",
                    last_modified_utc="",
                    last_modified_local="",
                    sla_local=spec.sla_local_time.strftime("%H:%M"),
                    status="FAIL",
                    reason="no_tables_matched",
                )
            )
            continue

        for t in matches:
            table_ref = bigquery.TableReference(dataset_ref, t.table_id)
            try:
                table = client.get_table(table_ref)
                last_modified_utc = _as_utc(table.modified)
                last_modified_local = last_modified_utc.astimezone(tz)

                if last_modified_local.date() != date_local:
                    status = "FAIL"
                    reason = f"date_mismatch (got {last_modified_local.date().isoformat()})"
                elif last_modified_local > sla_local_dt:
                    status = "FAIL"
                    reason = "late_after_sla"
                else:
                    status = "PASS"
                    reason = ""

                rows.append(
                    TableCheckResult(
                        project_id=spec.project_id,
                        dataset_id=spec.dataset_id,
                        table_pattern=spec.table_pattern,
                        table_id=t.table_id,
                        table_type=getattr(table, "table_type", "") or "",
                        last_modified_utc=_iso(last_modified_utc),
                        last_modified_local=last_modified_local.replace(microsecond=0).isoformat(),
                        sla_local=spec.sla_local_time.strftime("%H:%M"),
                        status=status,
                        reason=reason,
                    )
                )
            except (Forbidden, NotFound) as exc:
                rows.append(
                    TableCheckResult(
                        project_id=spec.project_id,
                        dataset_id=spec.dataset_id,
                        table_pattern=spec.table_pattern,
                        table_id=t.table_id,
                        table_type="",
                        last_modified_utc="",
                        last_modified_local="",
                        sla_local=spec.sla_local_time.strftime("%H:%M"),
                        status="FAIL",
                        reason=f"{exc.__class__.__name__}: {exc.message}",
                    )
                )

    report_csv = outdir / "readiness_guardrail_report.csv"
    report_md = outdir / "readiness_guardrail_report.md"
    summary_json = outdir / "readiness_guardrail_summary.json"

    _write_csv(report_csv, rows)
    _write_md(
        report_md,
        date_local=date_local,
        tz_name=tz_name,
        checked_at_utc=checked_at_utc,
        specs=specs,
        rows=rows,
    )

    failures = [r for r in rows if r.status == "FAIL"]
    summary = {
        "date_local": date_local.isoformat(),
        "timezone": tz_name,
        "checked_at_utc": _iso(checked_at_utc),
        "tables_total": len(rows),
        "tables_fail": len(failures),
        "tables_pass": len(rows) - len(failures),
        "report_csv": str(report_csv),
        "report_md": str(report_md),
    }
    _write_json_summary(summary_json, summary)

    print(f"CSV: {report_csv}")
    print(f"MD:  {report_md}")
    print(f"JSON:{summary_json}")
    print(json.dumps(summary, indent=2, sort_keys=True))

    return 0 if not failures else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily readiness guardrail (BigQuery final tables freshness).")
    parser.add_argument(
        "--config-csv",
        default="scripts/guardrails/final_producers.csv",
        help="CSV specs file (project_id,dataset_id,table_pattern,sla_local_time)",
    )
    parser.add_argument("--outdir", default="guardrail-out", help="Output directory for reports/artifacts")
    parser.add_argument("--timezone", default="Europe/Prague", help="IANA timezone name for local SLA evaluation")
    parser.add_argument(
        "--date-local",
        default="",
        help="Local date to check in YYYY-MM-DD (defaults to today in --timezone)",
    )
    parser.add_argument(
        "--ignore-table-regex",
        action="append",
        default=None,
        help="Regex for table names to ignore (repeatable). Default ignores: .*_test$",
    )
    args = parser.parse_args()

    # Apply default ignore patterns if none provided (avoids argparse append gotcha)
    ignore_table_regexes = args.ignore_table_regex if args.ignore_table_regex else [".*_test$"]

    try:
        return run(
            csv_path=Path(args.config_csv),
            outdir=Path(args.outdir),
            tz_name=args.timezone,
            date_local_str=args.date_local,
            ignore_table_regexes=ignore_table_regexes,
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
