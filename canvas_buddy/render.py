"""Turn Change objects into messages for each channel."""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .collectors import KIND_LABELS
from .diff import FIELD_LABELS, Change

TELEGRAM_LIMIT = 4000  # real limit is 4096; leave headroom
DISCORD_LIMIT = 1900


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - fall back rather than crash a run
        return ZoneInfo("UTC")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_dt(value: str | None, tz_name: str = "Europe/Dublin") -> str:
    dt = parse_iso(value)
    if dt is None:
        return str(value) if value else "(none)"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_tz(tz_name)).strftime("%a %d %b %Y, %H:%M")


def _truncate(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _icon(change: Change) -> str:
    if change.change_type == "new":
        return {"announcement": "📢", "assignment": "📝", "discussion": "💬",
                "discussion_entry": "💬", "file": "📎", "page": "📄"}.get(
            change.record.kind, "🆕"
        )
    return "✏️"


def render_change_text(change: Change, tz_name: str) -> str:
    """Plain-text rendering of one change; channel formatters wrap this."""
    rec = change.record
    kind = KIND_LABELS.get(rec.kind, rec.kind)
    lines: list[str] = []
    verb = "New" if change.change_type == "new" else "Updated"
    lines.append(f"{_icon(change)} {rec.course_code} — {verb} {kind.lower()}")
    lines.append(rec.title)

    if change.change_type == "new":
        fields = change.detail.get("fields", {})
        if rec.kind == "assignment":
            due = fields.get("due_at")
            lines.append(f"Due: {fmt_dt(due, tz_name) if due else 'no due date set'}")
            if fields.get("points_possible"):
                lines.append(f"Marks: {fields['points_possible']}")
            body = _truncate(fields.get("description", ""), 400)
        elif rec.kind in {"announcement", "discussion", "discussion_entry"}:
            author = rec.meta.get("author")
            if author:
                lines.append(f"From: {author}")
            body = _truncate(fields.get("message", ""), 500)
        elif rec.kind == "syllabus":
            body = _truncate(fields.get("body", ""), 300)
        else:
            body = ""
        if body:
            lines.append("")
            lines.append(body)
    else:
        for name, info in change.detail.get("changes", {}).items():
            label = FIELD_LABELS.get(name, name)
            if info.get("type") == "value":
                old, new = info.get("old", ""), info.get("new", "")
                if name in {"due_at", "unlock_at", "lock_at"}:
                    old = fmt_dt(old, tz_name) if old else "(none)"
                    new = fmt_dt(new, tz_name) if new else "(none)"
                lines.append(f"{label}: {old or '(none)'} → {new or '(none)'}")
            else:
                lines.append(f"{label} changed:")
                lines.append(_truncate(info.get("diff", ""), 700))

    if rec.url:
        lines.append("")
        lines.append(rec.url)
    return "\n".join(lines)


def render_telegram(changes: list[Change], tz_name: str, header: str | None = None) -> list[str]:
    """Telegram HTML messages, split to stay under the length limit."""
    blocks: list[str] = []
    if header:
        blocks.append(f"<b>{html.escape(header)}</b>")

    for change in changes:
        rec = change.record
        kind = KIND_LABELS.get(rec.kind, rec.kind)
        verb = "New" if change.change_type == "new" else "Updated"
        head = (
            f"{_icon(change)} <b>{html.escape(rec.course_code)}</b> — "
            f"{verb} {html.escape(kind.lower())}"
        )
        title = html.escape(rec.title)
        if rec.url:
            title = f'<a href="{html.escape(rec.url, quote=True)}">{title}</a>'
        body_lines = render_change_text(change, tz_name).split("\n")[2:]
        # Drop the raw URL line; it is already on the title.
        body_lines = [ln for ln in body_lines if ln.strip() != (rec.url or "").strip()]
        body = html.escape("\n".join(body_lines).strip())
        block = f"{head}\n{title}"
        if body:
            block += f"\n{body}"
        blocks.append(block)

    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > TELEGRAM_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def render_discord(changes: list[Change], tz_name: str, header: str | None = None) -> list[str]:
    blocks = []
    if header:
        blocks.append(f"**{header}**")
    for change in changes:
        text = render_change_text(change, tz_name)
        first, _, rest = text.partition("\n")
        blocks.append(f"**{first}**\n{rest}")
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) > DISCORD_LIMIT and current:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def render_plain(changes: list[Change], tz_name: str, header: str | None = None) -> str:
    parts = [header] if header else []
    parts += [render_change_text(c, tz_name) for c in changes]
    return ("\n\n" + "-" * 48 + "\n\n").join(p for p in parts if p)


def render_email_html(changes: list[Change], tz_name: str, header: str) -> str:
    rows = []
    for change in changes:
        text = html.escape(render_change_text(change, tz_name)).replace("\n", "<br>")
        rows.append(
            '<div style="margin:0 0 18px;padding:12px 14px;border-left:3px solid #6b8afd;'
            'background:#f6f8ff;font-family:-apple-system,Segoe UI,sans-serif;'
            f'font-size:14px;line-height:1.5">{text}</div>'
        )
    return (
        f'<div style="font-family:-apple-system,Segoe UI,sans-serif">'
        f"<h2 style=\"font-size:16px\">{html.escape(header)}</h2>{''.join(rows)}</div>"
    )


def due_soon_summary(records, tz_name: str, days: int = 7) -> str:
    """A short 'what's coming up' block built from current assignment records."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    upcoming = []
    for rec in records:
        if rec.kind != "assignment":
            continue
        due = parse_iso(rec.fields.get("due_at"))
        if due is None:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if now <= due <= horizon:
            upcoming.append((due, rec))
    if not upcoming:
        return ""
    upcoming.sort(key=lambda pair: pair[0])
    lines = [f"⏰ Due in the next {days} days:"]
    for due, rec in upcoming:
        delta = due - now
        hours = int(delta.total_seconds() // 3600)
        when = f"{hours}h" if hours < 48 else f"{hours // 24}d"
        lines.append(f"• {rec.course_code}: {rec.title} — {fmt_dt(rec.fields.get('due_at'), tz_name)} (in {when})")
    return "\n".join(lines)
