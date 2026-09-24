"""Configuration loading. All secrets come from the environment / .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Course:
    canvas_id: int
    code: str

    @property
    def label(self) -> str:
        return self.code


def _parse_courses(raw: str) -> list[Course]:
    """Parse 'id:CODE,id:CODE' into Course objects."""
    courses: list[Course] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            cid, code = chunk.split(":", 1)
        else:
            cid, code = chunk, chunk
        try:
            courses.append(Course(canvas_id=int(cid.strip()), code=code.strip()))
        except ValueError:
            continue
    return courses

""" TODO """
DEFAULT_COURSES = ""


@dataclass(frozen=True)
class Config:
    canvas_base_url: str = os.getenv(
        "CANVAS_BASE_URL", "TODO"
    ).rstrip("/")
    canvas_token: str = os.getenv("CANVAS_TOKEN", "")
    courses: list[Course] = field(
        default_factory=lambda: _parse_courses(os.getenv("CANVAS_COURSES", DEFAULT_COURSES))
    )

    db_path: str = os.getenv("CANVAS_BUDDY_DB", "data/state.db")
    timezone: str = os.getenv("TZ", "Europe/Dublin")

    # What to watch. Turn any of these off in .env if they get noisy.
    watch_announcements: bool = _bool("WATCH_ANNOUNCEMENTS", True)
    watch_discussions: bool = _bool("WATCH_DISCUSSIONS", True)
    watch_assignments: bool = _bool("WATCH_ASSIGNMENTS", True)
    watch_syllabus: bool = _bool("WATCH_SYLLABUS", True)
    watch_modules: bool = _bool("WATCH_MODULES", True)
    watch_pages: bool = _bool("WATCH_PAGES", True)
    watch_files: bool = _bool("WATCH_FILES", True)

    # Notification channels
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = _int("SMTP_PORT", 587)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "")
    smtp_to: str = os.getenv("SMTP_TO", "")

    poll_minutes: int = _int("POLL_MINUTES", 45)
    http_timeout: int = _int("HTTP_TIMEOUT", 30)
    max_retries: int = _int("HTTP_MAX_RETRIES", 4)
    # Alert when an assignment falls due within this many days.
    due_soon_days: int = _int("DUE_SOON_DAYS", 7)

    def validate(self) -> list[str]:
        problems = []
        if not self.canvas_token:
            problems.append("CANVAS_TOKEN is not set.")
        if not self.courses:
            problems.append("CANVAS_COURSES is empty or malformed.")
        return problems

    def active_channels(self) -> list[str]:
        channels = []
        if self.telegram_bot_token and self.telegram_chat_id:
            channels.append("telegram")
        if self.discord_webhook_url:
            channels.append("discord")
        if self.smtp_host and self.smtp_to:
            channels.append("email")
        return channels


def load_config() -> Config:
    return Config()
