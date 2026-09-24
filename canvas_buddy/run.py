"""Canvas Buddy command line entry point."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from .canvas_client import CanvasAuthError, CanvasClient, CanvasError
from .collectors import Record, collect_course
from .config import Config, load_config
from .diff import Change, diff_records
from .notify import Notifier, telegram_chat_id_helper
from .render import (
    due_soon_summary,
    render_discord,
    render_email_html,
    render_plain,
    render_telegram,
)
from .store import Store

log = logging.getLogger("canvas_buddy")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build_client(cfg: Config) -> CanvasClient:
    return CanvasClient(
        base_url=cfg.canvas_base_url,
        token=cfg.canvas_token,
        timeout=cfg.http_timeout,
        max_retries=cfg.max_retries,
    )


def _collect_all(client: CanvasClient, cfg: Config) -> dict[int, list[Record]]:
    out: dict[int, list[Record]] = {}
    for course in cfg.courses:
        log.info("Reading %s (id %s)...", course.code, course.canvas_id)
        records = collect_course(client, course, cfg)
        log.info("  %s items", len(records))
        out[course.canvas_id] = records
    return out


def cmd_check(cfg: Config, args) -> int:
    """One polling pass: fetch, diff, notify, save."""
    store = Store(cfg.db_path)
    notifier = Notifier(cfg, dry_run=args.dry_run)
    run_id = store.start_run()
    total_changes = 0
    notified = 0
    try:
        client = build_client(cfg)
        collected = _collect_all(client, cfg)

        all_changes: list[Change] = []
        newly_seeded: list[str] = []
        all_records: list[Record] = []

        for course in cfg.courses:
            records = collected.get(course.canvas_id, [])
            all_records.extend(records)
            if not records:
                log.warning("%s: nothing read; skipping diff to avoid false removals.", course.code)
                continue

            if not store.is_seeded(course.canvas_id):
                # First sight of this course: record the state, stay quiet.
                store.upsert_many(records)
                store.mark_seeded(course.canvas_id, len(records))
                newly_seeded.append(f"{course.code} ({len(records)} items)")
                log.info("%s: baseline saved, no alerts for existing content.", course.code)
                continue

            snapshot = store.snapshot_for_course(course.canvas_id)
            changes, missing = diff_records(records, snapshot, detect_removals=False)
            all_changes.extend(changes)
            store.upsert_many(records)
            if missing:
                log.debug("%s: %s items no longer listed", course.code, len(missing))

        total_changes = len(all_changes)

        if newly_seeded:
            text = (
                "✅ Canvas Buddy is watching:\n"
                + "\n".join(f"• {s}" for s in newly_seeded)
                + "\n\nExisting content has been recorded as the baseline. "
                "From now on you'll only hear about what's new or changed."
            )
            upcoming = due_soon_summary(all_records, cfg.timezone, cfg.due_soon_days)
            if upcoming:
                text += "\n\n" + upcoming
            notified += notifier.send_simple("Canvas Buddy — baseline saved", text)

        if all_changes:
            store.record_events(run_id, all_changes)
            header = (
                f"Canvas update — {len(all_changes)} change"
                f"{'s' if len(all_changes) != 1 else ''}"
            )
            notified += notifier.send(
                subject=header,
                telegram_messages=render_telegram(all_changes, cfg.timezone, header),
                discord_messages=render_discord(all_changes, cfg.timezone, header),
                plain=render_plain(all_changes, cfg.timezone, header),
                email_html=render_email_html(all_changes, cfg.timezone, header),
            )
            log.info("%s change(s) reported.", len(all_changes))
        elif not newly_seeded:
            log.info("No changes.")

        store.finish_run(run_id, "ok", total_changes, notified)
        return 0

    except CanvasAuthError as exc:
        log.error("%s", exc)
        store.finish_run(run_id, "auth_error", total_changes, notified, str(exc))
        if not args.dry_run:
            notifier.send_simple(
                "Canvas Buddy — token problem",
                f"⚠️ Canvas rejected the API token.\n\n{exc}\n\n"
                "Create a new token in Canvas → Account → Settings → Approved Integrations, "
                "then update CANVAS_TOKEN in the .env file.",
            )
        return 2
    except CanvasError as exc:
        log.error("Canvas error: %s", exc)
        store.finish_run(run_id, "error", total_changes, notified, str(exc))
        return 1
    finally:
        store.close()


def cmd_watch(cfg: Config, args) -> int:
    """Poll forever. Used by Docker and by the launchd/systemd service."""
    interval = max(5, args.interval or cfg.poll_minutes) * 60
    log.info("Watching every %s minutes. Ctrl-C to stop.", interval // 60)
    while True:
        started = time.monotonic()
        try:
            cmd_check(cfg, args)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
        except Exception as exc:  # noqa: BLE001 - a bad poll must not kill the watcher
            log.exception("Unexpected error in poll: %s", exc)
        elapsed = time.monotonic() - started
        time.sleep(max(30, interval - elapsed))


def cmd_test(cfg: Config, args) -> int:
    """Verify the Canvas token, the course IDs, and every notification channel."""
    ok = True
    print("Canvas")
    print(f"  base url : {cfg.canvas_base_url}")
    try:
        client = build_client(cfg)
        profile = client.whoami()
        print(f"  token    : OK — signed in as {profile.get('name')} ({profile.get('primary_email') or 'no email'})")
    except CanvasAuthError as exc:
        print(f"  token    : FAILED — {exc}")
        return 2
    except CanvasError as exc:
        print(f"  token    : FAILED — {exc}")
        return 2

    print("\nCourses")
    for course in cfg.courses:
        try:
            data = client.course(course.canvas_id)
            print(f"  {course.code:<10} id {course.canvas_id:<8} OK — {data.get('name')}")
        except CanvasError as exc:
            ok = False
            print(f"  {course.code:<10} id {course.canvas_id:<8} FAILED — {exc}")

    print("\nNotification channels")
    channels = cfg.active_channels()
    if not channels:
        print("  none configured — alerts will print to the console only")
    for ch in channels:
        print(f"  {ch}: configured")

    if channels and not args.no_send:
        notifier = Notifier(cfg, dry_run=False)
        sent = notifier.send_simple(
            "Canvas Buddy — test",
            "✅ Canvas Buddy test message. If you can read this, alerts work.",
        )
        print(f"  test message sent to {sent} channel(s)")

    print("\nWatching:", ", ".join(
        name for name, on in [
            ("announcements", cfg.watch_announcements),
            ("discussions", cfg.watch_discussions),
            ("assignments", cfg.watch_assignments),
            ("syllabus", cfg.watch_syllabus),
            ("modules", cfg.watch_modules),
            ("pages", cfg.watch_pages),
            ("files", cfg.watch_files),
        ] if on
    ))
    return 0 if ok else 1


def cmd_digest(cfg: Config, args) -> int:
    """Summarise the last N hours of events, plus what's due soon."""
    store = Store(cfg.db_path)
    notifier = Notifier(cfg, dry_run=args.dry_run)
    since = (datetime.now(timezone.utc) - timedelta(hours=args.hours)).isoformat(timespec="seconds")
    rows = store.recent_events(since)

    lines = [f"📋 Canvas digest — last {args.hours}h"]
    if rows:
        by_course: dict[str, list] = {}
        for row in rows:
            by_course.setdefault(row["course_code"], []).append(row)
        for code, items in sorted(by_course.items()):
            lines.append(f"\n{code} ({len(items)})")
            for row in items[:15]:
                verb = "new" if row["change_type"] == "new" else "updated"
                lines.append(f"  • {verb}: {row['title']}")
    else:
        lines.append("\nNothing changed.")

    try:
        client = build_client(cfg)
        records: list[Record] = []
        for course in cfg.courses:
            records.extend(collect_course(client, course, cfg))
        upcoming = due_soon_summary(records, cfg.timezone, cfg.due_soon_days)
        if upcoming:
            lines.append("\n" + upcoming)
    except CanvasError as exc:
        log.warning("Could not read due dates for the digest: %s", exc)

    text = "\n".join(lines)
    notifier.send_simple("Canvas Buddy — digest", text)
    store.close()
    return 0


def cmd_status(cfg: Config, args) -> int:
    store = Store(cfg.db_path)
    print(f"Database: {cfg.db_path}")
    print(f"Tracked items: {store.item_count()}")
    for course in cfg.courses:
        seeded = "yes" if store.is_seeded(course.canvas_id) else "no"
        print(f"  {course.code:<10} {store.item_count(course.canvas_id):>5} items   baseline: {seeded}")
    print("\nRecent runs:")
    for row in store.last_runs(8):
        print(
            f"  {row['started_at']}  {str(row['status'] or 'running'):<11}"
            f" changes={row['changes']:<4} notified={row['notified']}"
            + (f"  error={row['error'][:60]}" if row["error"] else "")
        )
    store.close()
    return 0


def cmd_setup_telegram(cfg: Config, args) -> int:
    token = args.token or cfg.telegram_bot_token
    if not token:
        print("Pass --token or set TELEGRAM_BOT_TOKEN in .env first.")
        return 1
    print(telegram_chat_id_helper(token))
    return 0


def cmd_courses(cfg: Config, args) -> int:
    """List active courses with their IDs - useful when semester 2 starts."""
    client = build_client(cfg)
    try:
        for course in client.active_courses():
            print(f"{course.get('id'):<10} {course.get('course_code') or '':<24} {course.get('name')}")
    except CanvasError as exc:
        print(f"Failed: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="canvas-buddy",
        description="Watch Canvas courses and alert on anything new or changed.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="run one polling pass")

    p_watch = sub.add_parser("watch", help="poll continuously")
    p_watch.add_argument("--interval", type=int, help="minutes between polls")

    p_test = sub.add_parser("test", help="verify token, courses and channels")
    p_test.add_argument("--no-send", action="store_true", help="skip the test message")

    p_digest = sub.add_parser("digest", help="summary of recent changes")
    p_digest.add_argument("--hours", type=int, default=24)

    sub.add_parser("status", help="show local state")
    sub.add_parser("courses", help="list active Canvas courses and IDs")

    p_tg = sub.add_parser("setup-telegram", help="find your Telegram chat id")
    p_tg.add_argument("--token", help="bot token, if not yet in .env")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_config()

    if args.command not in {"setup-telegram"}:
        problems = cfg.validate()
        if problems:
            for p in problems:
                print(f"Configuration problem: {p}", file=sys.stderr)
            print("\nCopy .env.example to .env and fill it in.", file=sys.stderr)
            return 2

    handlers = {
        "check": cmd_check,
        "watch": cmd_watch,
        "test": cmd_test,
        "digest": cmd_digest,
        "status": cmd_status,
        "courses": cmd_courses,
        "setup-telegram": cmd_setup_telegram,
    }
    # argparse defaults these only for the subcommands that declare them.
    for attr, default in (("dry_run", False), ("no_send", False), ("interval", None), ("hours", 24), ("token", None)):
        if not hasattr(args, attr):
            setattr(args, attr, default)

    return handlers[args.command](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
