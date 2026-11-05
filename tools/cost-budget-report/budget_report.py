"""Read one existing budget snapshot; never call an API or mutate a budget."""
import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp requires a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires a timezone")
    return parsed


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("invalid amount")
    # Bound parsing and fixed-point output, including zero with a huge exponent.
    text = str(value)
    if len(text) > 128:
        raise ValueError("amount representation too long")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("invalid amount") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("invalid amount")
    exponent = number.as_tuple().exponent
    if not -30 <= exponent <= 30 or not -30 <= number.adjusted() <= 30:
        raise ValueError("amount outside supported precision or range")
    return format(number, "f")


def cell(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(
        ">", "&gt;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def render(raw, provenance, *, now, max_age=timedelta(hours=24)):
    """Same Markdown section for the monthly report and autonomy digest.

    Provenance must describe the original read, not the time this file is rendered.
    The caller passes a freshness limit explicitly; this does not change schedules.
    """
    title = "## Rozpočty GitHubu — pozorování API"
    try:
        if now.tzinfo is None or max_age.total_seconds() <= 0:
            raise ValueError("invalid clock or freshness limit")
        if type(provenance["schema_version"]) is not int or provenance["schema_version"] != 1:
            raise ValueError("unsupported provenance")
        if provenance["source_sha256"] != hashlib.sha256(raw).hexdigest():
            raise ValueError("snapshot hash mismatch")
        if provenance.get("complete") is not True:
            raise ValueError("incomplete snapshot")
        endpoint = provenance["source_endpoint"]
        if not isinstance(endpoint, str) or not endpoint.startswith("https://api.github.com/"):
            raise ValueError("missing API provenance")
        url = urlsplit(endpoint)
        if url.hostname != "api.github.com" or url.username or url.password or url.query or url.fragment:
            raise ValueError("unsafe API provenance")
        observed = timestamp(provenance["observed_at"])
        age = now - observed
        if age.total_seconds() < 0 or age > max_age:
            raise ValueError("future or stale snapshot")
        snapshot = json.loads(raw)
        rows = snapshot["rows"]
        if not isinstance(rows, list):
            raise ValueError("invalid rows")
        rendered, identities = [], set()
        for row in rows:
            if not isinstance(row["product"], str) or not row["product"]:
                raise ValueError("missing product")
            identity = (row["budget_scope"], row["id"])
            if identity in identities or not all(isinstance(v, str) and v for v in identity):
                raise ValueError("duplicate or missing budget identity")
            identities.add(identity)
            stop = row["prevent_further_usage"]
            if type(stop) is not bool:
                raise ValueError("unknown hard/soft state")
            alert = row.get("alert_enabled")
            alert_text = "ano" if alert is True else "ne" if alert is False else "DATA_GAP"
            rendered.append("| " + " | ".join(cell(v) for v in (
                row["budget_scope"], row["product"], amount(row["budget_amount"]),
                amount(row["consumed_amount"]), "ano" if stop else "ne", alert_text)) + " |")
        period = provenance.get("period")
        if period is None:
            period_text = "DATA_GAP — API období nedoloženo; měsíc nelze odvodit z data čtení."
        else:
            start, end = timestamp(period["start"]), timestamp(period["end"])
            if end <= start:
                raise ValueError("invalid period")
            zone = ZoneInfo("Europe/Prague")
            period_text = f"{start.astimezone(zone).isoformat()} až {end.astimezone(zone).isoformat()} (Praha)"
        stamp = observed.astimezone(ZoneInfo("Europe/Prague")).isoformat()
        return "\n".join([title, "", f"Pozorováno: {stamp} (Praha).", f"Období: {period_text}",
            "", "| Scope | Produkt | Budget USD | API consumed USD | Tvrdý stop | Upozornění doloženo |",
            "|---|---|---:|---:|---|---|", *rendered,
            "" if rows else "API v úplném snapshotu nevrátilo žádné budgety.",
            "", "Částky jsou pozorování budget API, nikoli uzavřené vyúčtování ani důkaz čisté úspory.",
            "Nulové consumed_amount neprokazuje nulové náklady. Existující ochrany se nemění.",
            f"Zdroj: {cell(endpoint)}; SHA256: `{provenance['source_sha256']}`.", ""])
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError, RecursionError):
        # Never include malformed API values or external error text in the digest.
        return title + "\n\nDATA_GAP — chybějící, neúplný, zastaralý nebo neověřitelný snapshot.\n"


def render_paragraphs(raw, provenance, *, now, max_age=timedelta(hours=24)):
    """Plain text for HTML/PDF consumers which escape paragraphs themselves."""
    lines = render(raw, provenance, now=now, max_age=max_age).splitlines()
    result = []
    labels = ('Scope', 'Produkt', 'Budget USD', 'API consumed USD', 'Tvrdý stop', 'Upozornění doloženo')
    header_seen = False
    for line in lines:
        if not header_seen and line.startswith('| Scope |'):
            header_seen = True
            continue
        if not line or line.startswith('## ') or line.startswith('|---'):
            continue
        if line.startswith('| '):
            cells = [html.unescape(c.strip()) for c in line.strip('|').split('|')]
            result.append('; '.join(f'{label}: {value}' for label, value in zip(labels, cells)))
        else:
            result.append(html.unescape(line.replace('`', '')))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--max-age-hours", required=True, type=float)
    args = parser.parse_args()
    try:
        raw = Path(args.snapshot).read_bytes()
        provenance = json.loads(Path(args.provenance).read_bytes())
        text = render(raw, provenance, now=datetime.now(timezone.utc),
                      max_age=timedelta(hours=args.max_age_hours))
    except (OSError, ValueError, OverflowError):
        text = "## Rozpočty GitHubu — pozorování API\n\nDATA_GAP — snapshot nelze načíst.\n"
    print(text)


if __name__ == "__main__":
    main()
