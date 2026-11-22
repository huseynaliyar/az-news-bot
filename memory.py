from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.rss_reader import NewsItem

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _normalize_tokens(text: str) -> set[str]:
    return {tok for tok in WORD_RE.findall(text.lower()) if len(tok) > 2}


def title_similarity(title_a: str, title_b: str) -> float:
    tokens_a = _normalize_tokens(title_a)
    tokens_b = _normalize_tokens(title_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class NewsMemory:
    """Tracks seen URLs and multi-day published story summaries for deduplication."""

    def __init__(self, path: str | Path, retention_days: int = 14) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self.seen_urls: dict[str, str] = {}
        self.published_stories: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.seen_urls = {}
            self.published_stories = []
            return

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.seen_urls = data.get("seen_urls", {})
        self.published_stories = data.get("published_stories", [])

    def prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)

        kept_urls: dict[str, str] = {}
        for h, ts in self.seen_urls.items():
            try:
                if datetime.fromisoformat(ts) >= cutoff:
                    kept_urls[h] = ts
            except ValueError:
                continue
        self.seen_urls = kept_urls

        kept_stories: list[dict] = []
        for story in self.published_stories:
            try:
                if datetime.fromisoformat(story["published_at"]) >= cutoff:
                    kept_stories.append(story)
            except (KeyError, ValueError):
                continue
        self.published_stories = kept_stories

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen_urls": self.seen_urls,
            "published_stories": self.published_stories,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def filter_unseen(
        self, items: list[NewsItem], fuzzy_threshold: float = 0.72
    ) -> list[NewsItem]:
        """Layer 1 & 2 deduplication: removes seen URLs and near-exact local duplicates."""
        unseen: list[NewsItem] = []
        seen_in_batch: set[str] = set()
        recent_titles = [s.get("headline_az", "") for s in self.published_stories]

        for item in items:
            h = url_hash(item.url)
            if h in self.seen_urls or h in seen_in_batch:
                continue

            if any(title_similarity(item.title, t) >= fuzzy_threshold for t in recent_titles):
                continue

            seen_in_batch.add(h)
            unseen.append(item)

        return unseen

    def mark_urls_seen(self, urls: list[str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for url in urls:
            if url:
                self.seen_urls[url_hash(url)] = now

    def get_recent_stories(self, memory_days: int = 7, limit: int = 60) -> list[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=memory_days)
        recent: list[dict] = []
        for story in reversed(self.published_stories):
            try:
                if datetime.fromisoformat(story["published_at"]) >= cutoff:
                    recent.append(story)
            except (KeyError, ValueError):
                continue
            if len(recent) >= limit:
                break
        return recent

    def record_published_story(
        self,
        headline_az: str,
        summary_az: str,
        topic_key: str,
        sources: list[str],
    ) -> None:
        self.published_stories.append(
            {
                "published_at": datetime.now(timezone.utc).isoformat(),
                "headline_az": headline_az,
                "summary_az": summary_az,
                "topic_key": topic_key,
                "sources": sources,
            }
        )
