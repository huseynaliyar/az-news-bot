from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass

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
    importance: int = 6
    image_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CuratedStory":
        return cls(
            headline_az=str(data.get("headline_az", "")),
            summary_az=str(data.get("summary_az", "")),
            category=str(data.get("category", "Xəbər")),
            region=str(data.get("region", "world")),
            verified=bool(data.get("verified", False)),
            verification_note=str(data.get("verification_note", "Tək mənbə")),
            is_update=bool(data.get("is_update", False)),
            topic_key=str(data.get("topic_key", "news")),
            source_indices=list(data.get("source_indices", [])),
            sources=list(data.get("sources", [])),
            importance=int(data.get("importance", 6)),
            image_url=str(data.get("image_url", "")),
        )


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

    return f"""Sən müasir, zövqlü və oxucuların sevdiyi Azərbaycan Telegram xəbər kanalının baş redaktorusan.
Sənə Azərbaycan, Türkiyə, dünya, texnologiya, elm və biznes RSS mənbələrindən xəbərlər gəlir.

VƏZİFƏLƏRİN:
1. EYNİ HADİSƏNİ BİRLƏŞDİR: Fərqli saytlardan eyni hadisə haqqında gələn xəbərləri BİR xəbər kimi birləşdir və bütün uyğun indeksləri `source_indices` siyahısına yaz.
2. TƏKRARI ÇIXAR: Aşağıdakı "SON GÜNLƏRDƏ PAYLAŞILMIŞ VƏ NÖVBƏDƏ OLAN XƏBƏRLƏR" siyahısında artıq olan mövzuları TAMAMİLƏ ÇIXAR (yalnız ciddi yeni inkişaf varsa `is_update: true` saxla).
3. DOĞRULUĞU VƏ VACİBLİYİ QİYMƏTLƏNDİR:
   - 2+ müstəqil mənbə təsdiqləyirsə və ya etibarlı rəsmi mənbədirsə: `verified: true`, `verification_note: "Təsdiqlənmiş"`.
   - Yalnız 1 mənbədədirsə: `verified: false`, `verification_note: "Tək mənbə / Yoxlanılmalıdır"`.
   - `importance` (1-10): 9-10 = fövqəladə/son dəqiqə böyük hadisə; 7-8 = çox maraqlı və ya mühüm gündəm/texnologiya/elm xəbəri; 5-6 = sıradan xəbər; 1-4 = əhəmiyyətsiz. Yalnız həqiqətən oxucunun diqqətini çəkən, paylaşılmağa dəyər xəbərlərə 7+ bal ver.
4. MÜASİR YAZI ÜSLUBU VƏ MÖVZU BALANSI:
   - Maksimum {max_posts} xəbər seç. Ard-arda təkcə ağır siyasi xəbər yox, maraqlı texnologiya, elm, iqtisadiyyat və qeyri-adi dünya hadisələrini də mütləq qat.
   - Quru, darıxdırıcı rəsmi agentlik dilindən UZAQ DUR ("çərçivəsində müvafiq tədbirlər həyata keçirilib" kimi şablonlar yazma).
   - Başlıq (`headline_az`) qısa, kəskin və maraq oyadan olsun (klikbeyt yox, əsas faktı dərhal verən).
   - Xülasə (`summary_az`) 2-3 cümləlik canlı, axıcı və insan kimi yazılmış Azərbaycan dilində olsun; faciə/ciddi hadisələrdə hörmətli və ciddi, texnologiya/maraqlı mövzularda isə dinamik və yaddaqalan üslub seç.

SON GÜNLƏRDƏ PAYLAŞILMIŞ VƏ NÖVBƏDƏ OLAN XƏBƏRLƏR:
{history_block}

YENİ NAMİZƏD XƏBƏRLƏR:
{candidates_block}

CAVABI YALNIZ DÜZGÜN JSON FORMATINDA VER:
{{
  "stories": [
    {{
      "headline_az": "Qısa, maraq oyadan və aydın başlıq",
      "summary_az": "2-3 cümləlik canlı və axıcı Azərbaycan dilində xülasə.",
      "category": "Siyasət | İqtisadiyyat | Texnologiya | Elm | Dünya | Türkiyə | Azərbaycan | Maraqlı",
      "region": "az | tr | world",
      "verified": true,
      "verification_note": "Təsdiqlənmiş",
      "is_update": false,
      "importance": 8,
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
            "temperature": 0.3,
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
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "You are a sharp, modern Azerbaijani news editor. Always respond with valid JSON only.",
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

    batch = candidates[:40]
    prompt = build_prompt(batch, recent_stories, max_posts=max_posts)

    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    groq_model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    preferred = (primary_provider or os.getenv("AI_PROVIDER", "gemini")).strip().lower()

    gemini_models = list(
        dict.fromkeys([gemini_model, "gemini-3.1-flash-lite", "gemini-3.8-flash"])
    )

    providers: list[tuple[str, str, str]] = []
    if preferred == "groq":
        if groq_key:
            providers.append(("groq", groq_key, groq_model))
        if gemini_key:
            providers.extend(("gemini", gemini_key, m) for m in gemini_models)
    else:
        if gemini_key:
            providers.extend(("gemini", gemini_key, m) for m in gemini_models)
        if groq_key:
            providers.append(("groq", groq_key, groq_model))

    if not providers:
        raise RuntimeError(
            "Heç bir AI API açarı tapılmadı. GEMINI_API_KEY və ya GROQ_API_KEY təyin edin."
        )

    raw_response: str | None = None
    last_error: Exception | None = None
    for name, key, model in providers:
        for attempt in range(2):
            try:
                logger.info("AI təhlili işə salınır: %s (%s)", name, model)
                if name == "gemini":
                    raw_response = _call_gemini(prompt, key, model)
                else:
                    raw_response = _call_groq(prompt, key, model)
                break
            except Exception as exc:
                logger.warning("%s (%s) cəhd %d xətası: %s", name, model, attempt + 1, exc)
                last_error = exc
                if attempt == 0:
                    time.sleep(5)
        if raw_response is not None:
            break

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
        image_url = ""
        for idx in indices:
            cand = batch[idx]
            if not image_url and cand.image_url:
                image_url = cand.image_url
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
                importance=int(item.get("importance", 6)),
                image_url=image_url,
            )
        )

    return stories
