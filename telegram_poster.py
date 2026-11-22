from __future__ import annotations

import html
import logging
import re
import time

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

OG_VIDEO_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:video(?::url|:secure_url)?["\'][^>]+content=["\']([^"\']+\.mp4[^"\']*)["\']|'
    r'<meta[^>]+content=["\']([^"\']+\.mp4[^"\']*)["\'][^>]+(?:property|name)=["\']og:video(?::url|:secure_url)?["\']',
    re.IGNORECASE,
)

HTML_MP4_RE = re.compile(
    r'<(?:video|source)[^>]+src=["\'](https?://[^"\']+\.mp4(?:\?[^"\']*)?)["\']',
    re.IGNORECASE,
)


def _is_youtube_url(url: str) -> bool:
    u = url.lower()
    return "youtube.com/watch" in u or "youtu.be/" in u or "youtube.com/shorts/" in u


def fetch_article_media(article_url: str, timeout: int = 8) -> tuple[str, str]:
    if not article_url or "news.google.com" in article_url:
        return "", ""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AzNewsBot/1.0; +https://github.com/)"
        }
        resp = requests.get(article_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        snippet = resp.text[:80000]

        video_url = ""
        v_match = OG_VIDEO_RE.search(snippet)
        if v_match:
            video_url = html.unescape((v_match.group(1) or v_match.group(2) or "").strip())
        else:
            h_match = HTML_MP4_RE.search(snippet)
            if h_match:
                video_url = html.unescape(h_match.group(1).strip())
        if video_url and not (video_url.startswith("http://") or video_url.startswith("https://")):
            video_url = ""

        image_url = ""
        i_match = OG_IMAGE_RE.search(snippet)
        if i_match:
            img = html.unescape((i_match.group(1) or i_match.group(2) or "").strip())
            if img.startswith("http://") or img.startswith("https://"):
                image_url = img

        return video_url, image_url
    except Exception as exc:
        logger.debug("Media oxunmadı (%s): %s", article_url, exc)
        return "", ""


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
    timeout: int = 20,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    photo_url: str,
    caption: str,
    timeout: int = 25,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1024],
        "parse_mode": "HTML",
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def send_telegram_video(
    bot_token: str,
    chat_id: str,
    video_url: str,
    caption: str,
    timeout: int = 35,
) -> dict:
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"
    payload = {
        "chat_id": chat_id,
        "video": video_url,
        "caption": caption[:1024],
        "parse_mode": "HTML",
        "supports_streaming": True,
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
        primary_url = story.sources[0]["url"] if story.sources else ""
        is_youtube = _is_youtube_url(primary_url)

        video_url = ""
        image_url = story.image_url

        if not is_youtube and not dry_run:
            for src in story.sources:
                v_url, i_url = fetch_article_media(src["url"])
                if not video_url and v_url:
                    video_url = v_url
                if not image_url and i_url:
                    image_url = i_url
                if video_url and image_url:
                    break

        if dry_run:
            media_tag = (
                f"VIDEO: {video_url}"
                if video_url
                else (f"PHOTO: {image_url}" if image_url else "TEXT")
            )
            logger.info("[DRY_RUN] Telegram (%s):\n%s\n%s", media_tag, message, "-" * 40)
            sent_count += 1
            continue

        if is_youtube:
            send_telegram_message(
                bot_token=bot_token,
                chat_id=chat_id,
                text=message,
                disable_preview=False,
            )
        else:
            posted = False
            if video_url:
                try:
                    send_telegram_video(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        video_url=video_url,
                        caption=message,
                    )
                    posted = True
                except Exception as exc:
                    logger.warning("Video göndərilmədi, şəkilə keçilir (%s): %s", video_url, exc)

            if not posted and image_url:
                try:
                    send_telegram_photo(
                        bot_token=bot_token,
                        chat_id=chat_id,
                        photo_url=image_url,
                        caption=message,
                    )
                    posted = True
                except Exception as exc:
                    logger.warning("Şəkil göndərilmədi, mətnə keçilir (%s): %s", image_url, exc)

            if not posted:
                send_telegram_message(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=message,
                    disable_preview=True,
                )

        sent_count += 1
        if sent_count < len(stories):
            time.sleep(delay_seconds)

    return sent_count
