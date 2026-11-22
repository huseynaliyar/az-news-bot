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
    """Tracks seen URLs, multi-day published stories, and drip-feed pending queue."""

    def __init__(self, path: str | Path, retention_days: int = 14) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self.seen_urls: dict[str, str] = {}
        self.published_stories: list[dict] = []
        self.pending_queue: list[dict] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.seen_urls = {}
            self.published_stories = []
            self.pending_queue = []
            return

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.seen_urls = data.get("seen_urls", {})
        self.published_stories = data.get("published_stories", [])
        self.pending_queue = data.get("pending_queue", [])

    def prune(self, queue_max_age_hours: int = 8) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self.retention_days)
        queue_cutoff = now - timedelta(hours=queue_max_age_hours)

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

        kept_queue: list[dict] = []
        for q in self.pending_queue:
            try:
                if datetime.fromisoformat(q["queued_at"]) >= queue_cutoff:
                    kept_queue.append(q)
            except (KeyError, ValueError):
                continue
        self.pending_queue = kept_queue

    def save(self, queue_max_age_hours: int = 8) -> None:
        self.prune(queue_max_age_hours=queue_max_age_hours)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen_urls": self.seen_urls,
            "published_stories": self.published_stories,
            "pending_queue": self.pending_queue,
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def filter_unseen(
        self, items: list[NewsItem], fuzzy_threshold: float = 0.72
    ) -> list[NewsItem]:
        unseen: list[NewsItem] = []
        seen_in_batch: set[str] = set()
        recent_titles = [s.get("headline_az", "") for s in self.published_stories] + [
            q.get("headline_az", "") for q in self.pending_queue
        ]

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

        for q in self.pending_queue:
            recent.append(
                {
                    "published_at": q.get("queued_at", ""),
                    "headline_az": q.get("headline_az", ""),
                    "topic_key": q.get("topic_key", ""),
                }
            )

        for story in reversed(self.published_stories):
            try:
                if datetime.fromisoformat(story["published_at"]) >= cutoff:
                    recent.append(story)
            except (KeyError, ValueError):
                continue
            if len(recent) >= limit:
                break
        return recent

    def enqueue_stories(self, stories: list[dict], max_queue_size: int = 12) -> None:
        now = datetime.now(timezone.utc).isoformat()
        existing_keys = {q.get("topic_key") for q in self.pending_queue}

        for story in stories:
            key = story.get("topic_key", "")
            if key and key in existing_keys:
                continue
            item = dict(story)
            item.setdefault("queued_at", now)
            item.setdefault("importance", 6)
            self.pending_queue.append(item)
            if key:
                existing_keys.add(key)

        self.pending_queue.sort(
            key=lambda x: (int(x.get("importance", 5)), x.get("queued_at", "")),
            reverse=True,
        )
        self.pending_queue = self.pending_queue[:max_queue_size]

    def pop_stories_to_publish(
        self, posts_per_run: int = 1, urgent_threshold: int = 9, max_urgent: int = 2
    ) -> list[dict]:
        if not self.pending_queue:
            return []

        selected: list[dict] = []
        remaining: list[dict] = []

        urgent_count = 0
        for item in self.pending_queue:
            imp = int(item.get("importance", 5))
            if len(selected) < posts_per_run:
                selected.append(item)
                if imp >= urgent_threshold:
                    urgent_count += 1
            elif imp >= urgent_threshold and urgent_count < max_urgent:
                selected.append(item)
                urgent_count += 1
            else:
                remaining.append(item)

        self.pending_queue = remaining
        return selected

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
