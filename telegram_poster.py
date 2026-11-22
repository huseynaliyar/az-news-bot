from __future__ import annotations

import html
import logging
import time

import requests

from src.ai_processor import CuratedStory

logger = logging.getLogger(__name__)

REGION_FLAGS = {
    "az": "🇦🇿",
    "tr": "🇹🇷",
    "world": "🌍",
}


def format_telegram_message(story: CuratedStory) -> str:
    flag = REGION_FLAGS.get(story.region, "📰")
    status_badge = "✅ Təsdiqlənmiş" if story.verified else "⚠️ Tək mənbə"
    update_tag = " | 🔄 Yenilənmə" if story.is_update else ""

    headline = html.escape(story.headline_az)
    summary = html.escape(story.summary_az)
    category = html.escape(story.category)

    source_links = ", ".join(
        f'<a href="{html.escape(src["url"], quote=True)}">{html.escape(src["name"])}</a>'
        for src in story.sources
    )

    return (
        f"{flag} <b>{headline}</b>\n\n"
        f"{summary}\n\n"
        f"🏷 #{category.replace(' ', '_')} | {status_badge}{update_tag}\n"
        f"🔗 Mənbə: {source_links}"
    )


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout: int = 20,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def publish_stories(
    stories: list[CuratedStory],
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
    delay_seconds: float = 1.5,
) -> int:
    sent_count = 0
    for story in stories:
        message = format_telegram_message(story)
        if dry_run:
            logger.info("[DRY_RUN] Telegram mesajı:\n%s\n%s", message, "-" * 40)
            sent_count += 1
            continue

        send_telegram_message(bot_token=bot_token, chat_id=chat_id, text=message)
        sent_count += 1
        if sent_count < len(stories):
            time.sleep(delay_seconds)

    return sent_count
