"""Notification channels: Telegram, Discord, email, and console."""
from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage

import requests

from .config import Config

log = logging.getLogger(__name__)


class Notifier:
    """Sends to every configured channel. One failing channel never blocks the others."""

    def __init__(self, cfg: Config, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run

    # -- individual channels ----------------------------------------------

    def _send_telegram(self, messages: list[str]) -> int:
        url = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}/sendMessage"
        sent = 0
        for msg in messages:
            payload = {
                "chat_id": self.cfg.telegram_chat_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            for attempt in range(3):
                try:
                    resp = requests.post(url, json=payload, timeout=self.cfg.http_timeout)
                except requests.RequestException as exc:
                    log.warning("Telegram send failed (%s/3): %s", attempt + 1, exc)
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code == 429:
                    wait = int(resp.json().get("parameters", {}).get("retry_after", 5))
                    log.warning("Telegram rate limited; waiting %ss", wait)
                    time.sleep(wait)
                    continue
                if resp.ok:
                    sent += 1
                    break
                log.warning("Telegram returned %s: %s", resp.status_code, resp.text[:300])
                # A malformed HTML entity is the usual cause - retry as plain text.
                if resp.status_code == 400 and payload.get("parse_mode"):
                    payload.pop("parse_mode")
                    continue
                break
            time.sleep(0.4)  # stay under Telegram's ~30 msg/sec ceiling
        return sent

    def _send_discord(self, messages: list[str]) -> int:
        sent = 0
        for msg in messages:
            try:
                resp = requests.post(
                    self.cfg.discord_webhook_url,
                    json={"content": msg},
                    timeout=self.cfg.http_timeout,
                )
                if resp.ok or resp.status_code == 204:
                    sent += 1
                else:
                    log.warning("Discord returned %s: %s", resp.status_code, resp.text[:200])
            except requests.RequestException as exc:
                log.warning("Discord send failed: %s", exc)
            time.sleep(0.5)
        return sent

    def _send_email(self, subject: str, plain: str, html_body: str | None) -> int:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.smtp_from or self.cfg.smtp_user
        msg["To"] = self.cfg.smtp_to
        msg.set_content(plain)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        try:
            with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                if self.cfg.smtp_user:
                    smtp.login(self.cfg.smtp_user, self.cfg.smtp_password)
                smtp.send_message(msg)
            return 1
        except Exception as exc:  # noqa: BLE001 - email is best effort
            log.warning("Email send failed: %s", exc)
            return 0

    # -- public API --------------------------------------------------------

    def send(
        self,
        subject: str,
        telegram_messages: list[str],
        discord_messages: list[str],
        plain: str,
        email_html: str | None = None,
    ) -> int:
        if self.dry_run:
            print("=" * 60)
            print(f"[DRY RUN] {subject}")
            print("=" * 60)
            print(plain)
            print()
            return 0

        sent = 0
        if self.cfg.telegram_bot_token and self.cfg.telegram_chat_id:
            sent += self._send_telegram(telegram_messages)
        if self.cfg.discord_webhook_url:
            sent += self._send_discord(discord_messages)
        if self.cfg.smtp_host and self.cfg.smtp_to:
            sent += self._send_email(subject, plain, email_html)
        if sent == 0:
            log.warning("No notification channel is configured - printing instead.")
            print(plain)
        return sent

    def send_simple(self, subject: str, text: str) -> int:
        return self.send(subject, [text], [text], text, None)


def telegram_chat_id_helper(bot_token: str) -> str:
    """Print recent chat IDs that have messaged the bot - used by `--setup-telegram`."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"Could not reach Telegram: {exc}"
    data = resp.json()
    if not data.get("ok"):
        return f"Telegram error: {data}"
    results = data.get("result", [])
    if not results:
        return (
            "No messages yet. Open Telegram, find your bot, press Start and send it any "
            "message, then run this again."
        )
    lines = []
    for update in results[-10:]:
        chat = (update.get("message") or update.get("channel_post") or {}).get("chat", {})
        if chat:
            lines.append(
                f"chat_id={chat.get('id')}  type={chat.get('type')}  "
                f"name={chat.get('first_name') or chat.get('title')}"
            )
    return "\n".join(dict.fromkeys(lines)) or "No chat IDs found in recent updates."
