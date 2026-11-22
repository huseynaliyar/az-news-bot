from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import requests

from src.rss_reader import NewsItem

logger = logging.getLogger(__name__)

CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass
class CuratedStory:
    headline_az: str
    summary_az: str
    category: str
    region: str
    verified: bool
    verification_note: str
    is_update: bool
    topic_key: str
    source_indices: list[int]
    sources: list[dict]


def build_prompt(
    candidates: list[NewsItem],
    recent_stories: list[dict],
    max_posts: int,
) -> str:
    candidate_lines = []
    for idx, item in enumerate(candidates):
        candidate_lines.append(
            f"[{idx}] ({item.source} | region={item.region} | tier={item.tier} | lang={item.language})\n"
            f"Başlıq: {item.title}\n"
            f"Məzmun: {item.summary or '-'}"
        )

    history_lines = []
    for s in recent_stories:
        date_str = s.get("published_at", "")[:10]
        history_lines.append(
            f"- [{date_str}] ({s.get('topic_key', '')}) {s.get('headline_az', '')}"
        )

    history_block = "\n".join(history_lines) if history_lines else "(Son günlərdə paylaşılmış xəbər yoxdur)"
    candidates_block = "\n\n".join(candidate_lines)

    return f"""Sən peşəkar azərbaycanlı xəbər redaktoru və fakt-yoxlayıcısısan.
Sənə Azərbaycan, Türkiyə və dünya RSS mənbələrindən gələn yeni xəbərlər verilir.

VƏZİFƏLƏRİN:
1. EYNİ HADİSƏNİ BİRLƏŞDİR: Müxtəlif saytlardan (məsələn, BBC, TRT Haber, Oxu.az və s.) eyni hadisə haqqında gələn xəbərləri BİR xəbər kimi birləşdir və bütün uyğun indeksləri `source_indices` siyahısına yaz.
2. ÇOXGÜNLÜK YADDAŞI YOXLAYIB TƏKRARI ÇIXAR: Aşağıdakı "SON GÜNLƏRDƏ PAYLAŞILMIŞ XƏBƏRLƏR" siyahısını yoxla. Əgər yeni xəbər son günlərdə artıq paylaşılıbsa və ciddi yeni inkişaf yoxdursa, onu TAMAMİLƏ ÇIXAR. Yalnız mühüm yeni detal/inkişaf varsa `is_update: true` kimi saxla.
3. DOĞRULUĞU QİYMƏTLƏNDİR:
   - Əgər xəbər 2 və ya daha çox müstəqil mənbə tərəfindən təsdiqlənirsə və ya rəsmi/etibarlı (tier=reliable) mənbədən rəsmi bəyanatdırsa: `verified: true`, `verification_note: "Təsdiqlənmiş"` yaz.
   - Əgər yalnız 1 mənbədə varsa və hələ təsdiqlənməyibsə / iddia xarakterlidirsə: `verified: false`, `verification_note: "Tək mənbə / Yoxlanılmalıdır"` yaz.
4. AZƏRBAYCAN DİLİNDƏ YAZ: Ən vacib maksimum {max_posts} xəbəri seç (Azərbaycan, Türkiyə və dünya balansını qoru). Hər xəbər üçün təbii, səlis və qərəzsiz Azərbaycan dilində başlıq (`headline_az`) və 2-3 cümləlik konkret xülasə (`summary_az`) yaz. Reklam, klikbeyt və əhəmiyyətsiz şou xəbərlərini çıxar.

SON GÜNLƏRDƏ PAYLAŞILMIŞ XƏBƏRLƏR:
{history_block}

YENİ NAMİZƏD XƏBƏRLƏR:
{candidates_block}

CAVABI YALNIZ DÜZGÜN JSON FORMATINDA VER (əlavə izah yazma):
{{
  "stories": [
    {{
      "headline_az": "Qısa və aydın Azərbaycan dilində başlıq",
      "summary_az": "2-3 cümləlik dəqiq və neytral xülasə.",
      "category": "Siyasət | İqtisadiyyat | Texnologiya | Dünya | Türkiyə | Azərbaycan | Təhlükəsizlik",
      "region": "az | tr | world",
      "verified": true,
      "verification_note": "Təsdiqlənmiş",
      "is_update": false,
      "topic_key": "qisa-ingilisce-movzu-acari",
      "source_indices": [0, 3]
    }}
  ]
}}"""


def _extract_json(raw_text: str) -> dict:
    cleaned = CODE_FENCE_RE.sub("", raw_text.strip()).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def _call_gemini(prompt: str, api_key: str, model: str, timeout: int = 45) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(prompt: str, api_key: str, model: str, timeout: int = 45) -> str:
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a precise Azerbaijani news editor. Always respond with valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def curate_news(
    candidates: list[NewsItem],
    recent_stories: list[dict],
    max_posts: int = 5,
    primary_provider: str | None = None,
) -> list[CuratedStory]:
    if not candidates:
        return []

    # Keep prompt compact for free-tier token limits
    batch = candidates[:40]
    prompt = build_prompt(batch, recent_stories, max_posts=max_posts)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    preferred = (primary_provider or os.getenv("AI_PROVIDER", "gemini")).strip().lower()

    providers: list[tuple[str, str, str]] = []
    if preferred == "groq":
        if groq_key:
            providers.append(("groq", groq_key, groq_model))
        if gemini_key:
            providers.append(("gemini", gemini_key, gemini_model))
    else:
        if gemini_key:
            providers.append(("gemini", gemini_key, gemini_model))
        if groq_key:
            providers.append(("groq", groq_key, groq_model))

    if not providers:
        raise RuntimeError(
            "Heç bir AI API açarı tapılmadı. GEMINI_API_KEY və ya GROQ_API_KEY təyin edin."
        )

    raw_response: str | None = None
    last_error: Exception | None = None
    for name, key, model in providers:
        try:
            logger.info("AI təhlili işə salınır: %s (%s)", name, model)
            if name == "gemini":
                raw_response = _call_gemini(prompt, key, model)
            else:
                raw_response = _call_groq(prompt, key, model)
            break
        except Exception as exc:
            logger.warning("%s xətası, növbəti provayderə keçilir: %s", name, exc)
            last_error = exc

    if raw_response is None:
        raise RuntimeError(f"Bütün AI provayderləri uğursuz oldu: {last_error}")

    parsed = _extract_json(raw_response)
    stories: list[CuratedStory] = []

    for item in parsed.get("stories", [])[:max_posts]:
        indices = [
            i for i in item.get("source_indices", [])
            if isinstance(i, int) and 0 <= i < len(batch)
        ]
        if not indices:
            continue

        seen_names: set[str] = set()
        sources: list[dict] = []
        for idx in indices:
            cand = batch[idx]
            if cand.source not in seen_names:
                seen_names.add(cand.source)
                sources.append({"name": cand.source, "url": cand.url})

        stories.append(
            CuratedStory(
                headline_az=str(item.get("headline_az", "")).strip(),
                summary_az=str(item.get("summary_az", "")).strip(),
                category=str(item.get("category", "Xəbər")).strip(),
                region=str(item.get("region", "world")).strip().lower(),
                verified=bool(item.get("verified", False)),
                verification_note=str(
                    item.get("verification_note", "Tək mənbə")
                ).strip(),
                is_update=bool(item.get("is_update", False)),
                topic_key=str(item.get("topic_key", "news")).strip(),
                source_indices=indices,
                sources=sources,
            )
        )

    return stories
