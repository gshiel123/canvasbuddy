# Canvas Buddy

Watches your Canvas modules and messages you when anything
changes — including the silent edits Canvas never notifies you about.

Built for Semester 1 2026/27:

| Course | Canvas ID |
|---|---|
| yyyy-xxnnnn — Example Module Name | Canvas ID |

# Changes to be made:
in directory canvas-buddy > config.py
        line 57 = DEFAULT_COURSES = "CANVASID:MODULEID, ..."
        line 63 = "CANVAS_BASE_URL", "your_canvas_home_link"

## What it catches

| Watched | New items | Edits |
|---|---|---|
| Announcements | yes | yes |
| Discussions and every reply | yes | yes |
| Assignments | yes | due date, marks, brief, open/close dates |
| Syllabus | — | full text diff |
| Modules and module items | yes | renames, publishing |
| Pages | yes | update timestamp |
| Files | yes | replacements |

A moved deadline arrives as `Due date: Sun 11 Oct, 00:59 → Thu 08 Oct, 18:00`.
An edited brief arrives as a line-level diff, so "must use IEEE format" added
quietly three weeks in still reaches you.

## Setup (about 10 minutes)

### 1. Install

```bash
cd canvas-buddy
./scripts/install-mac.sh
```

### 2. Canvas token

Canvas → Account → Settings → Approved Integrations → **+ New Access Token**.
Copy it immediately; Canvas never shows it again. Paste it into `.env` as
`CANVAS_TOKEN`. That file is gitignored — keep it that way, the token is
full access to your Canvas account.

### 3. Telegram

1. Open Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
2. Put the token it gives you into `.env` as `TELEGRAM_BOT_TOKEN`.
3. Find your new bot, press **Start**, send it any message.
4. Run this to get your chat id, and put it in `.env` as `TELEGRAM_CHAT_ID`:

```bash
./.venv/bin/python -m canvas_buddy.run setup-telegram
```

Discord and email work too — see the commented blocks in `.env.example`.
Any combination can be on at once.

### 4. Check it works

```bash
./.venv/bin/python -m canvas_buddy.run test
```

This confirms the token, resolves all three course IDs, and sends a test message.

### 5. First run

```bash
./.venv/bin/python -m canvas_buddy.run check
```

The first run records everything already on Canvas as the baseline and stays
quiet about it — you get one "now watching" message, not 200 alerts for
existing content. Everything after that is genuinely new or changed.

## Running it continuously

**On your Mac, for now** — copy `scripts/com.canvasbuddy.watch.plist` to
`~/Library/LaunchAgents/`, edit the two paths inside it, then:

```bash
launchctl load ~/Library/LaunchAgents/com.canvasbuddy.watch.plist
```

It polls every 45 minutes, and launchd runs a missed poll when the Mac wakes.
Nothing is lost while the lid is shut — Canvas keeps the history, so the next
poll catches up.

**On the Pi, later** — the same folder works unchanged:

```bash
docker compose up -d --build
```

Copy the `data/` folder across with it and the Pi picks up exactly where the
Mac left off, with no duplicate alerts. Or use
`scripts/canvas-buddy.service` for a plain systemd install.

## Commands

| Command | Does |
|---|---|
| `check` | one polling pass |
| `check --dry-run` | same, but prints alerts instead of sending |
| `watch` | poll continuously (used by Docker) |
| `test` | verify token, courses and channels |
| `status` | local state and recent run history |
| `digest --hours 24` | summary of recent changes plus what's due soon |
| `courses` | list active Canvas courses and IDs |
| `setup-telegram` | find your chat id |

Add a daily digest at 8am with a second launchd entry or a cron line:

```
0 8 * * * cd /path/to/canvas-buddy && ./.venv/bin/python -m canvas_buddy.run digest --hours 24
```

## Semester 2

When you want to add your semester 2 modules, run `courses` to get their IDs and add
them to `CANVAS_COURSES` in `.env`. They'll be baselined silently on the next
poll.

## Tuning the noise

Every section can be switched off in `.env`. If module and file alerts turn out
to be chatty, set `WATCH_MODULES=false` and `WATCH_FILES=false` — announcements,
assignments, discussions and the syllabus carry nearly all the real signal.

`POLL_MINUTES=45` is well within Canvas's rate limit of roughly 700 requests
per 10 minutes; three courses cost about 25 requests per poll.

## Tests

```bash
./.venv/bin/python -m pytest tests/ -q
```

Seven tests run the whole pipeline against a simulated Canvas server — baseline,
quiet run, a lecturer editing a deadline and a syllabus, then quiet again — with
no token and no network. `python scripts/demo.py` prints the alerts that
scenario produces.

## How it works

```
Canvas REST API  →  collectors (HTML → text)  →  SQLite snapshot
                                                      ↓
                                              diff against last poll
                                                      ↓
                                     Telegram / Discord / email
```

State lives in `data/state.db`. Delete it to start over; the next run rebuilds
the baseline silently.

## If something breaks

| Symptom | Cause |
|---|---|
| `token problem` alert | Token expired or revoked. Make a new one, update `.env`. |
| A course reports `FAILED` in `test` | Wrong ID, or enrolment ended. Run `courses`. |
| No alerts at all | Check `status` for recent run errors. |
| Too many alerts | Turn off `WATCH_MODULES` / `WATCH_FILES` / `WATCH_PAGES`. |

The token is the one secret here. It is never logged, never sent anywhere except
Canvas, and lives only in `.env`.
