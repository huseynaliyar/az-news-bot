from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from src.ai_processor import CuratedStory, curate_news
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
    max_news_age_hours = int(os.getenv("MAX_NEWS_AGE_HOURS", "12"))
    max_curate_per_run = int(os.getenv("MAX_CURATE_PER_RUN", "5"))
    posts_per_run = int(os.getenv("MAX_POSTS_PER_RUN", "1"))
    min_importance = int(os.getenv("MIN_IMPORTANCE", "7"))
    urgent_threshold = int(os.getenv("URGENT_THRESHOLD", "9"))
    queue_max_age_hours = int(os.getenv("QUEUE_MAX_AGE_HOURS", "8"))
    memory_days = int(os.getenv("MEMORY_DAYS", "7"))
    retention_days = int(os.getenv("HISTORY_RETENTION_DAYS", "14"))
    only_verified = _env_bool("ONLY_VERIFIED", default=False)
    silent_night = _env_bool("SILENT_NIGHT", default=True)
    dry_run = _env_bool("DRY_RUN", default=False)

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not dry_run and (not bot_token or not chat_id):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID təyin edilməlidir (və ya DRY_RUN=true edin)."
        )

    feeds = load_feeds_config(feeds_path)
    memory = NewsMemory(history_path, retention_days=retention_days)
    memory.prune(queue_max_age_hours=queue_max_age_hours)
    memory.pending_queue = [
        q for q in memory.pending_queue if int(q.get("importance", 6)) >= min_importance
    ]

    logger.info("%d RSS mənbəsi oxunur (son %d saat)...", len(feeds), max_news_age_hours)
    raw_items = fetch_all_feeds(
        feeds,
        max_items_per_feed=max_items_per_feed,
        max_age_hours=max_news_age_hours,
    )
    unseen_items = memory.filter_unseen(raw_items)

    logger.info(
        "Cəmi %d xəbər oxundu, %d yeni (təkrarsız) xəbər tapıldı. Növbədə: %d",
        len(raw_items),
        len(unseen_items),
        len(memory.pending_queue),
    )

    if unseen_items:
        recent_stories = memory.get_recent_stories(memory_days=memory_days)
        curated = curate_news(
            candidates=unseen_items,
            recent_stories=recent_stories,
            max_posts=max_curate_per_run,
        )
        curated = [s for s in curated if s.importance >= min_importance]
        if only_verified:
            curated = [s for s in curated if s.verified]

        memory.enqueue_stories([s.to_dict() for s in curated])
        memory.mark_urls_seen([item.url for item in unseen_items])
        logger.info(
            "AI %d mühüm xəbəri (>=%d bal) növbəyə əlavə etdi. Ümumi növbə: %d",
            len(curated),
            min_importance,
            len(memory.pending_queue),
        )

    to_publish_dicts = memory.pop_stories_to_publish(
        posts_per_run=posts_per_run,
        urgent_threshold=urgent_threshold,
    )

    if not to_publish_dicts:
        logger.info("Paylaşılacaq mühüm xəbər yoxdur. Çıxılır.")
        memory.save(queue_max_age_hours=queue_max_age_hours)
        return 0

    to_publish = [CuratedStory.from_dict(d) for d in to_publish_dicts]
    sent = publish_stories(
        stories=to_publish,
        bot_token=bot_token,
        chat_id=chat_id,
        dry_run=dry_run,
        urgent_threshold=urgent_threshold,
        silent_night=silent_night,
    )

    for story in to_publish:
        memory.record_published_story(
            headline_az=story.headline_az,
            summary_az=story.summary_az,
            topic_key=story.topic_key,
            sources=[s["name"] for s in story.sources],
        )

    memory.save(queue_max_age_hours=queue_max_age_hours)
    logger.info(
        "Tamamlandı! %d xəbər Telegram-a göndərildi. Növbədə qalan: %d",
        sent,
        len(memory.pending_queue),
    )
    return sent


if __name__ == "__main__":
    run()
