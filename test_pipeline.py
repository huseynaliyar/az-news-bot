import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.ai_processor import curate_news
from src.memory import NewsMemory
from src.rss_reader import NewsItem, canonicalize_url, clean_text
from src.telegram_poster import format_telegram_message, publish_stories


class TestNewsBotPipeline(unittest.TestCase):
    def test_url_canonicalization_and_text_cleanup(self):
        url = "https://www.TRTHaber.com/haber/123/?utm_source=twitter&id=5&fbclid=abc"
        self.assertEqual(
            canonicalize_url(url),
            "https://www.trthaber.com/haber/123?id=5",
        )
        raw = "<p>Bakı &amp; <b>Ankara</b> arasında   yeni saziş.</p>"
        self.assertEqual(clean_text(raw), "Bakı & Ankara arasında yeni saziş.")

    def test_multiday_memory_deduplication(self):
        with tempfile.TemporaryDirectory() as tmp:
            hist_path = Path(tmp) / "history.json"
            mem = NewsMemory(hist_path, retention_days=14)
            mem.mark_urls_seen(["https://oxu.az/news/1"])
            mem.record_published_story(
                headline_az="Bakı və Ankara arasında yeni enerji müqaviləsi imzalandı",
                summary_az="Azərbaycan və Türkiyə yeni enerji layihəsində razılaşdı.",
                topic_key="az-tr-energy-deal",
                sources=["Oxu.az", "TRT Haber"],
            )
            mem.save()

            reloaded = NewsMemory(hist_path, retention_days=14)
            items = [
                NewsItem(
                    id="1",
                    title="Duplicate URL item",
                    summary="...",
                    url="https://oxu.az/news/1",
                    source="Oxu.az",
                    region="az",
                    tier="reliable",
                    language="az",
                    published_at="2026-11-22T18:00:00+00:00",
                ),
                NewsItem(
                    id="2",
                    title="Bakı və Ankara arasında yeni enerji müqaviləsi imzalandı!",
                    summary="...",
                    url="https://news.day.az/999",
                    source="Day.az",
                    region="az",
                    tier="standard",
                    language="az",
                    published_at="2026-11-22T18:05:00+00:00",
                ),
                NewsItem(
                    id="3",
                    title="Avropa Mərkəzi Bankı faiz qərarını açıqladı",
                    summary="ECB faiz dərəcəsini sabit saxladı.",
                    url="https://feeds.bbci.co.uk/news/world-123",
                    source="BBC World",
                    region="world",
                    tier="reliable",
                    language="en",
                    published_at="2026-11-22T18:10:00+00:00",
                ),
            ]
            unseen = reloaded.filter_unseen(items)
            self.assertEqual(len(unseen), 1)
            self.assertEqual(unseen[0].source, "BBC World")

    @patch("src.ai_processor._call_groq")
    @patch("src.ai_processor._call_gemini")
    def test_ai_fallback_and_telegram_formatting(self, mock_gemini, mock_groq):
        # Simulate Gemini 429 error -> automatic fallback to Groq
        mock_gemini.side_effect = RuntimeError("429 Too Many Requests")
        mock_groq.return_value = json.dumps(
            {
                "stories": [
                    {
                        "headline_az": "Türkiyə və Azərbaycan arasında yeni nəqliyyat dəhlizi razılaşması",
                        "summary_az": "Bakı və Ankara regionda yükdaşımaları sürətləndirəcək yeni saziş imzalayıb. Layihə 2027-ci ildə tam istifadəyə veriləcək.",
                        "category": "İqtisadiyyat",
                        "region": "tr",
                        "verified": True,
                        "verification_note": "Təsdiqlənmiş",
                        "is_update": False,
                        "topic_key": "tr-az-transport-corridor",
                        "source_indices": [0, 1],
                    }
                ]
            },
            ensure_ascii=False,
        )

        candidates = [
            NewsItem(
                id="a",
                title="Türkiye ve Azerbaycan arasında yeni ulaşım koridoru anlaşması",
                summary="TRT Haber məlumatı",
                url="https://www.trthaber.com/haber/1",
                source="TRT Haber",
                region="tr",
                tier="reliable",
                language="tr",
                published_at="2026-11-22T18:00:00+00:00",
            ),
            NewsItem(
                id="b",
                title="Azərbaycan və Türkiyə yeni nəqliyyat sazişi imzaladı",
                summary="Oxu.az məlumatı",
                url="https://oxu.az/haber/2",
                source="Oxu.az",
                region="az",
                tier="reliable",
                language="az",
                published_at="2026-11-22T18:02:00+00:00",
            ),
        ]

        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "fake-gem", "GROQ_API_KEY": "fake-groq"},
        ):
            stories = curate_news(candidates, recent_stories=[], max_posts=5)

        self.assertEqual(len(stories), 1)
        self.assertTrue(stories[0].verified)
        self.assertEqual(len(stories[0].sources), 2)

        msg = format_telegram_message(stories[0])
        self.assertIn("🇹🇷", msg)
        self.assertIn("✅ Təsdiqlənmiş", msg)
        self.assertIn("TRT Haber", msg)
        self.assertIn("Oxu.az", msg)

        sent = publish_stories(stories, bot_token="x", chat_id="@test", dry_run=True)
        self.assertEqual(sent, 1)


if __name__ == "__main__":
    unittest.main()
