from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

try:
    import feedparser  # type: ignore
except ImportError:
    feedparser = None

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
}

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp)(?:\?.*)?$", re.IGNORECASE)


@dataclass
class NewsItem:
    id: str
    title: str
    summary: str
    url: str
    source: str
    region: str
    tier: str
    language: str
    published_at: str
    image_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def clean_text(raw: str | None, max_len: int = 450) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = TAG_RE.sub(" ", text)
    text = WHITESPACE_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    clean_query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunparse(
        (
            parsed.scheme.lower() or "https",
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urlencode(clean_query),
            "",
        )
    )


def _valid_image_url(url: str | None) -> str:
    if not url:
        return ""
    u = url.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return ""


def _extract_img_from_html(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    match = IMG_SRC_RE.search(raw_html)
    return _valid_image_url(match.group(1)) if match else ""


def _parse_date_string(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _is_too_old(published_iso: str | None, max_age_hours: int) -> bool:
    if not published_iso or max_age_hours <= 0:
        return False
    try:
        pub_dt = datetime.fromisoformat(published_iso)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        return pub_dt < cutoff
    except ValueError:
        return False


def _parse_xml_fallback(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    entries: list[dict] = []

    def _local(tag: str) -> str:
        return tag.split("}", 1)[-1].lower()

    for item in root.iter():
        if _local(item.tag) == "item":
            title = link = summary = pub = image_url = ""
            for child in item:
                tag = _local(child.tag)
                if tag == "title" and child.text:
                    title = child.text
                elif tag == "link" and child.text:
                    link = child.text
                elif tag in {"description", "summary"} and child.text and not summary:
                    summary = child.text
                elif tag in {"pubdate", "date"} and child.text and not pub:
                    pub = child.text
                elif tag in {"content", "thumbnail"} and not image_url:
                    cand = _valid_image_url(child.attrib.get("url"))
                    if cand:
                        image_url = cand
                elif tag == "enclosure" and not image_url:
                    enc_type = child.attrib.get("type", "").lower()
                    cand = _valid_image_url(child.attrib.get("url"))
                    if cand and (enc_type.startswith("image/") or IMAGE_EXT_RE.search(cand)):
                        image_url = cand
            if not image_url:
                image_url = _extract_img_from_html(summary)
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published_iso": _parse_date_string(pub),
                    "image_url": image_url,
                }
            )

    if entries:
        return entries

    for entry in root.iter():
        if _local(entry.tag) == "entry":
            title = link = summary = pub = image_url = ""
            for child in entry:
                tag = _local(child.tag)
                if tag == "title" and child.text:
                    title = child.text
                elif tag == "link":
                    href = child.attrib.get("href") or (child.text or "")
                    if href and not link:
                        link = href
                elif tag in {"summary", "content"} and child.text and not summary:
                    summary = child.text
                elif tag in {"updated", "published"} and child.text and not pub:
                    pub = child.text
                elif tag in {"thumbnail", "content"} and not image_url:
                    cand = _valid_image_url(child.attrib.get("url"))
                    if cand:
                        image_url = cand
            if not image_url:
                image_url = _extract_img_from_html(summary)
            entries.append(
                {
                    "title": title,
                    "link": link,
                    "summary": summary,
                    "published_iso": _parse_date_string(pub),
                    "image_url": image_url,
                }
            )

    return entries


def _extract_feedparser_image(entry: dict) -> str:
    for media in entry.get("media_content", []) or []:
        url = _valid_image_url(media.get("url"))
        mtype = str(media.get("type", "")).lower()
        medium = str(media.get("medium", "")).lower()
        if url and (mtype.startswith("image/") or medium == "image" or IMAGE_EXT_RE.search(url) or not mtype):
            return url

    for thumb in entry.get("media_thumbnail", []) or []:
        url = _valid_image_url(thumb.get("url"))
        if url:
            return url

    for enc in entry.get("enclosures", []) or []:
        url = _valid_image_url(enc.get("href") or enc.get("url"))
        etype = str(enc.get("type", "")).lower()
        if url and (etype.startswith("image/") or IMAGE_EXT_RE.search(url)):
            return url

    return _extract_img_from_html(entry.get("summary") or entry.get("description"))


def parse_rss_bytes(content: bytes) -> list[dict]:
    if feedparser is not None:
        parsed = feedparser.parse(content)
        entries: list[dict] = []
        for entry in parsed.entries:
            time_struct = entry.get("published_parsed") or entry.get("updated_parsed")
            if time_struct:
                pub_iso: str | None = datetime(
                    *time_struct[:6], tzinfo=timezone.utc
                ).isoformat()
            else:
                pub_iso = None
            entries.append(
                {
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "summary": entry.get("summary") or entry.get("description") or "",
                    "published_iso": pub_iso,
                    "image_url": _extract_feedparser_image(entry),
                }
            )
        return entries
    return _parse_xml_fallback(content)


def load_feeds_config(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_feed_items(
    feed_cfg: dict,
    max_items: int = 10,
    max_age_hours: int = 12,
    timeout: int = 15,
    session: requests.Session | None = None,
) -> list[NewsItem]:
    url = feed_cfg["url"]
    name = feed_cfg["name"]
    region = feed_cfg.get("region", "world")
    tier = feed_cfg.get("tier", "standard")
    language = feed_cfg.get("language", "en")

    http = session or requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AzNewsBot/1.0; +https://github.com/)"
    }

    try:
        resp = http.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        entries = parse_rss_bytes(resp.content)
    except Exception as exc:
        logger.warning("RSS oxunmadı (%s - %s): %s", name, url, exc)
        return []

    items: list[NewsItem] = []
    skipped_old = 0
    for idx, entry in enumerate(entries[:max_items]):
        title = clean_text(entry.get("title"), max_len=240)
        link = canonicalize_url(entry.get("link", ""))
        if not title or not link:
            continue

        raw_pub_iso = entry.get("published_iso")
        if _is_too_old(raw_pub_iso, max_age_hours=max_age_hours):
            skipped_old += 1
            continue

        summary = clean_text(entry.get("summary", ""), max_len=400)
        published_at = raw_pub_iso or datetime.now(timezone.utc).isoformat()
        image_url = _valid_image_url(entry.get("image_url"))

        items.append(
            NewsItem(
                id=f"{name.lower().replace(' ', '_')}_{idx}",
                title=title,
                summary=summary,
                url=link,
                source=name,
                region=region,
                tier=tier,
                language=language,
                published_at=published_at,
                image_url=image_url,
            )
        )

    logger.info(
        "Mənbə: %s (%s) -> %d yeni xəbər oxundu (%d köhnə buraxıldı)",
        name,
        region,
        len(items),
        skipped_old,
    )
    return items


def fetch_all_feeds(
    feeds: Iterable[dict],
    max_items_per_feed: int = 10,
    max_age_hours: int = 12,
    timeout: int = 15,
) -> list[NewsItem]:
    all_items: list[NewsItem] = []
    with requests.Session() as session:
        for feed_cfg in feeds:
            all_items.extend(
                fetch_feed_items(
                    feed_cfg,
                    max_items=max_items_per_feed,
                    max_age_hours=max_age_hours,
                    timeout=timeout,
                    session=session,
                )
            )
    return all_items
