#!/usr/bin/env python3
import argparse
import csv
import io
import json
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Forbidden, NotFound
from google.cloud import storage


@dataclass(frozen=True)
class ProducerSpec:
    group_id: str
    mode: str
    tenant: str
    country: str
    object_ref: str
    sla_local_time: time


@dataclass(frozen=True)
class ObjectResult:
    group_id: str
    mode: str
    tenant: str
    country: str
    object_ref: str
    date_start: str
    sla_local: str
    status: str
    reason: str
    asof_generation: str
    asof_updated_utc: str
    asof_updated_local: str
    current_generation: str
    current_updated_utc: str
    current_updated_local: str
    row_count: int
    sum_spend: float


@dataclass(frozen=True)
class GroupResult:
    group_id: str
    mode: str
    country: str
    date_start: str
    sla_local: str
    status: str
    reason: str
    members_total: int
    members_ok: int
    members_missing_generation: int
    sum_spend: float
    row_count: int


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


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_object_ref(value: str) -> tuple[str, str]:
    value = value.strip()
    if not value.startswith("gs://"):
        raise ValueError(f"Invalid object_ref: {value!r} (expected gs://bucket/object)")
    rest = value[len("gs://") :]
    if "/" not in rest:
        raise ValueError(f"Invalid object_ref: {value!r} (expected gs://bucket/object)")
    bucket, obj = rest.split("/", 1)
    if not bucket or not obj:
        raise ValueError(f"Invalid object_ref: {value!r} (expected gs://bucket/object)")
    return bucket, obj


def _load_specs(csv_path: Path) -> list[ProducerSpec]:
    specs: list[ProducerSpec] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(row for row in f if row.strip() and not row.lstrip().startswith("#"))
        required = {"group_id", "mode", "tenant", "country", "object_ref", "sla_local_time"}
        if set(reader.fieldnames or []) != required:
            raise ValueError(
                f"Invalid CSV header in {csv_path}. Expected exactly: {sorted(required)}; got: {reader.fieldnames}"
            )
        for row in reader:
            mode = row["mode"].strip()
            if mode not in {"merged", "separate"}:
                raise ValueError(f"Invalid mode: {mode!r} (expected merged|separate)")
            specs.append(
                ProducerSpec(
                    group_id=row["group_id"].strip(),
                    mode=mode,
                    tenant=row["tenant"].strip(),
                    country=row["country"].strip(),
                    object_ref=row["object_ref"].strip(),
                    sla_local_time=_parse_sla_local_time(row["sla_local_time"]),
                )
            )
    if not specs:
        raise ValueError(f"No specs found in {csv_path}")
    return specs


def _csv_sum_spend_for_date(csv_text: str, *, date_start: str) -> tuple[int, float]:
    # The legacy export is a CSV with date_start + spend columns.
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return 0, 0.0
    if "date_start" not in reader.fieldnames or "spend" not in reader.fieldnames:
        raise ValueError(f"Missing required columns: date_start/spend (got {reader.fieldnames})")

    row_count = 0
    sum_spend = 0.0
    for row in reader:
        if str(row.get("date_start") or "").strip() != date_start:
            continue
        row_count += 1
        raw = str(row.get("spend") or "").strip()
        try:
            sum_spend += float(raw) if raw else 0.0
        except (ValueError, TypeError):
            # Treat parse errors as 0 for robustness; surface a warning for triage.
            print(
                f"WARNING: Could not parse spend value {raw!r} for date_start={date_start}",
                file=sys.stderr,
            )
    return row_count, sum_spend


def _pick_generation_asof(
    blobs: list[storage.Blob],
    *,
    sla_utc: datetime,
) -> tuple[storage.Blob | None, storage.Blob | None]:
    # Returns (as_of_blob, current_blob) where current_blob is the newest by updated time.
    if not blobs:
        return None, None
    def updated_ts(b: storage.Blob) -> datetime:
        # Prefer time_created for generation ordering; updated can change on metadata updates.
        ts = getattr(b, "time_created", None) or getattr(b, "updated", None)
        if ts is None:
            return datetime.fromtimestamp(0, tz=UTC)
        return _as_utc(ts)

    blobs_sorted = sorted(blobs, key=updated_ts)
    current_blob = blobs_sorted[-1]
    asof_candidates = [b for b in blobs_sorted if updated_ts(b) <= sla_utc]
    return (asof_candidates[-1] if asof_candidates else None), current_blob


def _run_object_check(
    client: storage.Client,
    *,
    spec: ProducerSpec,
    date_start: date,
    tz: ZoneInfo,
    sla_local_dt: datetime,
) -> ObjectResult:
    bucket_name, object_name = _parse_object_ref(spec.object_ref)
    sla_utc = sla_local_dt.astimezone(UTC)
    date_start_str = date_start.isoformat()

    try:
        blobs = [
            b
            for b in client.list_blobs(bucket_or_name=bucket_name, prefix=object_name, versions=True)
            if b.name == object_name
        ]
    except (Forbidden, NotFound) as exc:
        # Avoid leaking internal details into Slack by keeping the reason coarse-grained.
        reason = exc.__class__.__name__
        return ObjectResult(
            group_id=spec.group_id,
            mode=spec.mode,
            tenant=spec.tenant,
            country=spec.country,
            object_ref=spec.object_ref,
            date_start=date_start_str,
            sla_local=sla_local_dt.strftime("%H:%M"),
            status="FAIL",
            reason=reason,
            asof_generation="",
            asof_updated_utc="",
            asof_updated_local="",
            current_generation="",
            current_updated_utc="",
            current_updated_local="",
            row_count=0,
            sum_spend=0.0,
        )

    asof_blob, current_blob = _pick_generation_asof(blobs, sla_utc=sla_utc)
    if current_blob is None:
        return ObjectResult(
            group_id=spec.group_id,
            mode=spec.mode,
            tenant=spec.tenant,
            country=spec.country,
            object_ref=spec.object_ref,
            date_start=date_start_str,
            sla_local=sla_local_dt.strftime("%H:%M"),
            status="FAIL",
            reason="object_not_found",
            asof_generation="",
            asof_updated_utc="",
            asof_updated_local="",
            current_generation="",
            current_updated_utc="",
            current_updated_local="",
            row_count=0,
            sum_spend=0.0,
        )

    def fmt_blob(b: storage.Blob | None) -> tuple[str, str, str]:
        if b is None:
            return "", "", ""
        updated = getattr(b, "updated", None) or getattr(b, "time_created", None)
        if updated is None:
            updated_utc = ""
            updated_local = ""
        else:
            updated_utc = _iso(_as_utc(updated))
            updated_local = _as_utc(updated).astimezone(tz).replace(microsecond=0).isoformat()
        return str(getattr(b, "generation", "") or ""), updated_utc, updated_local

    current_gen, current_updated_utc, current_updated_local = fmt_blob(current_blob)

    if asof_blob is None:
        return ObjectResult(
            group_id=spec.group_id,
            mode=spec.mode,
            tenant=spec.tenant,
            country=spec.country,
            object_ref=spec.object_ref,
            date_start=date_start_str,
            sla_local=sla_local_dt.strftime("%H:%M"),
            status="FAIL",
            reason="no_generation_before_sla",
            asof_generation="",
            asof_updated_utc="",
            asof_updated_local="",
            current_generation=current_gen,
            current_updated_utc=current_updated_utc,
            current_updated_local=current_updated_local,
            row_count=0,
            sum_spend=0.0,
        )

    asof_gen, asof_updated_utc, asof_updated_local = fmt_blob(asof_blob)

    # Download that exact generation and compute spend for date_start.
    blob = client.bucket(bucket_name).blob(object_name, generation=int(asof_gen))
    try:
        csv_text = blob.download_as_text(encoding="utf-8")
        row_count, sum_spend = _csv_sum_spend_for_date(csv_text, date_start=date_start_str)
    except Exception as exc:  # noqa: BLE001
        return ObjectResult(
            group_id=spec.group_id,
            mode=spec.mode,
            tenant=spec.tenant,
            country=spec.country,
            object_ref=spec.object_ref,
            date_start=date_start_str,
            sla_local=sla_local_dt.strftime("%H:%M"),
            status="FAIL",
            reason=f"download_or_parse_error:{exc.__class__.__name__}",
            asof_generation=asof_gen,
            asof_updated_utc=asof_updated_utc,
            asof_updated_local=asof_updated_local,
            current_generation=current_gen,
            current_updated_utc=current_updated_utc,
            current_updated_local=current_updated_local,
            row_count=0,
            sum_spend=0.0,
        )

    status = "PASS" if sum_spend > 0.0 else "FAIL"
    reason = "" if status == "PASS" else "sum_spend_not_positive"

    return ObjectResult(
        group_id=spec.group_id,
        mode=spec.mode,
        tenant=spec.tenant,
        country=spec.country,
        object_ref=spec.object_ref,
        date_start=date_start_str,
        sla_local=sla_local_dt.strftime("%H:%M"),
        status=status,
        reason=reason,
        asof_generation=asof_gen,
        asof_updated_utc=asof_updated_utc,
        asof_updated_local=asof_updated_local,
        current_generation=current_gen,
        current_updated_utc=current_updated_utc,
        current_updated_local=current_updated_local,
        row_count=row_count,
        sum_spend=sum_spend,
    )


def _write_objects_csv(path: Path, rows: list[ObjectResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "group_id",
                "mode",
                "tenant",
                "country",
                "object_ref",
                "date_start",
                "sla_local",
                "status",
                "reason",
                "asof_generation",
                "asof_updated_utc",
                "asof_updated_local",
                "current_generation",
                "current_updated_utc",
                "current_updated_local",
                "row_count",
                "sum_spend",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.group_id,
                    r.mode,
                    r.tenant,
                    r.country,
                    r.object_ref,
                    r.date_start,
                    r.sla_local,
                    r.status,
                    r.reason,
                    r.asof_generation,
                    r.asof_updated_utc,
                    r.asof_updated_local,
                    r.current_generation,
                    r.current_updated_utc,
                    r.current_updated_local,
                    str(r.row_count),
                    f"{r.sum_spend:.6f}",
                ]
            )


def _write_groups_csv(path: Path, rows: list[GroupResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "group_id",
                "mode",
                "country",
                "date_start",
                "sla_local",
                "status",
                "reason",
                "members_total",
                "members_ok",
                "members_missing_generation",
                "row_count",
                "sum_spend",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.group_id,
                    r.mode,
                    r.country,
                    r.date_start,
                    r.sla_local,
                    r.status,
                    r.reason,
                    str(r.members_total),
                    str(r.members_ok),
                    str(r.members_missing_generation),
                    str(r.row_count),
                    f"{r.sum_spend:.6f}",
                ]
            )


def _write_md(
    path: Path,
    *,
    date_local: date,
    tz_name: str,
    checked_at_utc: datetime,
    objects: list[ObjectResult],
    groups: list[GroupResult],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fails = [g for g in groups if g.status == "FAIL"]
    status = "✅ PASS" if not fails else "🚨 FAIL"

    lines: list[str] = []
    lines.append("# FB Cost Readiness Guardrail Report")
    lines.append("")
    lines.append(f"- Date (local): `{date_local.isoformat()}` (`{tz_name}`)")
    lines.append(f"- Checked at (UTC): `{_iso(checked_at_utc)}`")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Status: **{status}**")
    lines.append(f"- Groups: {len(groups) - len(fails)} PASS / {len(fails)} FAIL (total: {len(groups)})")
    lines.append("")

    lines.append("## Failures (first 50)")
    lines.append("")
    lines.append("| Group | SLA (local) | Reason | Members | Sum(spend) |")
    lines.append("|---|---:|---|---:|---:|")
    for g in fails[:50]:
        lines.append(
            f"| `{g.group_id}` | `{g.sla_local}` | `{g.reason}` | {g.members_total} | {g.sum_spend:.2f} |"
        )
    lines.append("")

    lines.append("## IAM (minimum)")
    lines.append("")
    lines.append("- GitHub Actions WIF service account must have `roles/storage.objectViewer` for each target bucket.")
    lines.append("- This guardrail requires object versioning enabled to evaluate \"as-of SLA\" generations.")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("- Definition: final costs ready iff `SUM(spend) > 0` for `date_start = date_local - 1`.")
    lines.append("- If a brand truly has zero spend, this guardrail will currently FAIL by definition.")
    lines.append("")

    # Quick object-level stats (counts only)
    obj_fail = sum(1 for o in objects if o.status == "FAIL")
    lines.append("## Object checks")
    lines.append("")
    lines.append(f"- Objects: {len(objects) - obj_fail} PASS / {obj_fail} FAIL (total: {len(objects)})")
    lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_json_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run(*, csv_path: Path, outdir: Path, tz_name: str, date_local_str: str, mode: str) -> int:
    tz = ZoneInfo(tz_name)
    date_local = _parse_date_local(date_local_str, tz)
    checked_at_utc = datetime.now(tz=UTC)
    date_start = date_local - date.resolution

    if mode and mode not in {"merged", "separate"}:
        raise ValueError(f"Invalid --mode: {mode!r} (expected merged|separate)")

    specs = _load_specs(csv_path)
    if mode:
        specs = [s for s in specs if s.mode == mode]
    if not specs:
        raise ValueError("No producer specs matched the selected mode/config.")

    client = storage.Client()

    objects: list[ObjectResult] = []
    for spec in specs:
        group_id = spec.group_id.strip() or spec.tenant
        sla_local_dt = datetime.combine(date_local, spec.sla_local_time, tzinfo=tz)
        objects.append(
            _run_object_check(
                client,
                spec=ProducerSpec(
                    group_id=group_id,
                    mode=spec.mode,
                    tenant=spec.tenant,
                    country=spec.country,
                    object_ref=spec.object_ref,
                    sla_local_time=spec.sla_local_time,
                ),
                date_start=date_start,
                tz=tz,
                sla_local_dt=sla_local_dt,
            )
        )

    # Group aggregation (for merged mode).
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for o in objects:
        key = (o.group_id, o.mode)
        groups.setdefault(
            key,
            {
                "group_id": o.group_id,
                "mode": o.mode,
                "country": o.country,
                "date_start": o.date_start,
                "sla_local": o.sla_local,
                "members_total": 0,
                "members_ok": 0,
                "members_missing_generation": 0,
                "sum_spend": 0.0,
                "row_count": 0,
                "any_generation": False,
            },
        )
        g = groups[key]
        g["members_total"] += 1
        if o.reason == "no_generation_before_sla":
            g["members_missing_generation"] += 1
        if o.asof_generation:
            g["any_generation"] = True
        if o.status == "PASS":
            g["members_ok"] += 1
        g["sum_spend"] += o.sum_spend
        g["row_count"] += o.row_count

    group_rows: list[GroupResult] = []
    for g in groups.values():
        if g["sum_spend"] > 0.0:
            status = "PASS"
            reason = ""
        else:
            status = "FAIL"
            reason = "sum_spend_not_positive" if g["any_generation"] else "no_generation_before_sla"

        group_rows.append(
            GroupResult(
                group_id=str(g["group_id"]),
                mode=str(g["mode"]),
                country=str(g["country"]),
                date_start=str(g["date_start"]),
                sla_local=str(g["sla_local"]),
                status=status,
                reason=reason,
                members_total=int(g["members_total"]),
                members_ok=int(g["members_ok"]),
                members_missing_generation=int(g["members_missing_generation"]),
                sum_spend=float(g["sum_spend"]),
                row_count=int(g["row_count"]),
            )
        )

    objects_csv = outdir / "fb_cost_guardrail_objects.csv"
    groups_csv = outdir / "fb_cost_guardrail_groups.csv"
    report_md = outdir / "fb_cost_guardrail_report.md"
    summary_json = outdir / "fb_cost_guardrail_summary.json"

    _write_objects_csv(objects_csv, objects)
    _write_groups_csv(groups_csv, sorted(group_rows, key=lambda r: (r.mode, r.group_id)))
    _write_md(
        report_md,
        date_local=date_local,
        tz_name=tz_name,
        checked_at_utc=checked_at_utc,
        objects=objects,
        groups=group_rows,
    )

    failures = [g for g in group_rows if g.status == "FAIL"]
    summary = {
        "date_local": date_local.isoformat(),
        "timezone": tz_name,
        "checked_at_utc": _iso(checked_at_utc),
        "groups_total": len(group_rows),
        "groups_fail": len(failures),
        "groups_pass": len(group_rows) - len(failures),
        "report_md": str(report_md),
        "groups_csv": str(groups_csv),
        "objects_csv": str(objects_csv),
    }
    _write_json_summary(summary_json, summary)

    print(f"MD:   {report_md}")
    print(f"CSV:  {groups_csv}")
    print(f"OBJ:  {objects_csv}")
    print(f"JSON: {summary_json}")
    print(json.dumps(summary, indent=2, sort_keys=True))

    return 0 if not failures else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="FB final-costs readiness guardrail (GCS generations as-of SLA).")
    parser.add_argument(
        "--config-csv",
        default="scripts/guardrails/fb_cost_producers.csv",
        help="CSV specs file (group_id,mode,tenant,country,object_ref,sla_local_time)",
    )
    parser.add_argument("--outdir", default="guardrail-out", help="Output directory for reports/artifacts")
    parser.add_argument("--timezone", default="Europe/Prague", help="IANA timezone name for local SLA evaluation")
    parser.add_argument(
        "--date-local",
        default="",
        help="Local date to check in YYYY-MM-DD (defaults to today in --timezone)",
    )
    parser.add_argument(
        "--mode",
        default="merged",
        help="Filter config rows by mode (merged|separate). Default: merged.",
    )
    args = parser.parse_args()

    try:
        return run(
            csv_path=Path(args.config_csv),
            outdir=Path(args.outdir),
            tz_name=args.timezone,
            date_local_str=args.date_local,
            mode=args.mode.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
