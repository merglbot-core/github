from __future__ import annotations


def _to_int(v: object) -> int:
    try:
        if v is None:
            return 0
        return int(float(str(v).strip() or "0"))
    except Exception:
        return 0


def _to_float(v: object) -> float:
    try:
        if v is None:
            return 0.0
        return float(str(v).strip() or "0")
    except Exception:
        return 0.0


def slack_escape(s: str) -> str:
    """Escape Slack link primitives, stabilize mrkdwn code spans, and break @mentions."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "ˋ")  # prevent breaking code spans / blocks
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("@", "@\u200b")  # zero-width space breaks mentions
    )


def render_table_section_mrkdwn(
    *,
    tenant: str,
    row_items: list[tuple[str, list[str], str]],
    cell_w: int = 7,
    max_chars: int = 2900,
) -> str:
    """Render a tenant section with a fixed-width table.

    Slack Block Kit limits `section.text` to 3000 chars. We clamp to `max_chars`
    to avoid hard rejects while keeping the payload readable.
    """
    label_w = max((len(lbl) for lbl, _, _ in row_items), default=10)

    def _cell(v: str) -> str:
        v = (v or "").strip()
        if len(v) > cell_w:
            v = v[:cell_w]
        return v.center(cell_w)

    table_lines: list[str] = []
    for i, (lbl, cells, suffix) in enumerate(row_items):
        if i == 1:
            sep = ("-" * label_w) + "-+-" + "-+-".join(["-" * cell_w for _ in range(6)])
            table_lines.append(sep)

        padded_cells = (cells + [""] * 6)[:6]
        line = f"{lbl.ljust(label_w)} | " + " | ".join(_cell(c) for c in padded_cells)
        if suffix:
            line += suffix
        table_lines.append(line.rstrip())

    header = f"🏢 `{slack_escape(tenant)}`"
    trunc_line = "... (truncated; see artifact)"

    def _join(body: list[str], *, truncated: bool) -> str:
        lines = [header, "```", *body]
        if truncated:
            lines.append(trunc_line)
        lines.append("```")
        return "\n".join(lines).rstrip()

    full = _join(table_lines, truncated=False)
    if len(full) <= max_chars:
        return full

    kept: list[str] = []
    for line in table_lines:
        candidate = kept + [line]
        if len(_join(candidate, truncated=True)) > max_chars:
            break
        kept.append(line)

    if not kept:
        minimal = f"{header}\n{trunc_line}"
        return minimal[:max_chars].rstrip()

    return _join(kept, truncated=True)

