"""Turn raw Canvas API objects into normalised, comparable records."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from .canvas_client import CanvasClient, CanvasError
from .config import Config, Course

log = logging.getLogger(__name__)

# Human labels for each record kind, used in alert headings.
KIND_LABELS = {
    "announcement": "Announcement",
    "discussion": "Discussion",
    "discussion_entry": "Discussion reply",
    "assignment": "Assignment",
    "syllabus": "Syllabus",
    "module": "Module",
    "module_item": "Module item",
    "page": "Page",
    "file": "File",
}

# Fields whose change is worth interrupting Greg for, in priority order.
HIGH_SIGNAL_FIELDS = {"due_at", "unlock_at", "lock_at", "points_possible", "body", "message"}


def html_to_text(html: str | None) -> str:
    """Flatten Canvas rich text to plain text so diffs are readable."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # Keep link targets visible - lecturers often bury a Zoom or paper link.
    for a in soup.find_all("a"):
        href = a.get("href")
        label = a.get_text(strip=True)
        if href and label and href not in label:
            a.replace_with(f"{label} <{href}>")
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*", "\n\n", text)
    return text.strip()


@dataclass
class Record:
    """One watched thing in Canvas at one point in time."""

    course_id: int
    course_code: str
    kind: str
    item_id: str
    title: str
    url: str
    fields: dict[str, Any] = field(default_factory=dict)
    # Not compared, just carried through for display.
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.course_id}:{self.kind}:{self.item_id}"

    def content_hash(self) -> str:
        blob = json.dumps(self.fields, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def collect_course(client: CanvasClient, course: Course, cfg: Config) -> list[Record]:
    """Fetch every watched entity for one course. Failures in one area don't sink the rest."""
    records: list[Record] = []
    cid = course.canvas_id
    code = course.code

    def guard(label: str, fn):
        try:
            fn()
        except CanvasError as exc:
            log.warning("%s: could not read %s (%s)", code, label, exc)
        except Exception as exc:  # noqa: BLE001 - never let one section kill the run
            log.warning("%s: unexpected error reading %s (%s)", code, label, exc)

    if cfg.watch_syllabus:
        def _syllabus():
            data = client.course(cid, include_syllabus=True)
            body = html_to_text(data.get("syllabus_body"))
            records.append(
                Record(
                    course_id=cid,
                    course_code=code,
                    kind="syllabus",
                    item_id=str(cid),
                    title=f"{code} syllabus",
                    url=f"{client.base_url}/courses/{cid}/assignments/syllabus",
                    fields={"body": body},
                )
            )
        guard("syllabus", _syllabus)

    if cfg.watch_announcements:
        def _announcements():
            for a in client.announcements(cid):
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="announcement",
                        item_id=_s(a.get("id")),
                        title=_s(a.get("title")) or "(untitled announcement)",
                        url=_s(a.get("html_url")),
                        fields={
                            "title": _s(a.get("title")),
                            "message": html_to_text(a.get("message")),
                            "posted_at": _s(a.get("posted_at")),
                        },
                        meta={"author": _s((a.get("author") or {}).get("display_name"))},
                    )
                )
        guard("announcements", _announcements)

    if cfg.watch_discussions:
        def _discussions():
            topics = [t for t in client.discussions(cid) if not t.get("is_announcement")]
            for t in topics:
                tid = _s(t.get("id"))
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="discussion",
                        item_id=tid,
                        title=_s(t.get("title")) or "(untitled discussion)",
                        url=_s(t.get("html_url")),
                        fields={
                            "title": _s(t.get("title")),
                            "message": html_to_text(t.get("message")),
                            "locked": _s(t.get("locked")),
                            "reply_count": _s(t.get("discussion_subentry_count")),
                        },
                    )
                )
                # Replies are where deadlines and clarifications hide.
                try:
                    entries = client.discussion_entries(cid, int(tid))
                except (CanvasError, ValueError):
                    continue
                for e in entries:
                    records.append(
                        Record(
                            course_id=cid,
                            course_code=code,
                            kind="discussion_entry",
                            item_id=f"{tid}-{_s(e.get('id'))}",
                            title=f"Reply in: {_s(t.get('title'))}",
                            url=_s(t.get("html_url")),
                            fields={
                                "message": html_to_text(e.get("message")),
                            },
                            meta={
                                "author": _s(e.get("user_name")),
                                "created_at": _s(e.get("created_at")),
                            },
                        )
                    )
        guard("discussions", _discussions)

    if cfg.watch_assignments:
        def _assignments():
            for a in client.assignments(cid):
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="assignment",
                        item_id=_s(a.get("id")),
                        title=_s(a.get("name")) or "(untitled assignment)",
                        url=_s(a.get("html_url")),
                        fields={
                            "name": _s(a.get("name")),
                            "due_at": _s(a.get("due_at")),
                            "unlock_at": _s(a.get("unlock_at")),
                            "lock_at": _s(a.get("lock_at")),
                            "points_possible": _s(a.get("points_possible")),
                            "submission_types": ",".join(a.get("submission_types") or []),
                            "description": html_to_text(a.get("description")),
                            "published": _s(a.get("published")),
                        },
                        meta={"due_at": _s(a.get("due_at"))},
                    )
                )
        guard("assignments", _assignments)

    if cfg.watch_modules:
        def _modules():
            for m in client.modules(cid):
                mid = _s(m.get("id"))
                items = m.get("items")
                if items is None:
                    try:
                        items = client.module_items(cid, int(mid))
                    except (CanvasError, ValueError):
                        items = []
                item_titles = [_s(i.get("title")) for i in items]
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="module",
                        item_id=mid,
                        title=_s(m.get("name")) or "(untitled module)",
                        url=f"{client.base_url}/courses/{cid}/modules",
                        fields={
                            "name": _s(m.get("name")),
                            "published": _s(m.get("published")),
                            "items": "\n".join(item_titles),
                        },
                    )
                )
                for i in items:
                    records.append(
                        Record(
                            course_id=cid,
                            course_code=code,
                            kind="module_item",
                            item_id=_s(i.get("id")),
                            title=_s(i.get("title")) or "(untitled item)",
                            url=_s(i.get("html_url")),
                            fields={
                                "title": _s(i.get("title")),
                                "type": _s(i.get("type")),
                                "published": _s(i.get("published")),
                            },
                            meta={"module": _s(m.get("name"))},
                        )
                    )
        guard("modules", _modules)

    if cfg.watch_pages:
        def _pages():
            for p in client.pages(cid):
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="page",
                        item_id=_s(p.get("url")) or _s(p.get("page_id")),
                        title=_s(p.get("title")) or "(untitled page)",
                        url=_s(p.get("html_url")),
                        fields={
                            "title": _s(p.get("title")),
                            "updated_at": _s(p.get("updated_at")),
                        },
                    )
                )
        guard("pages", _pages)

    if cfg.watch_files:
        def _files():
            for f in client.files(cid):
                records.append(
                    Record(
                        course_id=cid,
                        course_code=code,
                        kind="file",
                        item_id=_s(f.get("id")),
                        title=_s(f.get("display_name")) or "(unnamed file)",
                        url=_s(f.get("url")).split("?")[0]
                        or f"{client.base_url}/courses/{cid}/files",
                        fields={
                            "display_name": _s(f.get("display_name")),
                            "updated_at": _s(f.get("updated_at")),
                            "size": _s(f.get("size")),
                        },
                    )
                )
        guard("files", _files)

    return records
