from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from src.ai_processor import CuratedStory

logger = logging.getLogger(__name__)

REGION_FLAGS = {
    "az": "🇦🇿",
    "tr": "🇹🇷",
    "world": "🌍",
}

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']|'
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    re.IGNORECASE,
)

YOUTUBE_EMBED_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})',
    re.IGNORECASE,
)

VIDEO_INDICATOR_RE = re.compile(
    r'(?:property|name)=["\']og:video|<video[\s>]|["\']https?://[^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?["\']',
    re.IGNORECASE,
)


def _is_youtube_url(url: str) -> bool:
    u = url.lower()
    return "youtube.com/watch" in u or "youtu.be/" in u or "youtube.com/shorts/" in u


def is_baku_night(start_hour: int = 0, end_hour: int = 8) -> bool:
    baku_hour = (datetime.now(timezone.utc) + timedelta(hours=4)).hour
    if start_hour < end_hour:
        return start_hour <= baku_hour < end_hour
    return baku_hour >= start_hour or baku_hour < end_hour


def _post_with_retry(url: str, payload: dict, timeout: int, attempts: int = 3) -> dict:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            # Do not retry 4xx client errors (bad photo URL, invalid HTML, etc.)
            if getattr(exc, "response", None) is not None and 400 <= exc.response.status_code < 500:
                raise
            logger.warning("Telegram cəhd %d/%d xətası: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise last_exc  # type: ignore[misc]


def fetch_article_media(article_url: str, timeout: int = 8) -> tuple[str, str]:
    if not article_url or "news.google.com" in article_url:
        return "", ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AzNewsBot/1.0; +https://github.com/)"
        }
        resp = requests.get(article_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        snippet = resp.text[:100000]

        image_url = ""
        i_match = OG_IMAGE_RE.search(snippet)
        if i_match:
            img = html.unescape((i_match.group(1) or i_match.group(2) or "").strip())
            if img.startswith("http://") or img.startswith("https://"):
                image_url = img

        video_watch_url = ""
        yt_match = YOUTUBE_EMBED_RE.search(snippet)
        if yt_match:
            video_watch_url = f"https://www.youtube.com/watch?v={yt_match.group(1)}"
        elif VIDEO_INDICATOR_RE.search(snippet):
            video_watch_url = article_url

        return video_watch_url, image_url
    except Exception as exc:
        logger.debug("Media oxunmadı (%s): %s", article_url, exc)
        return "", ""


def _build_video_button(video_url: str) -> dict | None:
    if not video_url:
        return None
    return {
        "inline_keyboard": [
            [{"text": "▶️ Videoya bax", "url": video_url}]
        ]
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
    disable_preview: bool = True,
    disable_notification: bool = False,
    reply_markup: dict | None = None,
    timeout: int = 20,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
        "disable_notification": disable_notification,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post_with_retry(url, payload, timeout=timeout)


def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    photo_url: str,
    caption: str,
    disable_notification: bool = False,
    reply_markup: dict | None = None,
    timeout: int = 25,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload: dict = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1024],
        "parse_mode": "HTML",
        "disable_notification": disable_notification,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _post_with_retry(url, payload, timeout=timeout)


def publish_stories(
    stories: list[CuratedStory],
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
    delay_seconds: float = 1.5,
    urgent_threshold: int = 9,
    silent_night: bool = True,
) -> int:
    sent_count = 0
    night_mode = silent_night and is_baku_night(0, 8)

    for story in stories:
        message = format_telegram_message(story)
        primary_url = story.sources[0]["url"] if story.sources else ""
        silent = (night_mode and story.importance < urgent_threshold) or (
            story.category.lower() in {"elm", "maraqlı"}
        )

        video_watch_url = primary_url if _is_youtube_url(primary_url) else ""
        image_url = story.image_url

        if not dry_run:
            for src in story.sources:
                v_url, i_url = fetch_article_media(src["url"])
                if not video_watch_url and v_url:
                    video_watch_url = v_url
                if not image_url and i_url:
                    image_url = i_url
                if video_watch_url and image_url:
                    break

        reply_markup = _build_video_button(video_watch_url)

        if dry_run:
            logger.info(
                "[DRY_RUN] Telegram (img=%s | video_btn=%s | silent=%s):\n%s\n%s",
                bool(image_url),
                video_watch_url or "yox",
                silent,
                message,
                "-" * 40,
            )
            sent_count += 1
            continue

        posted = False
        if image_url:
            try:
                send_telegram_photo(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    photo_url=image_url,
                    caption=message,
                    disable_notification=silent,
                    reply_markup=reply_markup,
                )
                posted = True
            except Exception as exc:
                logger.warning("Şəkil göndərilmədi, mətnə keçilir (%s): %s", image_url, exc)

        if not posted:
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=message,
                disable_preview=not _is_youtube_url(primary_url),
                disable_notification=silent,
                reply_markup=reply_markup,
            )

        sent_count += 1
        if sent_count < len(stories):
            time.sleep(delay_seconds)

    return sent_count
