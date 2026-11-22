from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from src.ai_processor import curate_news
from src.memory import NewsMemory
from src.rss_reader import fetch_all_feeds, load_feeds_config
from src.telegram_poster import publish_stories

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("az-news-bot")


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def run() -> int:
    load_dotenv()

    base_dir = Path(__file__).resolve().parent.parent
    feeds_path = Path(os.getenv("FEEDS_PATH", base_dir / "feeds.json"))
    history_path = Path(os.getenv("HISTORY_PATH", base_dir / "data" / "history.json"))

    max_items_per_feed = int(os.getenv("MAX_ITEMS_PER_FEED", "10"))
    max_posts_per_run = int(os.getenv("MAX_POSTS_PER_RUN", "5"))
    memory_days = int(os.getenv("MEMORY_DAYS", "7"))
    retention_days = int(os.getenv("HISTORY_RETENTION_DAYS", "14"))
    only_verified = _env_bool("ONLY_VERIFIED", default=False)
    dry_run = _env_bool("DRY_RUN", default=False)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not dry_run and (not bot_token or not chat_id):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID təyin edilməlidir (və ya DRY_RUN=true edin)."
        )

    feeds = load_feeds_config(feeds_path)
    memory = NewsMemory(history_path, retention_days=retention_days)

    logger.info("%d RSS mənbəsi oxunur...", len(feeds))
    raw_items = fetch_all_feeds(feeds, max_items_per_feed=max_items_per_feed)
    unseen_items = memory.filter_unseen(raw_items)

    logger.info(
        "Cəmi %d xəbər oxundu, %d yeni (təkrarsız) xəbər tapıldı.",
        len(raw_items),
        len(unseen_items),
    )

    if not unseen_items:
        logger.info("Yeni xəbər yoxdur. Çıxılır.")
        memory.save()
        return 0

    recent_stories = memory.get_recent_stories(memory_days=memory_days)
    curated = curate_news(
        candidates=unseen_items,
        recent_stories=recent_stories,
        max_posts=max_posts_per_run,
    )

    if only_verified:
        curated = [s for s in curated if s.verified]

    logger.info("AI tərəfindən %d xəbər seçildi.", len(curated))

    sent = publish_stories(
        stories=curated,
        bot_token=bot_token,
        chat_id=chat_id,
        dry_run=dry_run,
    )

    # Mark all fetched unseen URLs as processed so they are not re-evaluated next run
    memory.mark_urls_seen([item.url for item in unseen_items])
    for story in curated:
        memory.record_published_story(
            headline_az=story.headline_az,
            summary_az=story.summary_az,
            topic_key=story.topic_key,
            sources=[s["name"] for s in story.sources],
        )

    memory.save()
    logger.info("Tamamlandı! %d xəbər Telegram-a göndərildi.", sent)
    return sent


if __name__ == "__main__":
    run()
