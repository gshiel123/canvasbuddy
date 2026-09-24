"""Compare a fresh set of records against the stored snapshot."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from .collectors import Record

# Bigger number = more likely to matter to a working student.
PRIORITY_HIGH = 3
PRIORITY_MEDIUM = 2
PRIORITY_LOW = 1

_NEW_PRIORITY = {
    "announcement": PRIORITY_HIGH,
    "assignment": PRIORITY_HIGH,
    "discussion": PRIORITY_MEDIUM,
    "discussion_entry": PRIORITY_MEDIUM,
    "syllabus": PRIORITY_HIGH,
    "module": PRIORITY_LOW,
    "module_item": PRIORITY_LOW,
    "page": PRIORITY_LOW,
    "file": PRIORITY_LOW,
}

# A change in one of these is worth shouting about wherever it appears.
_CRITICAL_FIELDS = {"due_at", "lock_at", "unlock_at", "points_possible"}

# Long free-text fields get a line-level diff rather than before/after dumps.
_TEXT_FIELDS = {"body", "message", "description", "items"}

# Noise: these move on their own and rarely mean anything on their own.
_IGNORED_ON_CHANGE = {
    ("discussion", "reply_count"),  # covered by discussion_entry records
    ("page", "updated_at"),         # handled below as a plain "page updated"
    ("file", "updated_at"),
}

FIELD_LABELS = {
    "due_at": "Due date",
    "unlock_at": "Available from",
    "lock_at": "Closes",
    "points_possible": "Marks",
    "submission_types": "Submission type",
    "published": "Published",
    "body": "Content",
    "message": "Text",
    "description": "Brief",
    "items": "Items",
    "name": "Name",
    "title": "Title",
    "locked": "Locked",
    "display_name": "Filename",
    "size": "Size",
    "updated_at": "Updated",
}


@dataclass
class Change:
    record: Record
    change_type: str  # new | changed | removed
    priority: int = PRIORITY_LOW
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_high(self) -> bool:
        return self.priority >= PRIORITY_HIGH


def _text_diff(old: str, new: str, max_lines: int = 12) -> str:
    """Compact, human-readable diff of two blocks of text."""
    old_lines = [ln for ln in (old or "").splitlines() if ln.strip()]
    new_lines = [ln for ln in (new or "").splitlines() if ln.strip()]
    out: list[str] = []
    for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append(line)
        if len(out) >= max_lines:
            out.append("... (truncated)")
            break
    if not out:
        return "(whitespace-only change)"
    return "\n".join(out)


def _priority_for_change(kind: str, changed_fields: list[str]) -> int:
    if any(f in _CRITICAL_FIELDS for f in changed_fields):
        return PRIORITY_HIGH
    if kind in {"syllabus", "assignment", "announcement"}:
        return PRIORITY_HIGH
    if kind in {"discussion", "discussion_entry"}:
        return PRIORITY_MEDIUM
    return PRIORITY_LOW


def diff_records(
    fresh: list[Record],
    snapshot: dict[str, dict[str, Any]],
    detect_removals: bool = True,
) -> tuple[list[Change], list[str]]:
    """Return (changes, keys_no_longer_present)."""
    changes: list[Change] = []
    seen_keys: set[str] = set()

    for rec in fresh:
        seen_keys.add(rec.key)
        prev = snapshot.get(rec.key)
        if prev is None:
            changes.append(
                Change(
                    record=rec,
                    change_type="new",
                    priority=_NEW_PRIORITY.get(rec.kind, PRIORITY_LOW),
                    detail={"fields": dict(rec.fields)},
                )
            )
            continue

        if prev["content_hash"] == rec.content_hash():
            continue

        old_fields = (prev["payload"] or {}).get("fields", {}) or {}
        field_changes: dict[str, dict[str, str]] = {}
        for name, new_value in rec.fields.items():
            old_value = old_fields.get(name, "")
            if str(old_value) == str(new_value):
                continue
            if (rec.kind, name) in _IGNORED_ON_CHANGE:
                continue
            if name in _TEXT_FIELDS:
                field_changes[name] = {
                    "type": "text",
                    "diff": _text_diff(str(old_value), str(new_value)),
                }
            else:
                field_changes[name] = {
                    "type": "value",
                    "old": str(old_value),
                    "new": str(new_value),
                }

        if not field_changes:
            continue

        changes.append(
            Change(
                record=rec,
                change_type="changed",
                priority=_priority_for_change(rec.kind, list(field_changes)),
                detail={"changes": field_changes},
            )
        )

    missing: list[str] = []
    if detect_removals:
        missing = [k for k in snapshot if k not in seen_keys]

    changes.sort(key=lambda c: (-c.priority, c.record.course_code, c.record.kind))
    return changes, missing
