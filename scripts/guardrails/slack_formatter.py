from __future__ import annotations

import unicodedata


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


def _char_display_width(ch: str) -> int:
    """
    Approximate the visual width of a character in Slack code blocks.

    Slack renders code blocks in a monospace font, but emoji/wide glyphs still
    occupy ~2 columns. We use a small heuristic that is good enough for the
    status icons used in these reports.
    """

    if not ch:
        return 0

    codepoint = ord(ch)
    if codepoint in (0x200B, 0x200D, 0xFE0E, 0xFE0F):  # ZWSP, ZWJ, VS15/VS16
        return 0
    if unicodedata.combining(ch):
        return 0
    if codepoint < 32 or (0x7F <= codepoint < 0xA0):  # control chars
        return 0

    # Emoji-like ranges commonly used in these Slack tables.
    if (
        0x1F000 <= codepoint <= 0x1FAFF  # emoji blocks
        or 0x2600 <= codepoint <= 0x26FF  # misc symbols (e.g., ⚠)
        or 0x2700 <= codepoint <= 0x27BF  # dingbats (e.g., ✅, ❌)
    ):
        return 2

    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def _display_width(s: str) -> int:
    return sum(_char_display_width(ch) for ch in (s or ""))


def _truncate_to_width(s: str, max_w: int) -> str:
    if max_w <= 0:
        return ""
    w = 0
    out: list[str] = []
    for ch in (s or ""):
        ch_w = _char_display_width(ch)
        if w + ch_w > max_w:
            break
        out.append(ch)
        w += ch_w
    return "".join(out)


def _ljust_display(s: str, width: int) -> str:
    s = s or ""
    pad = max(width - _display_width(s), 0)
    return s + (" " * pad)


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
    label_w = max((_display_width(lbl) for lbl, _, _ in row_items), default=10)

    def _cell(v: str) -> str:
        v = (v or "").strip()
        v = _truncate_to_width(v, cell_w)
        v_w = _display_width(v)
        pad_total = max(cell_w - v_w, 0)
        pad_left = pad_total // 2
        pad_right = pad_total - pad_left
        return (" " * pad_left) + v + (" " * pad_right)

    table_lines: list[str] = []
    for i, (lbl, cells, suffix) in enumerate(row_items):
        if i == 1:
            sep = ("-" * label_w) + "-+-" + "-+-".join(["-" * cell_w for _ in range(6)])
            table_lines.append(sep)

        padded_cells = (cells + [""] * 6)[:6]
        line = f"{_ljust_display(lbl, label_w)} | " + " | ".join(_cell(c) for c in padded_cells)
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
