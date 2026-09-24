"""SQLite snapshot store. Holds the last-seen state of every watched item."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collectors import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    key           TEXT PRIMARY KEY,
    course_id     INTEGER NOT NULL,
    course_code   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    content_hash  TEXT NOT NULL,
    payload       TEXT NOT NULL,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    last_changed  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_course ON items(course_id);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);

CREATE TABLE IF NOT EXISTS seeded_courses (
    course_id  INTEGER PRIMARY KEY,
    seeded_at  TEXT NOT NULL,
    item_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,
    changes     INTEGER DEFAULT 0,
    notified    INTEGER DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER,
    created_at  TEXT NOT NULL,
    course_code TEXT,
    kind        TEXT,
    change_type TEXT,
    title       TEXT,
    url         TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        with closing(self.conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- seeding -----------------------------------------------------------

    def is_seeded(self, course_id: int) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seeded_courses WHERE course_id = ?", (course_id,)
        )
        return cur.fetchone() is not None

    def mark_seeded(self, course_id: int, item_count: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO seeded_courses (course_id, seeded_at, item_count)"
            " VALUES (?, ?, ?)",
            (course_id, utcnow(), item_count),
        )
        self.conn.commit()

    # -- items -------------------------------------------------------------

    def snapshot_for_course(self, course_id: int) -> dict[str, dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT key, content_hash, payload, title, url FROM items WHERE course_id = ?",
            (course_id,),
        )
        out: dict[str, dict[str, Any]] = {}
        for row in cur.fetchall():
            out[row["key"]] = {
                "content_hash": row["content_hash"],
                "payload": json.loads(row["payload"]),
                "title": row["title"],
                "url": row["url"],
            }
        return out

    def upsert(self, record: Record) -> None:
        now = utcnow()
        digest = record.content_hash()
        cur = self.conn.execute(
            "SELECT content_hash, first_seen FROM items WHERE key = ?", (record.key,)
        )
        row = cur.fetchone()
        payload = record.to_json()
        if row is None:
            self.conn.execute(
                "INSERT INTO items (key, course_id, course_code, kind, item_id, title, url,"
                " content_hash, payload, first_seen, last_seen, last_changed)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.key,
                    record.course_id,
                    record.course_code,
                    record.kind,
                    record.item_id,
                    record.title,
                    record.url,
                    digest,
                    payload,
                    now,
                    now,
                    now,
                ),
            )
        else:
            changed = row["content_hash"] != digest
            self.conn.execute(
                "UPDATE items SET title=?, url=?, content_hash=?, payload=?, last_seen=?,"
                " last_changed=CASE WHEN ? THEN ? ELSE last_changed END WHERE key=?",
                (
                    record.title,
                    record.url,
                    digest,
                    payload,
                    now,
                    1 if changed else 0,
                    now,
                    record.key,
                ),
            )

    def upsert_many(self, records: list[Record]) -> None:
        for rec in records:
            self.upsert(rec)
        self.conn.commit()

    def delete_keys(self, keys: list[str]) -> None:
        if not keys:
            return
        self.conn.executemany("DELETE FROM items WHERE key = ?", [(k,) for k in keys])
        self.conn.commit()

    def item_count(self, course_id: int | None = None) -> int:
        if course_id is None:
            cur = self.conn.execute("SELECT COUNT(*) AS n FROM items")
        else:
            cur = self.conn.execute(
                "SELECT COUNT(*) AS n FROM items WHERE course_id = ?", (course_id,)
            )
        return int(cur.fetchone()["n"])

    # -- runs and events ---------------------------------------------------

    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO runs (started_at) VALUES (?)", (utcnow(),))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_run(
        self, run_id: int, status: str, changes: int, notified: int, error: str | None = None
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, changes=?, notified=?, error=? WHERE id=?",
            (utcnow(), status, changes, notified, error, run_id),
        )
        self.conn.commit()

    def record_events(self, run_id: int, changes: list) -> None:
        now = utcnow()
        rows = [
            (
                run_id,
                now,
                c.record.course_code,
                c.record.kind,
                c.change_type,
                c.record.title,
                c.record.url,
                json.dumps(c.detail, default=str),
            )
            for c in changes
        ]
        self.conn.executemany(
            "INSERT INTO events (run_id, created_at, course_code, kind, change_type,"
            " title, url, detail) VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()

    def recent_events(self, since_iso: str) -> list[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM events WHERE created_at >= ? ORDER BY created_at DESC", (since_iso,)
        )
        return cur.fetchall()

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        return cur.fetchall()
